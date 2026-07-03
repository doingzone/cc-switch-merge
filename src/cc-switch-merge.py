#!/usr/bin/env python3
"""cc-switch-merge — cc-switch exit merge/sync CLI.

Handles merging settings.json and config.toml changes back after a
cc-switch session, with backup support and optional sim mode.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

try:
    import tomli_w
except ImportError:
    tomli_w = None  # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CCS_KNOWN_TOP_KEYS: set[str] = {
    "model",
    "model_provider",
    "model_reasoning_effort",
    "preferred_auth_method",
    "disable_response_storage",
}

# settings.json 中的关键用户配置字段 — 缺失时认为是被 cc-switch 接管简化,
# 需要从最近完整备份补全, 否则用户的 plugins/permissions/MCP 会在合并后"消失"。
CCS_SETTINGS_PRESERVE_KEYS: tuple[str, ...] = (
    "enabledPlugins",
    "permissions",
    "hooks",
    "mcpServers",
    "extraKnownMarketplaces",
    "autoCompactEnabled",
    "context",
    "language",
    "skipDangerousModePermissionPrompt",
    "statusLine",
    "theme",
    "verbose",
)

# codex config.toml 里的结构性用户字段。cc-switch 接管只写 model/model_providers,
# 会清空这些段。两类:
# - REGISTRY 段 (named-entry dict): projects/mcp_servers/marketplaces — 用 UNION 合并
# - TABLE 段 (单配置块): tui/features/memories/hooks/notice — 缺失则整段恢复, 有则 union key
CCS_CODEX_REGISTRY_SECTIONS: tuple[str, ...] = ("projects", "mcp_servers", "marketplaces")
CCS_CODEX_TABLE_SECTIONS: tuple[str, ...] = ("tui", "features", "memories", "hooks", "notice")

# cc-switch 代理地址 (本地 HTTP 代理, 所有 claude/codex 请求都走这个)
# 如果 settings.json 里的 ANTHROPIC_BASE_URL 是直连 API URL (如 https://api.minimaxi.com/anthropic),
# 会被自动改回代理 URL, 避免 Claude Code 绕过 cc-switch 直接调上游 API。
CCS_PROXY_BASE_URL = "http://127.0.0.1:15721"

# Claude settings.json 的 env 里, 这些字段归 cc-switch/provider 切换流程管理。
# 其他 env 字段默认归用户稳定配置, 从 truth(before/richest backup)保留。
CCS_CLAUDE_ENV_OVERRIDE_KEYS: tuple[str, ...] = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME",
    "OPENAI_API_KEY",
    "ZAI_API_KEY",
)


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def parse_json(path: Path) -> dict:
    """Read and parse a JSON file, returning an empty dict if missing."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    """Write *data* to *path* as pretty-printed JSON (trailing newline)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def backup_file(src: Path, backup_dir: Path, prefix: str) -> Path | None:
    """Copy *src* into *backup_dir* with a timestamped name.

    Returns the backup path, or ``None`` if *src* does not exist.
    Format: ``{prefix}-YYYYMMDD-HHMMSS.{ext}``
    """
    if not src.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    ext = src.suffix.lstrip(".") or "bak"
    dst = backup_dir / f"{prefix}-{ts}.{ext}"
    shutil.copy(src, dst)
    return dst


def parse_toml(path: Path) -> dict:
    """Read and parse a TOML file, returning an empty dict if missing."""
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def write_toml(path: Path, data: dict) -> None:
    """Write *data* to *path* as TOML.

    Uses tomli_w if available; falls back to a simple hand-rolled writer
    (sufficient for our flat-with-sections shape: scalars + nested dicts).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if tomli_w is not None:
        with path.open("wb") as f:
            tomli_w.dump(data, f)
    else:
        with path.open("w", encoding="utf-8") as f:
            _write_toml_fallback(data, f)


def _write_toml_fallback(obj: dict, f, prefix: str = "") -> None:
    """Hand-rolled TOML writer for flat-with-sections dicts.

    Writes scalars (non-dict) at the current level, then recurses into
    dict-valued keys as [section] / [prefix.section] blocks.
    """
    sections: dict = {}
    scalars: list = []
    for k, v in obj.items():
        if isinstance(v, dict):
            sections[k] = v
        else:
            scalars.append((k, v))
    for k, v in scalars:
        f.write(f"{k} = {_toml_scalar(v)}\n")
    for section_name, section_data in sections.items():
        full = f"{prefix}.{section_name}" if prefix else section_name
        f.write(f"\n[{full}]\n")
        _write_toml_fallback(section_data, f, prefix=full)


def _toml_scalar(v: Any) -> str:
    """Render a Python scalar as a TOML literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_scalar(x) for x in v) + "]"
    return f'"{str(v)}"'


