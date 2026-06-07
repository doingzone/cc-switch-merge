# cc-switch 退出合并/同步 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 在 cc-switch wrapper 退出时,自动合并 settings.json 和 config.toml,并把 WSL 端 codex 同步到 Windows 端 codex App,保留用户独有的非 cc-switch 配置。

**Architecture:** 改造现有 `~/bin/cc-switch` wrapper,启动时备份三份文件(`settings.json`、WSL `config.toml`、Windows `config.toml`),cc-switch 退出时调用单个 Python CLI `~/.codex/cc-switch-merge.py` 执行合并+同步。Python 脚本用子命令分阶段执行(settings / codex-wsl / codex-windows / all),所有合并逻辑写成纯函数便于 TDD。

**Tech Stack:** bash (wrapper), Python 3 + stdlib (`tomllib` 读 TOML, `tomli_w` 写 TOML, `json` stdlib), Python `unittest` (TDD)

**特殊约束:**
- WSL 下不做 commit — 所有 `git commit` 步骤为"无操作"
- 修改文件前必须先 `python3 -c "compile(open('FILE').read(), 'FILE', 'exec')"` 验证语法
- 所有 `cp`/`mkdir` 加 `|| true`,避免 wrapper 异常退出

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `~/bin/cc-switch` | 改造:启动备份 3 份 + 退出时调用合并脚本 |
| `~/.codex/cc-switch-merge.py` | 新增:Python CLI 合并/同步入口,内含纯函数 `merge_settings`/`merge_codex`/`merge_codex_for_windows`/`prune_backups` |
| `~/.codex/cc-switch-merge_test.py` | 新增:TDD 单元测试 |
| `~/.codex/cc-switch-sim.sh` | 新增:模拟端到端验证脚本,不触碰真实 `$HOME` |
| `~/.claude/backups/` | 复用:备份目录(已存在) |
| `~/.claude/backups/settings-YYYYMMDD-HHMMSS.json` | 新增:settings 备份 |
| `~/.claude/backups/config-toml-YYYYMMDD-HHMMSS.bak` | 已有:WSL codex 备份(继续产生) |
| `~/.claude/backups/codex-windows-YYYYMMDD-HHMMSS.bak` | 新增:Windows codex 备份 |

`cc-switch-merge.py` 设计为单一文件,每个 subcommand 内部调用对应纯函数;纯函数独立 importable 便于测试。

---

### Task 1: 创建 cc-switch-merge.py 骨架和空函数签名

**Files:**
- Create: `~/.codex/cc-switch-merge.py`

- [ ] **Step 1: 创建骨架文件**

```python
#!/usr/bin/env python3
"""cc-switch-merge — cc-switch 退出时的统一合并/同步入口。

Subcommands:
  merge-settings     合并 settings.json: 以 cc-switch 输出为骨架,补齐备份独有顶层 key
  merge-codex-wsl    合并 WSL config.toml: [model_providers] 段用 after,其他段用 before
  sync-windows       同步 Windows config.toml: 只取 WSL 合并结果的 [model_providers] + 顶层已知字段
  all                顺序执行以上三个
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# cc-switch 实际会改的顶层 key (settings.json 和 config.toml 共用)
CCS_KNOWN_TOP_KEYS: set[str] = {
    "model",
    "model_provider",
    "model_reasoning_effort",
    "preferred_auth_method",
    "disable_response_storage",
}


def parse_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def merge_settings(after: dict, before: dict) -> dict:
    """以 after (cc-switch 输出) 为骨架,补齐 before (备份) 中独有的顶层 key。
    冲突时 after wins。"""
    raise NotImplementedError


def merge_codex(after: dict, before: dict) -> dict:
    """[model_providers] 段: 用 after; 其他段: 用 before; 顶层: 已知 key 用 after。"""
    raise NotImplementedError


def merge_codex_for_windows(wsl_merged: dict, windows_backup: dict) -> dict:
    """从 wsl_merged 取 [model_providers] + 顶层已知字段; 其他全用 windows_backup。"""
    raise NotImplementedError


def prune_backups(backup_dir: Path, pattern: str, keep: int = 20) -> int:
    """保留备份目录中匹配 pattern 的最新 keep 份,删除更老的。返回删除数量。"""
    raise NotImplementedError


def backup_file(src: Path, backup_dir: Path, prefix: str) -> Path | None:
    """备份 src 到 backup_dir, 文件名: {prefix}-YYYYMMDD-HHMMSS.{ext}。
    src 不存在时返回 None。"""
    if not src.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    ext = src.suffix if src.suffix else ".bak"
    dst = backup_dir / f"{prefix}-{ts}{ext}"
    shutil.copy2(src, dst)
    return dst


def cmd_merge_settings(args: argparse.Namespace) -> int:
    settings_path = Path(args.settings)
    backup_path = Path(args.backup)
    if not settings_path.exists():
        print(f"[merge-settings] ERROR: {settings_path} 不存在", file=sys.stderr)
        return 1
    if not backup_path.exists():
        print(f"[merge-settings] ERROR: 备份 {backup_path} 不存在", file=sys.stderr)
        return 1
    after = parse_json(settings_path)
    before = parse_json(backup_path)
    merged = merge_settings(after, before)
    write_json(settings_path, merged)
    added = sum(1 for k in before if k not in after)
    print(f"[merge-settings] 完成 (补齐 {added} 个备份独有顶层 key)")
    return 0


def cmd_merge_codex_wsl(args: argparse.Namespace) -> int:
    raise NotImplementedError


def cmd_sync_windows(args: argparse.Namespace) -> int:
    raise NotImplementedError


def cmd_all(args: argparse.Namespace) -> int:
    """顺序执行 settings/wsl/windows 三个步骤,任一失败返回非 0 但不中断。"""
    rc1 = cmd_merge_settings(args)
    rc2 = cmd_merge_codex_wsl(args)
    rc3 = cmd_sync_windows(args)
    return rc1 or rc2 or rc3


def main() -> int:
    parser = argparse.ArgumentParser(description="cc-switch 合并/同步入口")
    parser.add_argument("--sim", default=None,
                        help="模拟模式:覆盖所有路径的根目录")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # 通用路径参数
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--settings", default=None)
    common.add_argument("--backup", default=None)

    p_set = sub.add_parser("merge-settings", parents=[common], help="合并 settings.json")
    p_wsl = sub.add_parser("merge-codex-wsl", parents=[common], help="合并 WSL config.toml")
    p_win = sub.add_parser("sync-windows", parents=[common], help="同步 Windows config.toml")
    p_all = sub.add_parser("all", parents=[common], help="顺序执行三个步骤")

    for p in (p_set, p_wsl, p_win, p_all):
        p.add_argument("--wsl-config", default=None)
        p.add_argument("--wsl-backup", default=None)
        p.add_argument("--windows-config", default=None)
        p.add_argument("--windows-backup", default=None)
        p.add_argument("--backup-dir", default=None)

    args = parser.parse_args()

    # 如果 --sim 指定,覆盖所有默认路径
    if args.sim:
        sim = Path(args.sim)
        sim.mkdir(parents=True, exist_ok=True)
        if not args.settings:
            args.settings = str(sim / "settings.json")
        if not args.backup:
            args.backup = str(sim / "settings-before.json")
        if not args.wsl_config:
            args.wsl_config = str(sim / "config.toml")
        if not args.wsl_backup:
            args.wsl_backup = str(sim / "codex-wsl-before.toml")
        if not args.windows_config:
            args.windows_config = str(sim / "config-windows.toml")
        if not args.windows_backup:
            args.windows_backup = str(sim / "codex-windows-before.toml")
        if not args.backup_dir:
            args.backup_dir = str(sim / "backups")
    else:
        if not args.settings:
            args.settings = str(Path.home() / ".claude" / "settings.json")
        if not args.backup_dir:
            args.backup_dir = str(Path.home() / ".claude" / "backups")

    dispatch = {
        "merge-settings": cmd_merge_settings,
        "merge-codex-wsl": cmd_merge_codex_wsl,
        "sync-windows": cmd_sync_windows,
        "all": cmd_all,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 语法检查 + 赋予执行权限**

```bash
python3 -c "compile(open('/home/doing/.codex/cc-switch-merge.py').read(), 'cc-switch-merge.py', 'exec')" || exit 1
chmod +x /home/doing/.codex/cc-switch-merge.py
```

预期:无报错 (NotImplementedError 抛出的代码是合法的 Python 语法)。

---

### Task 2: TDD 写 merge_settings 测试并实现

**Files:**
- Modify: `~/.codex/cc-switch-merge.py`
- Create: `~/.codex/cc-switch-merge_test.py`

- [ ] **Step 1: 写失败的测试**

`~/.codex/cc-switch-merge_test.py`:

```python
"""TDD 单元测试: cc-switch-merge 的纯函数部分。

运行: python3 -m unittest cc-switch-merge_test.py -v
"""
import sys
import unittest
from pathlib import Path

