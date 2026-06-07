#!/usr/bin/env bash
# cc-switch-merge 模拟端到端验证
# 用 fixture 文件(/tmp/cc-switch-sim/)模拟 cc-switch 写过之后的状态,
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
mkdir -p "$SIM_DIR"

# 备份: 真实文件复制 (用作 "before" 备份)
cp "$HOME/.claude/settings.json"  "$SIM_DIR/settings.json.bak"
cp "$HOME/.codex/config.toml"     "$SIM_DIR/config.toml.bak"
if [[ -f "/mnt/d/Users/doing/.codex/config.toml" ]]; then
    cp "/mnt/d/Users/doing/.codex/config.toml" "$SIM_DIR/windows-config.toml.bak"
else
    echo "WARN: Windows 端 config.toml 不可访问,跳过" >&2
fi

# 2. 模拟"cc-switch 写过的"settings.json (在 fixture 里手工改)
python3 <<'PY'
import json
from pathlib import Path
sim = Path("/tmp/cc-switch-sim")
before = json.loads((sim / "settings.json.bak").read_text())
after = dict(before)
after["env"] = {
    "ANTHROPIC_AUTH_TOKEN": "PROXY_MANAGED",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:15721",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-7[1M]",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "Opus",
}
# 加 cc-switch 独有的新 key (模拟新版本引入)
after["experimentalCcSwitch"] = {"version": "3.16.0"}
(sim / "settings.json").write_text(json.dumps(after, indent=2, ensure_ascii=False))
PY

# 3. 模拟"cc-switch 写过的"WSL config.toml
python3 <<'PY'
import sys
from pathlib import Path
sim = Path("/tmp/cc-switch-sim")
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore
try:
    import tomli_w
except ImportError:
    print("WARN: tomli_w not available", file=sys.stderr)
    sys.exit(0)
before = tomllib.loads((sim / "config.toml.bak").read_text())
after = dict(before)
after["model"] = "simulated-newmodel"
after["model_provider"] = "custom"
after["model_providers"] = {
    "custom": {
        "name": "simulated-newmodel",
        "wire_api": "responses",
        "requires_openai_auth": True,
        "base_url": "http://127.0.0.1:15721/v1",
    }
}
# 不加新段, 避免被 merge_codex 的 "其他段用 before" 逻辑丢弃
(sim / "config.toml").write_bytes(tomli_w.dumps(after).encode("utf-8"))
PY

# 4. 模拟"cc-switch 写过的"windows config.toml
# 这里我们做的是 WSL 改写之后还没同步的状态, 直接复制"WSL 写过的"作为 Windows 端的 after
if [[ -f "$SIM_DIR/windows-config.toml.bak" ]]; then
    cp "$SIM_DIR/config.toml" "$SIM_DIR/windows-config.toml"
fi

# 5. 跑合并脚本 (--sim 模式覆盖所有路径)
# 注意: --sim 是顶层参数, 必须在子命令 all 之前
# 同时显式传 --settings / --wsl-config / --windows-config / --backup-dir,
# 因为 merge 脚本对这些参数有非 None 默认值, --sim 的 None-检查不会覆盖
echo "=== Running cc-switch-merge.py --sim $SIM_DIR all ==="
python3 "$MERGE_SCRIPT" \
    --sim "$SIM_DIR" \
    --settings "$SIM_DIR/settings.json" \
    --wsl-config "$SIM_DIR/config.toml" \
    --windows-config "$SIM_DIR/windows-config.toml" \
    --backup-dir "$SIM_DIR/backups" \
    all

# 6. 验证 settings.json 合并结果
echo ""
echo "=== 验证 settings.json 合并结果 ==="
python3 <<PY
import json
from pathlib import Path
sim = Path("$SIM_DIR")
result = json.loads((sim / "settings.json").read_text())
before = json.loads((sim / "settings.json.bak").read_text())
for key in ("hooks", "permissions", "statusLine", "language", "theme"):
    assert key in result, f"FAIL: {key} 丢失"
    print(f"  OK: {key} 保留")
assert result["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:15721"
print("  OK: env.ANTHROPIC_BASE_URL = http://127.0.0.1:15721")
assert result["experimentalCcSwitch"]["version"] == "3.16.0"
print("  OK: experimentalCcSwitch.version = 3.16.0")
print("settings.json 验证通过")
PY

# 7. 验证 WSL config.toml 合并结果
echo ""
echo "=== 验证 WSL config.toml 合并结果 ==="
python3 <<PY
import sys
from pathlib import Path
sim = Path("$SIM_DIR")
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore
result = tomllib.loads((sim / "config.toml").read_text())
before = tomllib.loads((sim / "config.toml.bak").read_text())
assert result["model"] == "simulated-newmodel", f"FAIL: model = {result['model']}"
print("  OK: model = simulated-newmodel")
assert result["model_providers"]["custom"]["name"] == "simulated-newmodel"
print("  OK: [model_providers.custom].name = simulated-newmodel")
for proj in before.get("projects", {}):
    assert proj in result.get("projects", {}), f"FAIL: project {proj} 丢失"
print(f"  OK: {len(before.get('projects', {}))} 个 projects 段保留")
if "mcp_servers" in before:
    assert "mcp_servers" in result, "FAIL: mcp_servers 丢失"
    print("  OK: mcp_servers 段保留")
print("WSL config.toml 验证通过")
PY

# 8. 验证 Windows config.toml 同步结果
if [[ -f "$SIM_DIR/windows-config.toml" ]]; then
    echo ""
    echo "=== 验证 Windows config.toml 同步结果 ==="
    python3 <<PY
import sys
from pathlib import Path
sim = Path("$SIM_DIR")
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore
result = tomllib.loads((sim / "windows-config.toml").read_text())
win_before = tomllib.loads((sim / "windows-config.toml.bak").read_text())
assert result["model_providers"]["custom"]["name"] == "simulated-newmodel"
print("  OK: [model_providers.custom].name = simulated-newmodel")
# 验证 base_url 保持 127.0.0.1 (WSL2 NAT 模式 inbound 必须走 loopback, WSL IP 会超时)
base_url = result["model_providers"]["custom"].get("base_url", "")
if "127.0.0.1" in base_url or "localhost" in base_url:
    print(f"  OK: base_url 保持 127.0.0.1 (Win10 WSL2 NAT inbound 要求): {base_url}")
else:
    print(f"  WARN: base_url 不是 loopback (Win10 WSL2 会 10s 超时): {base_url}")
if "mcp_servers" in win_before and "node_repl" in win_before["mcp_servers"]:
    assert "node_repl" in result["mcp_servers"], "FAIL: mcp_servers.node_repl 丢失"
    print("  OK: mcp_servers.node_repl 保留")
if "desktop" in win_before:
    assert "desktop" in result, "FAIL: desktop 段丢失"
    print("  OK: desktop 段保留")
if "windows" in win_before:
    assert "windows" in result, "FAIL: windows 段丢失"
    print("  OK: windows 段保留")
if "plugins" in win_before:
    assert "plugins" in result, "FAIL: plugins 段丢失"
    print(f"  OK: plugins 段保留 ({len(win_before['plugins'])} 个)")
print("Windows config.toml 验证通过")
PY
else
    echo "(skipping Windows validation, no fixture)"
fi

echo ""
echo "=== 模拟验证全部通过 ==="