def _get_wsl_ip() -> str | None:
    """获取 WSL 的真实 eth0 IP (给 Windows 端 codex 用)。

    socket.gethostbyname_ex 会返回 /etc/hosts 里的 loopback 映射 (127.0.1.1),
    那个地址 Windows 端访问不到。要用 UDP connect 技巧拿到真实出接口 IP。

    优先 192.168.x.x (Win10 mirrored 模式, Windows 端能直连),
    跳过 127.x.x.x (loopback) 和 169.254.x (link-local)。
    """
    import socket
    try:
        # UDP connect 技巧: 不真发包, 只让内核选路由
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
            return ip
    except Exception:
        pass

    # Fallback: 用 hostname -I 解析
    import subprocess
    try:
        result = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            for ip in result.stdout.split():
                if "." in ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                    return ip
    except Exception:
        pass

    return None


def _rewrite_base_url(old_url: str, _wsl_ip: str) -> str:
    """保留为 no-op。

    之前会把 base_url 中的 127.0.0.1/localhost 替换成 WSL eth0 IP, 假设是
    mirrored 模式 (win11)。在 win10 WSL2 NAT 模式下, 这个替换会让 Windows
    端 codex APP 走 WSL eth0 IP, 但 NAT 对 inbound HTTP 数据包会丢包, 导致
    codex APP 一直"思考"。实测: 127.0.0.1:15721 在 Windows 端 200ms 通,
    192.168.1.15:15721 在 Windows 端 10 秒超时。

    Windows 端 base_url 必须保持 127.0.0.1。
    """
    return old_url


# ---------------------------------------------------------------------------
# Pure merge functions
# ---------------------------------------------------------------------------

def merge_settings(after: dict, before: dict) -> dict:
    """以 after (cc-switch 输出) 为骨架,补齐 before (备份) 中独有的顶层 key 和 env key。

    - 顶层: after wins, before 独有的 key 补进去
    - env 块: deep merge, after wins, before 独有的 env key 补进去
      (避免 cc-switch 接管清空 ANTHROPIC_MODEL 之类的 env key 后, merge 把它丢了)
    """
    result = dict(after)
    for key, value in before.items():
        if key not in result:
            result[key] = value
    # env 块 deep merge: after 没有的 env key 从 before 补
    if isinstance(before.get("env"), dict) and isinstance(result.get("env"), dict):
        merged_env = dict(result["env"])
        for k, v in before["env"].items():
            if k not in merged_env:
                merged_env[k] = v
        result["env"] = merged_env
    elif isinstance(before.get("env"), dict) and "env" not in result:
        # after 没有 env 块, 但 before 有 — 整体搬过来
        result["env"] = dict(before["env"])
    return result


def merge_settings_with_truth(after: dict, truth: dict) -> dict:
    """以 truth(完整骨架)为基底, 只用 after 覆盖 cc-switch-owned env(白名单)。

    truth = before(启动备份)或 richest backup, 必须完整。
    after  = cc-switch 切换后的 settings.json(可能缩水, 主要用它的 env)。

    env 合成语义(修复了旧 merge_settings_with_last_good 的 API_KEY 丢失 bug):
    - truth.env 全部保留作基底(包括白名单字段, 如 ANTHROPIC_API_KEY=PROXY_MANAGED)
    - after.env 中的白名单字段覆盖 truth(当前 provider 真实值, 清 stale)
    - after 没有的白名单字段, 从 truth 兜底(不再丢弃, 保住 API_KEY)
    """
    result = dict(truth)

    # truth.env 全部保留作基底(白名单 + 非白名单)
    merged_env: dict[str, Any] = {}
    if isinstance(truth.get("env"), dict):
        merged_env.update(truth["env"])

    # after.env 的白名单字段覆盖(当前 provider 真实值)
    after_env = after.get("env")
    if isinstance(after_env, dict):
        for key in CCS_CLAUDE_ENV_OVERRIDE_KEYS:
            if key in after_env:
                merged_env[key] = after_env[key]

    # 认证键互斥: AUTH_TOKEN 优先(匹配 Claude Code 语义 + 当前所有 provider 都用 TOKEN)
    # cc-switch 接管会写 API_KEY=PROXY_MANAGED 占位, 必须剔除, 否则与 AUTH_TOKEN 共存触发启动告警
    if merged_env.get("ANTHROPIC_AUTH_TOKEN"):
        merged_env.pop("ANTHROPIC_API_KEY", None)

    if merged_env:
        result["env"] = merged_env
    elif "env" in result:
        result.pop("env", None)

    return result