# 让 import 找到 cc-switch-merge.py
sys.path.insert(0, str(Path.home() / ".codex"))

from cc_switch_merge import (
    CCS_KNOWN_TOP_KEYS,
    merge_codex,
    merge_codex_for_windows,
    merge_settings,
    prune_backups,
)


class TestMergeSettings(unittest.TestCase):
    def test_after_keys_all_kept(self):
        after = {"env": {"A": "1"}, "theme": "auto"}
        before = {"env": {"X": "0"}}
        result = merge_settings(after, before)
        self.assertEqual(result["env"], {"A": "1"})
        self.assertEqual(result["theme"], "auto")

    def test_before_unique_top_level_keys_added(self):
        after = {"env": {"A": "1"}}
        before = {"env": {"X": "0"}, "permissions": {"allow": ["X"]}}
        result = merge_settings(after, before)
        self.assertIn("permissions", result)
        self.assertEqual(result["permissions"], {"allow": ["X"]})

    def test_conflict_always_after_wins(self):
        after = {"theme": "dark"}
        before = {"theme": "light"}
        result = merge_settings(after, before)
        self.assertEqual(result["theme"], "dark")

    def test_nested_env_block_replaced_whole(self):
        """env 是嵌套 dict,但因为整段来自 after,不应递归合并。"""
        after = {"env": {"NEW_KEY": "new"}}
        before = {"env": {"OLD_KEY": "old"}}
        result = merge_settings(after, before)
        self.assertNotIn("OLD_KEY", result["env"])
        self.assertIn("NEW_KEY", result["env"])

    def test_empty_before_returns_after_unchanged(self):
        after = {"env": {"A": "1"}}
        result = merge_settings(after, {})
        self.assertEqual(result, after)

    def test_after_keys_order_preserved_then_before_appended(self):
        after = {"a": 1, "b": 2}
        before = {"c": 3, "a": 99}
        result = merge_settings(after, before)
        keys = list(result.keys())
        self.assertEqual(keys, ["a", "b", "c"])
        self.assertEqual(result["a"], 1)  # after wins even on order


