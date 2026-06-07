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
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    ext = src.suffix.lstrip(".") or "bak"
    dst = backup_dir / f"{prefix}-{ts}.{ext}"
    shutil.copy2(src, dst)
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


def find_intact_settings_backup(backup_dir: Path, min_keys: int = 10) -> Path | None:
    """从 backup_dir 里找最近的"完整" settings.json 备份。

    "完整" 的定义: 至少含 min_keys 个顶层 key 且包含关键用户字段。
    cc-switch 代理接管后写入的 settings.json 只有 env, 不会超过 2 个 key。
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
        candidates.append((p.stat().st_mtime, p))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def restore_missing_settings_keys(
    current: dict, intact_backup: dict, keys: tuple[str, ...]
) -> tuple[dict, list[str]]:
    """把 current 缺失的 keys 从 intact_backup 补全, 返回 (新 dict, 恢复的 key 列表)。"""
    restored: list[str] = []
    result = dict(current)
    for k in keys:
        if k in result:
            continue
        if k in intact_backup:
            result[k] = intact_backup[k]
            restored.append(k)
    return result, restored


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

    merged = merge_settings(after, before)

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

    # 防退化: 如果 after 缺关键用户字段 (被 cc-switch 接管简化过),
    # 从最近"完整"备份补全, 避免合并后用户的 plugins/permissions/MCP 消失。
    missing_keys = [k for k in CCS_SETTINGS_PRESERVE_KEYS if k not in merged]
    if missing_keys:
        intact = find_intact_settings_backup(backup_dir)
        if intact is not None:
            intact_data = parse_json(intact)
            merged, restored = restore_missing_settings_keys(
                merged, intact_data, CCS_SETTINGS_PRESERVE_KEYS
            )
            if restored:
                print(
                    f"[merge-settings] 防退化: 从 {intact.name} 恢复 {len(restored)} 个字段: "
                    f"{', '.join(restored[:6])}{' ...' if len(restored) > 6 else ''}"
                )

    # Backup current file before overwriting
    bak = backup_file(settings_path, backup_dir, prefix="settings")
    if bak:
        print(f"[merge-settings] Backup: {bak}")

    write_json(settings_path, merged)
    print(f"[merge-settings] Merged -> {settings_path}")
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

    # Backup current file before overwriting
    bak = backup_file(config_path, backup_dir, prefix="config-toml")
    if bak:
        print(f"[merge-codex-wsl] Backup: {bak}")

    write_toml(config_path, merged)
    print(f"[merge-codex-wsl] Merged -> {config_path}")

    # Prune old backups
    deleted = prune_backups(backup_dir, "config-toml-*.bak", keep=20)
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
    deleted = prune_backups(backup_dir, "codex-windows-*.bak", keep=20)
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
    """Run merge-settings, merge-codex-wsl, sync-windows, sync-auth in order."""
    rc1 = cmd_merge_settings(args)
    if rc1 != 0:
        return rc1

    rc2 = cmd_merge_codex_wsl(args)
    if rc2 != 0:
        return rc2

    # Windows 同步失败不阻塞 (可能 Windows 文件不可达)
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