def merge_codex(after: dict, before: dict) -> dict:
    """[model_providers] 段: 用 after; 其他段: 用 before; 顶层: 已知 key 用 after。"""
    result: dict[str, Any] = {}
    if "model_providers" in after:
        result["model_providers"] = after["model_providers"]
    for key, value in before.items():
        if key == "model_providers":
            continue
        # 已知顶层 key 留给最后一步处理,避免 before 泄漏到 after 没有的字段
        if key in CCS_KNOWN_TOP_KEYS:
            continue
        result[key] = value
    for key in CCS_KNOWN_TOP_KEYS:
        if key in after:
            result[key] = after[key]
    return result


def merge_codex_for_windows(wsl_merged: dict, windows_backup: dict) -> dict:
    """从 wsl_merged 取 [model_providers] + 顶层已知字段; 其他全用 windows_backup。"""
    result: dict[str, Any] = {}
    if "model_providers" in wsl_merged:
        result["model_providers"] = wsl_merged["model_providers"]
    for key in CCS_KNOWN_TOP_KEYS:
        if key in wsl_merged:
            result[key] = wsl_merged[key]
    for key, value in windows_backup.items():
        if key in result:
            continue
        # model_providers 总是来自 wsl, 即便 wsl 没有也不应保留 windows 的旧值
        if key == "model_providers":
            continue
        result[key] = value
    return result