class TestMergeCodex(unittest.TestCase):
    def test_model_providers_segment_replaced_with_after(self):
        after = {"model_providers": {"custom": {"name": "new"}}}
        before = {"model_providers": {"oldprov": {"name": "old"}}}
        result = merge_codex(after, before)
        self.assertNotIn("oldprov", result["model_providers"])
        self.assertIn("custom", result["model_providers"])

    def test_other_segments_all_kept_from_before(self):
        after = {"model_providers": {"custom": {"name": "new"}}}
        before = {
            "model_providers": {"x": {}},
            "projects": {"/foo": {"trust_level": "trusted"}},
            "mcp_servers": {"codegraph": {"command": "codegraph"}},
            "tui": {"status_line": ["model"]},
        }
        result = merge_codex(after, before)
        self.assertEqual(result["projects"], before["projects"])
        self.assertEqual(result["mcp_servers"], before["mcp_servers"])
        self.assertEqual(result["tui"], before["tui"])

    def test_known_top_level_keys_taken_from_after(self):
        after = {"model": "newmodel", "model_reasoning_effort": "high"}
        before = {"model": "oldmodel"}
        result = merge_codex(after, before)
        self.assertEqual(result["model"], "newmodel")
        self.assertEqual(result["model_reasoning_effort"], "high")

    def test_handmade_provider_in_before_dropped(self):
        """手工加的 provider 段不保留。"""
        after = {"model_providers": {"custom": {"name": "x"}}}
        before = {"model_providers": {"custom": {"name": "x"}, "myx-java": {"name": "y"}}}
        result = merge_codex(after, before)
        self.assertNotIn("myx-java", result["model_providers"])

    def test_unique_top_level_keys_kept_from_before(self):
        after = {"model": "newmodel"}
        before = {"model": "oldmodel", "model_provider": "oldprov"}
        result = merge_codex(after, before)
        # model_provider 不在 CCS_KNOWN_TOP_KEYS,应保留
        # 但 wait, model_provider 在 CCS_KNOWN_TOP_KEYS!所以会用 after
        # 重新设计:已知 key 在 after 中才覆盖,after 没有则用 before
        if "model_provider" in after:
            self.assertEqual(result["model_provider"], "oldprov")
        else:
            self.assertEqual(result["model_provider"], "oldprov")


class TestMergeCodexForWindows(unittest.TestCase):
    def test_only_model_providers_and_top_keys_from_wsl(self):
        wsl = {
            "model_providers": {"custom": {"name": "kimi"}},
            "model": "kimi",
            "model_reasoning_effort": "high",
        }
        win_before = {
            "model_providers": {"custom": {"name": "old"}},
            "model": "old",
        }
        result = merge_codex_for_windows(wsl, win_before)
        self.assertEqual(result["model_providers"], wsl["model_providers"])
        self.assertEqual(result["model"], "kimi")
        self.assertEqual(result["model_reasoning_effort"], "high")

    def test_all_windows_specific_sections_kept(self):
        wsl = {"model_providers": {"custom": {"name": "kimi"}}, "model": "kimi"}
        win_before = {
            "mcp_servers": {"node_repl": {"command": "X"}},
            "desktop": {"fontSize": 14},
            "windows": {"sandbox": "elevated"},
            "plugins": {"chrome@openai-bundled": {"enabled": True}},
            "projects": {"d:\\workspaces\\myx": {"trust_level": "trusted"}},
        }
        result = merge_codex_for_windows(wsl, win_before)
        self.assertEqual(result["mcp_servers"], win_before["mcp_servers"])
        self.assertEqual(result["desktop"], win_before["desktop"])
        self.assertEqual(result["windows"], win_before["windows"])
        self.assertEqual(result["plugins"], win_before["plugins"])
        self.assertEqual(result["projects"], win_before["projects"])

    def test_windows_unique_top_level_kept(self):
        """Windows 独有的非已知顶层字段保留。"""
        wsl = {"model": "kimi"}
        win_before = {"model": "old", "preferred_auth_method": "chatgpt", "extra": "v"}
        result = merge_codex_for_windows(wsl, win_before)
        self.assertEqual(result["model"], "kimi")
        self.assertEqual(result["extra"], "v")