def prune_backups(backup_dir: Path, pattern: str, keep: int = 20) -> int:
    """保留备份目录中匹配 pattern 的最新 keep 份,删除更老的。"""
    if not backup_dir.exists():
        return 0
    matches = sorted(
        [p for p in backup_dir.iterdir() if fnmatch.fnmatch(p.name, pattern)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    to_delete = matches[keep:]
    for p in to_delete:
        p.unlink()
    return len(to_delete)


def _settings_content_score(d: dict) -> int:
    """评估 settings.json 备份的"内容丰富度": 结构性字段项数之和。
    分数越高说明配置越完整, 用于在多个"完整"备份里挑最不降级的一个。"""
    score = 0
    for k in ("enabledPlugins", "hooks", "mcpServers"):
        v = d.get(k)
        if isinstance(v, dict):
            score += len(v)
    perm = d.get("permissions", {})
    if isinstance(perm, dict):
        for arr in perm.values():
            if isinstance(arr, list):
                score += len(arr)
    return score


def find_intact_settings_backup(backup_dir: Path, min_keys: int = 10) -> Path | None:
    """从 backup_dir 里找"内容最丰富"的 settings.json 备份。

    cc-switch 代理接管后写入的 settings.json 只有 env (1-3 key), 不会超过 min_keys。
    多个 >=min_keys 的备份里, 优先选结构性字段 (enabledPlugins/hooks) 项数最多的,
    避免选到"被削减过"的备份导致降级棘轮 (ratchet)。
    """
    if not backup_dir.exists():
        return None
    candidates = []
    for p in backup_dir.iterdir():
        if not (p.name.startswith("settings-") and p.suffix == ".json"):
            continue
        if "before-restore" in p.name:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if len(d) < min_keys:
            continue
        # 至少包含一个关键用户字段才算"完整"
        if not any(k in d for k in CCS_SETTINGS_PRESERVE_KEYS):
            continue
        # 内容丰富度高的优先 (降级棘轮防护); 同分取最近的
        candidates.append((_settings_content_score(d), p.stat().st_mtime, p))
    if not candidates:
        return None
    # 按 (丰富度降序, mtime 降序) 排序
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


def _settings_truth_is_usable(settings: Any) -> bool:
    """判断 settings 是否足够完整, 可以作为 last_good 真相源。"""
    if not isinstance(settings, dict):
        return False
    essential = ("env", "enabledPlugins", "hooks", "permissions")
    if not all(k in settings for k in essential):
        return False
    if len(settings) < 10:
        return False
    return _settings_content_score(settings) >= 5


def _load_intact_settings_backup(backup_dir: Path) -> tuple[Path | None, dict]:
    """读取内容最丰富的 Claude settings 备份；没有可用备份时返回空 dict。"""
    intact_path = find_intact_settings_backup(backup_dir)
    if intact_path is None:
        return None, {}
    try:
        intact_data = parse_json(intact_path)
    except Exception:
        return None, {}
    if not isinstance(intact_data, dict):
        return None, {}
    return intact_path, intact_data


def _select_truth(before: dict, backup_dir: Path) -> tuple[dict | None, str]:
    """选完整骨架: before 完整用 before, 否则用 richest backup。

    返回 (truth_dict, source_label)。两者都不可用时返回 (None, "")。
    新架构核心: 取代旧的 last_good healing。
    """
    if _settings_truth_is_usable(before):
        return before, "before(启动备份)"
    intact_path, intact_data = _load_intact_settings_backup(backup_dir)
    if intact_path is not None and intact_data and _settings_truth_is_usable(intact_data):
        return intact_data, f"richest backup({intact_path.name})"
    return None, ""


def get_provider_model_from_db(provider_id: str, app_type: str = "codex") -> str | None:
    """从 cc-switch DB 查 provider UUID 对应的 model 名。

    cc-switch DB 里的 settings_config 有两种格式:
    - codex: 是 TOML 字符串, model 在 "config" 字段里 (model = "...")
    - claude: 是 JSON, model 直接是 env.ANTHROPIC_MODEL 字段
    """
    import sqlite3
    db_path = Path.home() / ".cc-switch" / "cc-switch.db"
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT settings_config FROM providers WHERE id = ? AND app_type = ?",
            (provider_id, app_type),
        ).fetchone()
        conn.close()
        if not row:
            return None
        sc = json.loads(row[0])
        if app_type == "claude":
            # claude 格式: env.ANTHROPIC_MODEL
            return sc.get("env", {}).get("ANTHROPIC_MODEL")
        # codex 格式: config 是 TOML 字符串
        cfg_str = sc.get("config", "")
        if not cfg_str:
            return None
        import re
        m = re.search(r'^model\s*=\s*"([^"]+)"', cfg_str, re.MULTILINE)
        return m.group(1) if m else None
    except Exception:
        return None


def _codex_content_score(d: dict) -> int:
    """评估 codex config.toml 备份的内容丰富度 (REGISTRY 段项数 + TABLE 段存在性)。
    分数越高配置越完整, 用于挑最不降级的备份做兜底。"""
    score = 0
    for k in CCS_CODEX_REGISTRY_SECTIONS:
        v = d.get(k)
        if isinstance(v, dict):
            score += len(v)
    for k in CCS_CODEX_TABLE_SECTIONS:
        if isinstance(d.get(k), dict):
            score += len(d[k]) * 1
    return score


def find_richest_codex_backup(backup_dir: Path, pattern: str = "config-toml-*.toml") -> Path | None:
    """从 backup_dir 找内容最丰富的 codex config.toml 备份 (降级棘轮防护)。

    cc-switch 接管写的 config.toml 只有 model/model_providers (top=2-6),
    内容丰富度远低于完整备份。按 (丰富度, mtime) 选最丰富的。
    """
    if not backup_dir.exists():
        return None
    candidates = []
    for p in backup_dir.iterdir():
        if not fnmatch.fnmatch(p.name, pattern):
            continue
        try:
            d = parse_toml(p)
        except Exception:
            continue
        if not isinstance(d, dict) or not d:
            continue
        score = _codex_content_score(d)
        if score <= 0:
            continue
        candidates.append((score, p.stat().st_mtime, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


def restore_reduced_codex(current: dict, richest: dict) -> tuple[dict, list[str]]:
    """检测 codex config.toml 结构性段被削减/丢失, 从最丰富备份恢复。

    - REGISTRY 段 (projects/mcp_servers/marketplaces): UNION 合并 (current wins, 补 richest 独有)
    - TABLE 段 (tui/features/memories/hooks/notice): 缺失则整段恢复; 有则 union 缺失的 key
    """
    restored: list[str] = []
    result = dict(current)

    # REGISTRY 段: union 合并
    for k in CCS_CODEX_REGISTRY_SECTIONS:
        rich_val = richest.get(k)
        if not isinstance(rich_val, dict):
            continue
        cur_val = result.get(k)
        if not isinstance(cur_val, dict):
            cur_val = {}
        missing = {key: val for key, val in rich_val.items() if key not in cur_val}
        if missing:
            result[k] = {**cur_val, **missing}
            restored.append(f"{k}(+{len(missing)})")

    # TABLE 段: 缺失则恢复, 有则 union key
    for k in CCS_CODEX_TABLE_SECTIONS:
        rich_val = richest.get(k)
        if not isinstance(rich_val, dict):
            continue
        cur_val = result.get(k)
        if not isinstance(cur_val, dict):
            # 缺失: 整段恢复
            result[k] = dict(rich_val)
            restored.append(f"{k}(restore {len(rich_val)})")
        else:
            # 有: 补 richest 独有的 key
            missing = {key: val for key, val in rich_val.items() if key not in cur_val}
            if missing:
                result[k] = {**cur_val, **missing}
                restored.append(f"{k}(+{len(missing)} keys)")

    return result, restored


def resolve_model_from_hot_switch(log_line: str) -> tuple[str | None, str | None]:
    """从 cc-switch 日志的'热切换'行提取 app_type 和 provider_id。"""
    import re
    m = re.search(r"热切换\s+(\S+)\s+的目标供应商为\s+([a-f0-9-]+)", log_line)
    if not m:
        return None, None
    return m.group(1), m.group(2)  # app_type, provider_id


# ---------------------------------------------------------------------------
# CLI command functions
# ---------------------------------------------------------------------------

def cmd_merge_settings(args) -> int:
    """Merge settings.json: read after + before backup -> merge -> write."""
    settings_path: Path = args.settings
    backup_path: Path | None = args.settings_backup
    backup_dir: Path = args.backup_dir

    if backup_path is None:
        print("[merge-settings] No backup path specified (use --settings-backup or --sim)",
              file=sys.stderr)
        return 1

    after = parse_json(settings_path)
    before = parse_json(backup_path)

    if not after:
        print(f"[merge-settings] No 'after' file found: {settings_path}", file=sys.stderr)
        return 1
    if not before:
        print(f"[merge-settings] No 'before' backup found: {backup_path}", file=sys.stderr)
        return 1

    # 选骨架: before 完整用 before, 否则用 richest backup(取代旧 last_good healing)
    truth, src = _select_truth(before, backup_dir)
    if truth is None:
        print(
            f"[merge-settings] 无可用骨架(before 和 richest backup 都不完整): {backup_dir}",
            file=sys.stderr,
        )
        return 1
    print(f"[merge-settings] 骨架: {src}")

    merged = merge_settings_with_truth(after, truth)

    # 覆盖 env.ANTHROPIC_MODEL (cc-switch 热切换时 claude provider 不一定改 settings.json)
    # 同时覆盖 CCS_KNOWN_TOP_KEYS (model 等顶层 key)
    override = getattr(args, "override_model", None)
    if override:
        # 写 env.ANTHROPIC_MODEL
        if "env" not in merged or not isinstance(merged.get("env"), dict):
            merged["env"] = {}
        if merged["env"].get("ANTHROPIC_MODEL") != override:
            old = merged["env"].get("ANTHROPIC_MODEL", "<missing>")
            print(f"[merge-settings] Override env.ANTHROPIC_MODEL: {old} -> {override}")
            merged["env"]["ANTHROPIC_MODEL"] = override

    # 自动纠正 BASE_URL: 如果是直连 API URL (非 localhost), 改回代理 URL
    # 防护场景: 某次 wrapper 退出后, settings.json 被 cc-switch GUI 或其他进程
    # 写入了 provider 的直连 URL (如 https://api.minimaxi.com/anthropic),
    # Claude Code 会绕过 cc-switch 代理直连上游, 完全脱管。
    env = merged.get("env", {})
    if isinstance(env, dict):
        current_url = env.get("ANTHROPIC_BASE_URL", "")
        if current_url and not current_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            env["ANTHROPIC_BASE_URL"] = CCS_PROXY_BASE_URL
            print(
                f"[merge-settings] 纠正 BASE_URL: {current_url} -> {CCS_PROXY_BASE_URL} "
                f"(直连 API 改回 cc-switch 代理)"
            )

    # (旧 restore_missing_settings_keys / restore_reduced_settings_keys 事后恢复分支已删除:
    #  新架构骨架 before/richest backup 本身完整, 不需要事后补缺/防降级)

    # Backup current file before overwriting
    bak = backup_file(settings_path, backup_dir, prefix="settings")
    if bak:
        print(f"[merge-settings] Backup: {bak}")

    write_json(settings_path, merged)
    print(f"[merge-settings] Merged -> {settings_path}")
    return 0


def cmd_regen_claude(args) -> int:
    """手动重建 settings.json: 用 richest backup 作骨架 + live env 合成。

    不依赖 before(启动备份), 用于 settings.json 损坏/缩水时手动修复。
    live 不可读时纯用骨架。
    """
    settings_path: Path = args.settings
    backup_dir: Path = args.backup_dir

    live = parse_json(settings_path) or {}

    intact_path, intact_data = _load_intact_settings_backup(backup_dir)
    if intact_path is None or not intact_data or not _settings_truth_is_usable(intact_data):
        print(f"[regen-claude] 无可用 richest backup: {backup_dir}", file=sys.stderr)
        return 1
    print(f"[regen-claude] 骨架: richest backup({intact_path.name})")

    merged = merge_settings_with_truth(live, intact_data)

    override = getattr(args, "override_model", None)
    if override:
        if "env" not in merged or not isinstance(merged.get("env"), dict):
            merged["env"] = {}
        if merged["env"].get("ANTHROPIC_MODEL") != override:
            old = merged["env"].get("ANTHROPIC_MODEL", "<missing>")
            print(f"[regen-claude] Override env.ANTHROPIC_MODEL: {old} -> {override}")
            merged["env"]["ANTHROPIC_MODEL"] = override

    env = merged.get("env", {})
    if isinstance(env, dict):
        current_url = env.get("ANTHROPIC_BASE_URL", "")
        if current_url and not current_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            env["ANTHROPIC_BASE_URL"] = CCS_PROXY_BASE_URL
            print(
                f"[regen-claude] 纠正 BASE_URL: {current_url} -> {CCS_PROXY_BASE_URL} "
                f"(直连 API 改回 cc-switch 代理)"
            )

    bak = backup_file(settings_path, backup_dir, prefix="settings")
    if bak:
        print(f"[regen-claude] Backup: {bak}")

    write_json(settings_path, merged)
    print(f"[regen-claude] Regenerated -> {settings_path}")
    return 0


def cmd_merge_codex_wsl(args) -> int:
    """Merge WSL config.toml: read after + before backup -> merge -> write."""
    config_path: Path = args.wsl_config
    backup_path: Path | None = args.wsl_backup
    backup_dir: Path = args.backup_dir

    if backup_path is None:
        print("[merge-codex-wsl] No backup path specified (use --wsl-backup or --sim)",
              file=sys.stderr)
        return 1

    after = parse_toml(config_path)
    before = parse_toml(backup_path)

    if not after:
        print(f"[merge-codex-wsl] No 'after' file found: {config_path}", file=sys.stderr)
        return 1
    if not before:
        print(f"[merge-codex-wsl] No 'before' backup found: {backup_path}", file=sys.stderr)
        return 1

    merged = merge_codex(after, before)

    # 覆盖 model 字段 (cc-switch 热切换不改 config.toml, model 名来自 DB)
    override = getattr(args, "override_model", None)
    if override and merged.get("model") != override:
        print(f"[merge-codex-wsl] Override model: {merged.get('model')} -> {override}")
        merged["model"] = override

    # 防降级棘轮: before (启动备份) 可能本身就是降级版 (cc-switch 写过部分 config),
    # merge_codex 全盘取 before 导致 projects/mcp_servers/tui 等段丢失。
    # 从历史最丰富备份兜底恢复结构性段。
    richest = find_richest_codex_backup(backup_dir)
    if richest is not None:
        rich_data = parse_toml(richest)
        if rich_data:
            merged, restored = restore_reduced_codex(merged, rich_data)
            if restored:
                print(
                    f"[merge-codex-wsl] 防降级: 从 {richest.name} 恢复: "
                    f"{', '.join(restored)}"
                )

    # Backup current file before overwriting
    bak = backup_file(config_path, backup_dir, prefix="config-toml")
    if bak:
        print(f"[merge-codex-wsl] Backup: {bak}")

    write_toml(config_path, merged)
    print(f"[merge-codex-wsl] Merged -> {config_path}")

    # Prune old backups
    deleted = prune_backups(backup_dir, "config-toml-*.toml", keep=20)
    if deleted:
        print(f"[merge-codex-wsl] Pruned {deleted} old backups")
    return 0


def cmd_sync_windows(args) -> int:
    """Sync merged WSL config to Windows side (preserves Windows-only sections).

    所有合并在 WSL 文件系统上完成 (避免 9P 挂载的 IO 问题), 最后再 cp 一次回 Windows。
    """
    wsl_path: Path = args.wsl_config
    win_path: Path = args.windows_config
    win_backup: Path | None = args.windows_backup
    backup_dir: Path = args.backup_dir

    if win_backup is None or not Path(win_backup).is_file():
        print("[sync-windows] No Windows backup available, skipping Windows sync",
              file=sys.stderr)
        return 0

    # WSL config should have been just-merged by cmd_merge_codex_wsl
    wsl_merged = parse_toml(wsl_path)
    win_before = parse_toml(win_backup)

    if not wsl_merged:
        print(f"[sync-windows] WSL config empty: {wsl_path}", file=sys.stderr)
        return 1
    if not win_before:
        print(f"[sync-windows] Windows backup empty: {win_backup}", file=sys.stderr)
        return 1

    merged = merge_codex_for_windows(wsl_merged, win_before)

    # 防降级棘轮 (Windows 端): win_before (启动备份) 可能降级, 从历史最丰富的
    # Windows 备份兜底恢复结构性段。codex APP 通常会自我写回完整配置,
    # 但接管/切换瞬间也可能写入部分配置, 加这层防护避免 projects/mcp/desktop 丢失。
    richest_win = find_richest_codex_backup(backup_dir, pattern="codex-windows-*.toml")
    if richest_win is not None:
        rich_data = parse_toml(richest_win)
        if rich_data:
            merged, restored = restore_reduced_codex(merged, rich_data)
            if restored:
                print(
                    f"[sync-windows] 防降级: 从 {richest_win.name} 恢复: "
                    f"{', '.join(restored)}"
                )

    # base_url 保持 127.0.0.1 — WSL2 (Win10 默认 NAT 模式) 的 inbound 只能走
    # 127.0.0.1 (loopback), 实测 200ms 通; 走 WSL eth0 IP (192.168.x.x) 会被
    # NAT 丢包, 10 秒超时。Windows 端 codex APP 一直"思考" 就是这个原因。
    # 旧实现错误地把它替换成 WSL eth0 IP, 那是镜像模式的方案, Win10 不支持。

    # 把合并结果先写到 WSL 临时文件 (避免 9P 写 Windows 时的 IO 异常)
    staging_dir = Path("/tmp/cc-switch-windows-staging")
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_path = staging_dir / "config.toml"
    write_toml(staging_path, merged)
    print(f"[sync-windows] Staged at WSL path: {staging_path}")

    # Backup current Windows file before overwriting
    bak = backup_file(win_path, backup_dir, prefix="codex-windows")
    if bak:
        print(f"[sync-windows] Backup: {bak}")

    # 用 cp 从 WSL 临时位置写到 Windows 位置
    try:
        shutil.copy2(staging_path, win_path)
        print(f"[sync-windows] Synced -> {win_path}")
    except Exception as e:
        print(f"[sync-windows] ERROR: cp 失败 {staging_path} -> {win_path}: {e}", file=sys.stderr)
        return 1

    # Prune old Windows backups
    deleted = prune_backups(backup_dir, "codex-windows-*.toml", keep=20)
    if deleted:
        print(f"[sync-windows] Pruned {deleted} old Windows backups")
    return 0


def cmd_sync_auth(args) -> int:
    """Sync auth.json from WSL to Windows side.

    WSL 端 codex CLI 登录后会写 ~/.codex/auth.json (含 chatgpt token);
    Windows 端 codex APP 也独立写自己的 auth.json. wrapper 把 WSL 端
    auth.json 当成 config.toml 一样处理: 备份 + 跨端同步。

    方向: 用 mtime 决定 (新覆盖旧), 避免占位覆盖真 token。
    - WSL 端是 cc-switch 接管占位 (39 字节, PROXY_MANAGED) → mtime 旧
    - Windows 端用户登录后写新 → mtime 新
    - 这样 WSL 同步到 Windows 不会覆盖 Windows 真 token
    """
    wsl_path: Path = args.wsl_auth
    win_path: Path = args.windows_auth
    backup_dir: Path = args.backup_dir

    wsl_exists = wsl_path.is_file()
    win_exists = win_path.is_file()

    if not wsl_exists and not win_exists:
        print("[sync-auth] 两端都不存在 auth.json, 跳过")
        return 0

    # mtime 决定方向: 新的赢, 容忍 1 秒差异 (避免"几乎同时"误触)
    wsl_mtime = wsl_path.stat().st_mtime if wsl_exists else 0
    win_mtime = win_path.stat().st_mtime if win_exists else 0

    if wsl_exists and wsl_mtime - win_mtime > 1.0:
        # WSL 新, 同步到 Windows
        if not win_exists:
            print(f"[sync-auth] Windows 端无 auth.json, 直接从 WSL 复制")
        else:
            print(
                f"[sync-auth] WSL auth.json (mtime newer) -> Windows"
            )

        # 先备份 Windows 当前文件
        if win_exists:
            bak = backup_file(win_path, backup_dir, prefix="auth-windows")
            if bak:
                print(f"[sync-auth] Backup Windows auth.json: {bak}")

        # 写到 WSL staging 再 cp 到 Windows (避免 9P 写异常)
        staging_dir = Path("/tmp/cc-switch-windows-staging")
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging_path = staging_dir / "auth.json"
        shutil.copy2(wsl_path, staging_path)

        try:
            shutil.copy2(staging_path, win_path)
            print(f"[sync-auth] Synced WSL -> Windows: {win_path}")
        except Exception as e:
            print(f"[sync-auth] ERROR cp 失败: {e}", file=sys.stderr)
            return 1

    elif win_exists and win_mtime > wsl_mtime:
        print(
            f"[sync-auth] Windows auth.json (mtime newer) > WSL, "
            f"不动 (WSL 端不应被 Windows 写覆盖)"
        )
    else:
        print(f"[sync-auth] 两端 mtime 相同或差异 < 1s, 跳过同步")

    return 0


def cmd_all(args) -> int:
    """Run merge + sync, optionally scoped to one app_type (分组感知).

    --app-type claude: 只 cmd_merge_settings(Claude 切换不污染 Codex config)
    --app-type codex:  只 codex 命令(merge-codex-wsl + sync-windows + sync-auth)
    --app-type all/缺省: 全部(向后兼容, 退出合并兜底用)

    修复: 改 Claude provider 时 Codex 跟着切(wrapper 无脑跑 all + 同一个 override)。
    """
    app = getattr(args, "app_type", None)
    if app in (None, "", "all"):
        rc = cmd_merge_settings(args)
        if rc != 0:
            return rc
        rc = cmd_merge_codex_wsl(args)
        if rc != 0:
            return rc
        # Windows 同步失败不阻塞 (可能 Windows 文件不可达)
        cmd_sync_windows(args)
        cmd_sync_auth(args)
    elif app == "claude":
        rc = cmd_merge_settings(args)
        if rc != 0:
            return rc
    elif app == "codex":
        rc = cmd_merge_codex_wsl(args)
        if rc != 0:
            return rc
        cmd_sync_windows(args)
        cmd_sync_auth(args)
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cc-switch-merge",
        description="cc-switch exit merge/sync CLI",
    )

    # -- Common arguments shared by all subcommands --------------------------
    parser.add_argument(
        "--sim",
        type=str,
        default=None,
        metavar="DIR",
        help="Sim mode root directory; overrides all default paths",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=Path.home() / ".claude" / "settings.json",
        help="Path to settings.json",
    )
    parser.add_argument(
        "--settings-backup",
        type=Path,
        default=None,
        help="Path to settings.json backup (before snapshot)",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        default=None,
        help="Alias for --settings-backup",
    )
    parser.add_argument(
        "--wsl-config",
        type=Path,
        default=Path.home() / ".codex" / "config.toml",
        help="Path to WSL config.toml",
    )
    parser.add_argument(
        "--wsl-backup",
        type=Path,
        default=None,
        help="Path to WSL config.toml backup",
    )
    parser.add_argument(
        "--windows-config",
        type=Path,
        default=None,
        help="Path to Windows config.toml (accessible from WSL)",
    )
    parser.add_argument(
        "--windows-backup",
        type=Path,
        default=None,
        help="Path to Windows config.toml backup",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path.home() / ".claude" / "backups",
        help="Directory for backup files",
    )
    parser.add_argument(
        "--override-model",
        type=str,
        default=None,
        help="Override model name (from cc-switch DB, hot-switch doesn't write config)",
    )
    parser.add_argument(
        "--app-type",
        type=str,
        default=None,
        choices=["claude", "codex", "all"],
        help="Scope cmd_all to one app (分组感知): claude=只 Claude, codex=只 Codex, all/缺省=全部",
    )
    parser.add_argument(
        "--wsl-auth",
        type=Path,
        default=Path.home() / ".codex" / "auth.json",
        help="Path to WSL codex auth.json",
    )
    parser.add_argument(
        "--windows-auth",
        type=Path,
        default=None,
        help="Path to Windows codex auth.json (accessible from WSL)",
    )

    # -- Subcommands ---------------------------------------------------------
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "merge-settings",
        help="Merge settings.json after cc-switch",
    )
    sub.add_parser(
        "regen-claude",
        help="Regenerate Claude settings.json from richest backup + current cc-switch env",
    )
    sub.add_parser(
        "merge-codex-wsl",
        help="Merge WSL config.toml after cc-switch",
    )
    sub.add_parser(
        "sync-windows",
        help="Sync merged config to Windows side",
    )
    sub.add_parser(
        "sync-auth",
        help="Sync WSL auth.json to Windows side (mtime-based)",
    )
    sub.add_parser(
        "all",
        help="Run merge-settings + merge-codex-wsl + sync-windows + sync-auth",
    )

    return parser


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # --sim overrides all default paths
    if args.sim:
        sim = Path(args.sim)
        # sim 模式强制覆盖所有路径 (用户传了 --sim 就应该完全在 sim 目录下)
        args.settings = sim / "settings.json"
        args.settings_backup = sim / "settings.json.bak"
        args.wsl_config = sim / "config.toml"
        args.wsl_backup = sim / "config.toml.bak"
        args.windows_config = sim / "windows-config.toml"
        args.windows_backup = sim / "windows-config.toml.bak"
        args.wsl_auth = sim / "auth.json"
        args.windows_auth = sim / "windows-auth.json"
        args.backup_dir = sim / "backups"

    # Resolve --backup alias for settings_backup
    if args.settings_backup is None and args.backup is not None:
        args.settings_backup = args.backup

    # Dispatch
    dispatch = {
        "merge-settings": cmd_merge_settings,
        "regen-claude": cmd_regen_claude,
        "merge-codex-wsl": cmd_merge_codex_wsl,
        "sync-windows": cmd_sync_windows,
        "sync-auth": cmd_sync_auth,
        "all": cmd_all,
    }
    fn = dispatch.get(args.command)
    if fn is None:
        parser.print_help()
        return 1

    try:
        return fn(args)
    except NotImplementedError:
        print(f"[{args.command}] Not implemented yet", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