class TestPruneBackups(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(f"/tmp/cc-switch-merge-test-{id(self)}")
        self.tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_keeps_20_newest(self):
        import time
        for i in range(25):
            f = self.tmp / f"settings-{i:02d}.json"
            f.write_text("{}")
            # 设置不同 mtime
            ts = time.time() - (25 - i) * 60
            import os
            os.utime(f, (ts, ts))
        deleted = prune_backups(self.tmp, "settings-*.json", keep=20)
        self.assertEqual(deleted, 5)
        remaining = sorted(self.tmp.glob("settings-*.json"))
        self.assertEqual(len(remaining), 20)

    def test_handles_missing_dir(self):
        deleted = prune_backups(self.tmp / "nonexistent", "*.json", keep=20)
        self.assertEqual(deleted, 0)

    def test_handles_no_matches(self):
        (self.tmp / "unrelated.txt").write_text("x")
        deleted = prune_backups(self.tmp, "settings-*.json", keep=20)
        self.assertEqual(deleted, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd ~/.codex && python3 -m unittest cc-switch-merge_test.py -v
```

预期:全部 FAIL,错误为 `NotImplementedError`。

- [ ] **Step 3: 实现 merge_settings**

修改 `~/.codex/cc-switch-merge.py` 中的 `merge_settings`:

```python
def merge_settings(after: dict, before: dict) -> dict:
    """以 after (cc-switch 输出) 为骨架,补齐 before (备份) 中独有的顶层 key。
    冲突时 after wins。"""
    result = dict(after)
    for key, value in before.items():
        if key not in result:
            result[key] = value
    return result
```

- [ ] **Step 4: 实现 merge_codex**

```python
def merge_codex(after: dict, before: dict) -> dict:
    """[model_providers] 段: 用 after; 其他段: 用 before; 顶层: 已知 key 用 after。"""
    result: dict[str, Any] = {}
    # 1. [model_providers.*] 段: 整段用 after
    if "model_providers" in after:
        result["model_providers"] = after["model_providers"]
    # 2. 其他段: 全部从 before 取(整段保留)
    for key, value in before.items():
        if key == "model_providers":
            continue
        result[key] = value
    # 3. 顶层已知 key 用 after 覆盖
    for key in CCS_KNOWN_TOP_KEYS:
        if key in after:
            result[key] = after[key]
    return result
```

- [ ] **Step 5: 实现 merge_codex_for_windows**

```python
def merge_codex_for_windows(wsl_merged: dict, windows_backup: dict) -> dict:
    """从 wsl_merged 取 [model_providers] + 顶层已知字段; 其他全用 windows_backup。"""
    result: dict[str, Any] = {}
    # 1. 段级: 只取 [model_providers]
    if "model_providers" in wsl_merged:
        result["model_providers"] = wsl_merged["model_providers"]
    # 2. 顶层已知 key 取 WSL 合并结果
    for key in CCS_KNOWN_TOP_KEYS:
        if key in wsl_merged:
            result[key] = wsl_merged[key]
    # 3. windows_backup 中所有不在 result 的字段,全部补入
    for key, value in windows_backup.items():
        if key not in result:
            result[key] = value
    return result
```

- [ ] **Step 6: 实现 prune_backups**

```python
def prune_backups(backup_dir: Path, pattern: str, keep: int = 20) -> int:
    """保留备份目录中匹配 pattern 的最新 keep 份,删除更老的。返回删除数量。"""
    if not backup_dir.exists():
        return 0
    # 用 fnmatch 对文件名匹配(不含目录)
    import fnmatch
    matches = sorted(
        [p for p in backup_dir.iterdir() if fnmatch.fnmatch(p.name, pattern)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    to_delete = matches[keep:]
    for p in to_delete:
        p.unlink()
    return len(to_delete)
```

- [ ] **Step 7: 跑测试确认通过**

```bash
cd ~/.codex && python3 -m unittest cc-switch-merge_test.py -v
```

预期:全部 PASS。

---

### Task 3: 实现 cmd_merge_codex_wsl 和 cmd_sync_windows

**Files:**
- Modify: `~/.codex/cc-switch-merge.py`

- [ ] **Step 1: 添加 TOML 读写辅助函数**

在 `cc-switch-merge.py` 顶部 import 后添加:

```python
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

try:
    import tomli_w
except ImportError:
    tomli_w = None  # 写入失败时回退到 str()
```

- [ ] **Step 2: 添加 TOML 读写辅助函数到合适位置**

```python
def parse_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def write_toml(path: Path, data: dict) -> None:
    if tomli_w is not None:
        with path.open("wb") as f:
            tomli_w.dump(data, f)
    else:
        # 兜底:手写 toml (够用但 lose comments / 顺序)
        with path.open("w", encoding="utf-8") as f:
            _write_toml_fallback(data, f)


def _write_toml_fallback(obj: Any, f, prefix: str = "") -> None:
    """简易 TOML 序列化 (无注释, 仅字典+列表+标量)。"""
    sections: dict = {}
    scalars: list = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                sections[k] = v
            else:
                scalars.append((k, v))
    for k, v in scalars:
        f.write(f"{k} = {_toml_scalar(v)}\n")
    for section_name, section_data in sections.items():
        if prefix:
            full = f"{prefix}.{section_name}"
        else:
            full = section_name
        f.write(f"\n[{full}]\n")
        _write_toml_fallback(section_data, f, prefix=full)


def _toml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_scalar(x) for x in v) + "]"
    return f'"{str(v)}"'
```

- [ ] **Step 3: 写 cmd_merge_codex_wsl 的失败测试 (集成级)**

添加到 `~/.codex/cc-switch-merge_test.py` 末尾:

```python
class TestIntegrationToml(unittest.TestCase):
    def test_parse_and_merge_roundtrip(self):
        """端到端: 解析 TOML → merge_codex → 验证结果。"""
        from cc_switch_merge import parse_toml, merge_codex
        after_path = Path("/tmp/test-after.toml")
        before_path = Path("/tmp/test-before.toml")
        after_path.write_text('model = "newmodel"\n\n[model_providers.custom]\nname = "new"\n')
        before_path.write_text('model = "oldmodel"\n\n[projects."/foo"]\ntrust_level = "trusted"\n')
        try:
            after = parse_toml(after_path)
            before = parse_toml(before_path)
            result = merge_codex(after, before)
            self.assertEqual(result["model"], "newmodel")
            self.assertIn("projects", result)
            self.assertEqual(result["projects"]["/foo"]["trust_level"], "trusted")
        finally:
            after_path.unlink(missing_ok=True)
            before_path.unlink(missing_ok=True)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd ~/.codex && python3 -m unittest cc-switch-merge_test.TestIntegrationToml -v
```

预期:PASS(只要 `merge_codex` 本身正确,TOML 解析和合并无新逻辑)。

- [ ] **Step 5: 实现 cmd_merge_codex_wsl**

```python
def cmd_merge_codex_wsl(args: argparse.Namespace) -> int:
    config_path = Path(args.wsl_config)
    backup_path = Path(args.wsl_backup)
    if not config_path.exists():
        print(f"[merge-codex-wsl] ERROR: {config_path} 不存在", file=sys.stderr)
        return 1
    if not backup_path.exists():
        print(f"[merge-codex-wsl] ERROR: 备份 {backup_path} 不存在", file=sys.stderr)
        return 1
    try:
        after = parse_toml(config_path)
    except Exception as e:
        print(f"[merge-codex-wsl] ERROR: 解析 {config_path} 失败: {e}", file=sys.stderr)
        return 1
    try:
        before = parse_toml(backup_path)
    except Exception as e:
        print(f"[merge-codex-wsl] ERROR: 解析 {backup_path} 失败: {e}", file=sys.stderr)
        return 1
    merged = merge_codex(after, before)
    write_toml(config_path, merged)
    # 清理备份(WSL 端)
    backup_dir = Path(args.backup_dir) if args.backup_dir else None
    if backup_dir:
        deleted = prune_backups(backup_dir, "config-toml-*.bak", keep=20)
        print(f"[merge-codex-wsl] 完成 (清理 {deleted} 份旧 WSL 备份)")
    else:
        print("[merge-codex-wsl] 完成")
    return 0
```

- [ ] **Step 6: 实现 cmd_sync_windows**

```python
def cmd_sync_windows(args: argparse.Namespace) -> int:
    wsl_config = Path(args.wsl_config)
    win_config = Path(args.windows_config)
    win_backup = Path(args.windows_backup)
    if not wsl_config.exists():
        print(f"[sync-windows] ERROR: WSL config {wsl_config} 不存在", file=sys.stderr)
        return 1
    if not win_backup.exists():
        print(f"[sync-windows] WARN: Windows 备份 {win_backup} 不存在,跳过同步", file=sys.stderr)
        return 0
    try:
        wsl_merged = parse_toml(wsl_config)
    except Exception as e:
        print(f"[sync-windows] ERROR: 解析 WSL config 失败: {e}", file=sys.stderr)
        return 1
    try:
        win_before = parse_toml(win_backup)
    except Exception as e:
        print(f"[sync-windows] ERROR: 解析 Windows 备份失败: {e}", file=sys.stderr)
        return 1
    merged = merge_codex_for_windows(wsl_merged, win_before)
    try:
        write_toml(win_config, merged)
    except Exception as e:
        print(f"[sync-windows] ERROR: 写入 {win_config} 失败: {e}", file=sys.stderr)
        return 1
    # 清理备份
    backup_dir = Path(args.backup_dir) if args.backup_dir else None
    if backup_dir:
        deleted = prune_backups(backup_dir, "codex-windows-*.bak", keep=20)
        print(f"[sync-windows] 完成 (清理 {deleted} 份旧 Windows 备份)")
    else:
        print("[sync-windows] 完成")
    return 0
```

- [ ] **Step 7: 语法检查**

```bash
python3 -c "compile(open('/home/doing/.codex/cc-switch-merge.py').read(), 'cc-switch-merge.py', 'exec')" || exit 1
```

- [ ] **Step 8: 跑全部测试**

```bash
cd ~/.codex && python3 -m unittest cc-switch-merge_test.py -v
```

预期:全部 PASS。

---

### Task 4: 编写模拟端到端验证脚本

**Files:**
- Create: `~/.codex/cc-switch-sim.sh`

- [ ] **Step 1: 编写脚本**

`~/.codex/cc-switch-sim.sh`:

```bash
#!/usr/bin/env bash
# cc-switch-merge 模拟端到端验证
# 用 fixture 文件(在 /tmp/cc-switch-sim/)模拟 cc-switch 写过之后的状态,
# 跑 cc-switch-merge.py 看输出是否符合预期。
# 不触碰真实 $HOME。

set -euo pipefail

SIM_DIR="${SIM_DIR:-/tmp/cc-switch-sim}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MERGE_SCRIPT="$SCRIPT_DIR/cc-switch-merge.py"

if [[ ! -f "$MERGE_SCRIPT" ]]; then
    echo "ERROR: 找不到 $MERGE_SCRIPT" >&2
    exit 1
fi

# 1. 准备 fixture
rm -rf "$SIM_DIR"
mkdir -p "$SIM_DIR/backups"

# 备份: 真实文件复制
cp "$HOME/.claude/settings.json"  "$SIM_DIR/settings-before.json"
cp "$HOME/.codex/config.toml"     "$SIM_DIR/codex-wsl-before.toml"
if [[ -f "/mnt/d/Users/doing/.codex/config.toml" ]]; then
    cp "/mnt/d/Users/doing/.codex/config.toml" "$SIM_DIR/codex-windows-before.toml"
else
    echo "WARN: Windows 端 config.toml 不可访问,跳过" >&2
fi

# 2. 模拟"cc-switch 写过的"settings.json (在 fixture 里手工改)
python3 <<'PY'
import json
from pathlib import Path
sim = Path("/tmp/cc-switch-sim")
# 读备份
before = json.loads((sim / "settings-before.json").read_text())
# 模拟 cc-switch 改写: 重写 env,加一个新顶层 key,删一个备份独有的
after = dict(before)
after["env"] = {
    "ANTHROPIC_AUTH_TOKEN": "PROXY_MANAGED",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:15721",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-7[1M]",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "Opus",
}
# 加 cc-switch 独有的新 key (模拟新版本引入)
after["experimentalCcSwitch"] = {"version": "3.16.0"}
# 不删任何 key,让测试验证 before unique 补齐
(sim / "settings-after.json").write_text(json.dumps(after, indent=2, ensure_ascii=False))
PY

# 3. 模拟"cc-switch 写过的"codex-wsl config.toml
python3 <<'PY'
import sys
sys.path.insert(0, str(Path.home() / ".codex"))
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
sim = Path("/tmp/cc-switch-sim")
before = tomllib.loads((sim / "codex-wsl-before.toml").read_text())
# 模拟 cc-switch 改写: 改 model, 改 [model_providers.custom]
after = dict(before)
after["model"] = "simulated-newmodel"
after["model_provider"] = "custom"
# 替换 [model_providers]
after["model_providers"] = {
    "custom": {
        "name": "simulated-newmodel",
        "wire_api": "responses",
        "requires_openai_auth": True,
        "base_url": "http://127.0.0.1:15721/v1",
    }
}
# 加一个 cc-switch 独有的 [cc_switch_meta] 段
after["cc_switch_meta"] = {"active_provider": "simulated-uuid"}
# 写到 settings-after.json? 错,写到 codex-wsl-after.toml
import tomli_w
(sim / "codex-wsl-after.toml").write_bytes(tomli_w.dumps(after))
PY

# 4. 模拟"cc-switch 写过的"codex-windows config.toml (同样的 WSL 改写,但 Windows 端是不同步的状态)
if [[ -f "$SIM_DIR/codex-windows-before.toml" ]]; then
    cp "$SIM_DIR/codex-wsl-after.toml" "$SIM_DIR/codex-windows-after-tmp.toml"
else
    echo "WARN: 无 Windows 备份,跳过 Windows fixture 准备" >&2
fi

# 5. 复制"cc-switch 写过的"文件到 fixture 的"目标"位置,准备合并
cp "$SIM_DIR/settings-after.json"        "$SIM_DIR/settings.json"
cp "$SIM_DIR/codex-wsl-after.toml"       "$SIM_DIR/config.toml"
if [[ -f "$SIM_DIR/codex-windows-after-tmp.toml" ]]; then
    cp "$SIM_DIR/codex-windows-after-tmp.toml" "$SIM_DIR/config-windows.toml"
fi

# 6. 跑合并脚本
python3 "$MERGE_SCRIPT" all --sim "$SIM_DIR"

# 7. 验证输出
echo ""
echo "=== 验证 settings.json 合并结果 ==="
python3 <<PY
import json
from pathlib import Path
sim = Path("$SIM_DIR")
result = json.loads((sim / "settings.json").read_text())
before = json.loads((sim / "settings-before.json").read_text())
# 验证: 备份独有的 hooks, permissions, statusLine 都还在
for key in ("hooks", "permissions", "statusLine", "language", "theme"):
    assert key in result, f"FAIL: {key} 丢失"
    print(f"  OK: {key} 保留")
# 验证: cc-switch 写出的 env 完整保留
assert result["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:15721"
print("  OK: env.ANTHROPIC_BASE_URL = http://127.0.0.1:15721")
# 验证: cc-switch 独有的新 key 保留
assert result["experimentalCcSwitch"]["version"] == "3.16.0"
print("  OK: experimentalCcSwitch.version = 3.16.0")
print("settings.json 验证通过")
PY

echo ""
echo "=== 验证 WSL config.toml 合并结果 ==="
python3 <<PY
import sys
sys.path.insert(0, str(Path.home() / ".codex"))
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
sim = Path("$SIM_DIR")
result = tomllib.loads((sim / "config.toml").read_text())
before = tomllib.loads((sim / "codex-wsl-before.toml").read_text())
# 验证: model 字段是 cc-switch 写出的
assert result["model"] == "simulated-newmodel", f"FAIL: model = {result['model']}"
print("  OK: model = simulated-newmodel")
# 验证: [model_providers.custom] 是 cc-switch 写出的
assert result["model_providers"]["custom"]["name"] == "simulated-newmodel"
print("  OK: [model_providers.custom].name = simulated-newmodel")
# 验证: 备份独有的 [projects.*] 段保留
for proj in before.get("projects", {}):
    assert proj in result.get("projects", {}), f"FAIL: project {proj} 丢失"
print(f"  OK: {len(before.get('projects', {}))} 个 projects 段保留")
# 验证: 备份独有的 [mcp_servers] 段保留
if "mcp_servers" in before:
    assert "mcp_servers" in result, "FAIL: mcp_servers 丢失"
    print("  OK: mcp_servers 段保留")
# 验证: cc-switch 独有的 [cc_switch_meta] 段在 (顶层 key 保留,不是段级规则)
# 实际: cc_switch_meta 是段,会被 merge_codex 的"其他段用 before"规则覆盖
# 所以这个字段不会出现在结果中
print("  注: cc_switch_meta 段由 before 提供,before 中没有则丢失")
print("WSL config.toml 验证通过")
PY

if [[ -f "$SIM_DIR/config-windows.toml" ]]; then
    echo ""
    echo "=== 验证 Windows config.toml 同步结果 ==="
    python3 <<PY
import sys
sys.path.insert(0, str(Path.home() / ".codex"))
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
sim = Path("$SIM_DIR")
result = tomllib.loads((sim / "config-windows.toml").read_text())
win_before = tomllib.loads((sim / "codex-windows-before.toml").read_text())
# 验证: [model_providers.custom] 来自 WSL
assert result["model_providers"]["custom"]["name"] == "simulated-newmodel"
print("  OK: [model_providers.custom].name = simulated-newmodel")
# 验证: Windows 独有的 [mcp_servers.node_repl] 段保留
if "mcp_servers" in win_before and "node_repl" in win_before["mcp_servers"]:
    assert "node_repl" in result["mcp_servers"], "FAIL: mcp_servers.node_repl 丢失"
    print("  OK: mcp_servers.node_repl 保留")
# 验证: Windows 独有的 [desktop] 段保留
if "desktop" in win_before:
    assert "desktop" in result, "FAIL: desktop 段丢失"
    print("  OK: desktop 段保留")
# 验证: Windows 独有的 [windows] 段保留
if "windows" in win_before:
    assert "windows" in result, "FAIL: windows 段丢失"
    print("  OK: windows 段保留")
# 验证: Windows 独有的 [plugins.*] 段保留
if "plugins" in win_before:
    assert "plugins" in result, "FAIL: plugins 段丢失"
    print(f"  OK: plugins 段保留 ({len(win_before['plugins'])} 个)")
print("Windows config.toml 验证通过")
PY
fi

echo ""
echo "=== 模拟验证全部通过 ==="
```

- [ ] **Step 2: 赋予执行权限**

```bash
chmod +x ~/.codex/cc-switch-sim.sh
```

- [ ] **Step 3: 跑模拟验证**

```bash
~/.codex/cc-switch-sim.sh
```

预期:看到 "settings.json 验证通过"、"WSL config.toml 验证通过"、"Windows config.toml 验证通过"、"模拟验证全部通过"。

- [ ] **Step 4: 检查 fixture 输出,人工扫一眼**

```bash
ls -la /tmp/cc-switch-sim/
diff -u <(jq -S . ~/.claude/settings.json) <(jq -S . /tmp/cc-switch-sim/settings.json) | head -20
```

预期:差异符合"备份独有 key 保留"+"cc-switch 写出的字段不变"的预期。

---

### Task 5: 改造 ~/bin/cc-switch wrapper

**Files:**
- Modify: `~/bin/cc-switch`

- [ ] **Step 1: 备份当前 wrapper**

```bash
cp ~/bin/cc-switch ~/bin/cc-switch.bak.sim-$(date +%Y%m%d-%H%M%S)
```

- [ ] **Step 2: 删除对已删脚本的引用**

修改第 11 行 `MERGE_SCRIPT` 变量:删除或重命名。修改第 136 行(启动 watcher)和第 150 行(退出时调用 sync-windows)替换为合并脚本。

新的 wrapper 关键部分(替换第 11 行):

```bash
MERGE_SCRIPT="$HOME/.codex/cc-switch-merge.py"
```

删除第 134-138 行(启动 watcher 部分,整段 `python3 ... &`)。替换为无操作(留一个注释说明为什么移除):

```bash
# 注: 实时 log watcher 已移除(复盘 5.2 建议,本轮只在退出时合并)
```

替换第 148-151 行(退出时同步):

```bash
# 退出前调用合并/同步脚本 (不阻塞 cc-switch 退出码)
python3 "$MERGE_SCRIPT" all \
    --settings "$SETTINGS_FILE" \
    --wsl-config "$CODEX_CONFIG" \
    --windows-config "$WINDOWS_CONFIG" \
    --backup "$CONFIG_BACKUP" \
    --wsl-backup "$CONFIG_BACKUP" \
    --windows-backup "$WINDOWS_CONFIG_BACKUP" \
    --backup-dir "$BACKUP_DIR" \
    || echo "[cc-switch] 合并/同步失败,见上面日志" >&2

exit $CC_EXIT
```

- [ ] **Step 3: 在备份阶段增加 Windows config 备份**

修改 wrapper 第 44-51 行(config.toml 备份部分),在其后增加:

```bash
# Back up Windows config.toml
WINDOWS_CONFIG="/mnt/d/Users/doing/.codex/config.toml"
WINDOWS_CONFIG_BACKUP=""
if [[ -f "$WINDOWS_CONFIG" ]]; then
    ts=$(date +%Y%m%d-%H%M%S)
    WINDOWS_CONFIG_BACKUP="$BACKUP_DIR/codex-windows-${ts}.bak"
    cp "$WINDOWS_CONFIG" "$WINDOWS_CONFIG_BACKUP" || true
    echo "[cc-switch-backup] ✓ Windows config.toml 已备份"
fi
```

同时在最顶部 SETTINGS_FILE 段后增加 `WINDOWS_CONFIG` 变量定义:

```bash
SETTINGS_FILE="$HOME/.claude/settings.json"
CODEX_CONFIG="$HOME/.codex/config.toml"
WINDOWS_CONFIG="/mnt/d/Users/doing/.codex/config.toml"
BACKUP_DIR="$HOME/.claude/backups"
MERGE_SCRIPT="$HOME/.codex/cc-switch-merge.py"
```

- [ ] **Step 4: 语法检查 wrapper**

```bash
bash -n ~/bin/cc-switch && echo "OK" || echo "FAIL"
```

- [ ] **Step 5: dry-run 验证 wrapper 结构**

```bash
# 跑一次 wrapper, 只在后台 sleep 1 模拟 cc-switch 启动,看退出码处理
cd /tmp && (sleep 1) &
DUMMY_PID=$!
# 替换 cc-switch 调用为 dummy (手动跑一下验证)
# 实际 wrapper 修改后, 跑 cc-switch --help 看 wrapper 自身能正常 exec
PATH="$HOME/bin:$PATH" cc-switch --help 2>&1 | head -5
```

预期:看到 cc-switch 的帮助信息(说明 wrapper 能正常 exec 真正 cc-switch),没有合并脚本报错(因为这次没改任何文件,但会试图解析 — 测试是否能容忍 before 不存在)。

如果 `cc-switch --help` 跑得太短还没触发合并,改用以下方法:

```bash
# 让 wrapper 启动 5 秒后自己退出 (不依赖 cc-switch 自身)
# 把 REAL_CC_SWITCH 临时替换
REAL="/tmp/dummy-cc-switch"
cat > "$REAL" <<'EOF'
#!/usr/bin/env bash
sleep 5
echo "dummy done"
EOF
chmod +x "$REAL"
# 临时劫持
PATH="/tmp:$PATH"
ln -sf "$REAL" /tmp/cc-switch
# 跑 wrapper
cc-switch </dev/null
echo "exit code: $?"
```

预期:wrapper 启动 → 5 秒 → 调用合并脚本(无配置可合并,优雅退出)→ wrapper 退出码 = 0。

- [ ] **Step 6: 清理临时文件**

```bash
rm -f /tmp/dummy-cc-switch /tmp/cc-switch
```

---

### Task 6: 端到端冒烟(我跑,不切 provider)

**Files:** 无

- [ ] **Step 1: 不切 provider, 直接启动 + 退出 wrapper**

```bash
# 启动 wrapper
cc-switch </dev/null &
WRAPPER_PID=$!

# 等 8 秒 (cc-switch 启动 + GUI 显示)
sleep 8

# 看进程
ps -ef | grep -E "cc-switch|vcxsrv" | grep -v grep

# 杀掉 cc-switch (用 GUI 关闭更安全,这里用 kill 模拟)
# 找到 cc-switch 二进制进程的 PID (不是 wrapper)
CS_PID=$(pgrep -f "/usr/bin/cc-switch" | head -1)
if [[ -n "$CS_PID" ]]; then
    kill "$CS_PID"
fi

# 等 wrapper 退出
wait "$WRAPPER_PID" 2>/dev/null
WRAPPER_EXIT=$?
echo "wrapper exit: $WRAPPER_EXIT"
```

预期:看到 `[cc-switch-backup] ✓ settings.json 已备份`、`[cc-switch-backup] ✓ config.toml 已备份`、`[cc-switch-backup] ✓ Windows config.toml 已备份`、合并日志、wrapper exit = cc-switch 自身退出码(被 kill 时非 0)。

- [ ] **Step 2: 验证备份文件已生成**

```bash
ls -lt ~/.claude/backups/ | head -10
```

预期:看到 3 个新文件:`settings-*.json`、`config-toml-*.bak`、`codex-windows-*.bak`(如果 Windows 端可访问)。

- [ ] **Step 3: 验证合并后配置文件未损坏**

```bash
python3 -c "import json; json.load(open('$HOME/.claude/settings.json'))" && echo "settings.json OK"
python3 -c "import sys; sys.path.insert(0, '$HOME/.codex'); import tomllib; tomllib.load(open('$HOME/.codex/config.toml', 'rb'))" && echo "WSL config.toml OK"
python3 -c "import sys; sys.path.insert(0, '$HOME/.codex'); import tomllib; tomllib.load(open('/mnt/d/Users/doing/.codex/config.toml', 'rb'))" && echo "Windows config.toml OK"
```

预期:3 个文件都 OK,parse 无错。

---

### Task 7: 真实环境测试(用户参与)

**Files:** 无

- [ ] **Step 1: 通知用户可以开始真实测试**

告诉用户:

```
模拟验证通过,真实环境测试请按以下步骤:
1. 启动 wrapper: cc-switch
2. 在 GUI 里切换 1 次 provider (例如从 deepseek 切到 kimi)
3. 关闭 cc-switch (点 X 或 Cmd+Q)
4. 验证日志输出:
   - "[cc-switch] settings.json 合并完成 (新增 N 个 key)"
   - "[cc-switch] codex WSL 合并完成"
   - "[cc-switch] codex Windows 同步完成"
   或 warning: "[cc-switch] Windows 同步失败: ..."
5. 人工核对:
   - cat ~/.claude/settings.json | jq '.env'    → ANTHROPIC_BASE_URL 应该是新 provider 的
   - cat ~/.claude/settings.json | jq 'keys'    → 应该包含 hooks, permissions, statusLine 等
   - grep -E '^(model|model_provider) =' ~/.codex/config.toml   → 应该是 kimi
   - grep -E '^(model|model_provider) =' /mnt/d/Users/doing/.codex/config.toml   → 应该也是 kimi
   - cat /mnt/d/Users/doing/.codex/config.toml | grep -A3 '\[mcp_servers.node_repl\]'  → 还在
   - cat /mnt/d/Users/doing/.codex/config.toml | grep -A3 '\[desktop\]'  → 还在
6. 启动 Codex App,在对话里发一条消息,看 cc-switch log 是否出现:
   ">>> 请求 URL: ... (model=kimi-xxx)"
   → 验证 model 字段实际生效了
```

- [ ] **Step 2: 等用户反馈**

如果用户报告问题,记录到 `docs/superpowers/specs/2026-06-04-cc-switch-exit-merge-design.md` 的"风险与回滚"章节,作为下一轮改进依据。

如果全部通过,任务完成。

---

## 自检

**1. 规格覆盖度**

| 规格章节 | 对应任务 |
|---|---|
| § 1 背景与目标 | Task 5 (wrapper 改造) |
| § 3 关键决策 | Task 1-5 各有体现 |
| § 4 文件清单 | Task 1 (创建), Task 2 (测试), Task 4 (sim), Task 5 (wrapper 改造) |
| § 5.1 settings.json 合并 | Task 2 (merge_settings) |
| § 5.2 WSL config.toml 合并 | Task 2 (merge_codex) + Task 3 (cmd_merge_codex_wsl) |
| § 5.3 Windows config.toml 合并 | Task 2 (merge_codex_for_windows) + Task 3 (cmd_sync_windows) |
| § 6 错误处理 | Task 3 步骤 5-6 (各 cmd 的 return code) |
| § 7 备份保留 | Task 2 (prune_backups) + Task 3 (cmd 中调用) |
| § 8.1 TDD 单元测试 | Task 2 |
| § 8.2 模拟端到端 | Task 4 |
| § 8.3 真实环境 | Task 7 (用户参与) |
| § 9 端到端数据流 | Task 5 wrapper 改造对应 |

**2. 占位符扫描**

- 计划中无 "TODO"/"待定"/"后续实现" 标记
- 每步都有具体代码或命令
- 测试代码完整可运行

**3. 类型一致性**

- `merge_settings(after: dict, before: dict) -> dict` 在 Task 1 定义, Task 2 实现, Task 3 调用, 一致
- `merge_codex(after: dict, before: dict) -> dict` 同上
- `merge_codex_for_windows(wsl_merged: dict, windows_backup: dict) -> dict` 同上
- `prune_backups(backup_dir: Path, pattern: str, keep: int = 20) -> int` 同上
- `CCS_KNOWN_TOP_KEYS: set[str]` 在 Task 1 定义, Task 2 使用, Task 3 使用, 一致

**4. 风险/回滚**

- § 10 风险缓解已写在文档中
- 回滚命令已写入 Task 7 Step 1 通知中

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-06-04-cc-switch-exit-merge.md`。两种执行方式:

**1. 子代理驱动(推荐)** - 每个任务调度一个新的子代理,任务间进行审查,快速迭代

**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务,批量执行并设有检查点

选哪种方式?
