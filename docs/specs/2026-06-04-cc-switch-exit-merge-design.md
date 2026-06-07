# cc-switch 退出合并/同步 — 设计文档

- **日期**: 2026-06-04
- **状态**: 已批准,待 writing-plans

## 1. 背景与目标

cc-switch 3.16.0 重编译后,旧版所有同步逻辑失效。`~/bin/cc-switch` wrapper 中仍引用已删除的脚本 (`cc-switch-log-watcher.py`、`cc-switch-sync-windows.py`、`cc-switch-config-merge.py`),导致 wrapper 启动报错、退出必失败。

本设计要解决:**在 cc-switch 退出时,自动备份 + 合并 settings.json 和 config.toml,并把 WSL 端 codex 同步到 Windows 端 codex App**。

## 2. 范围

**在范围内**:
- 备份 `$HOME/.claude/settings.json` 和 `$HOME/.codex/config.toml`(启动时)
- cc-switch 退出时合并并写回上面两份文件
- WSL 端 config.toml 合并结果,选择性同步到 `/mnt/d/Users/doing/.codex/config.toml`
- TDD 单元测试 + 模拟端到端验证
- 真实环境测试(由用户执行)

**不在范围内**:
- 实时 trigger(切 provider 立即同步)— 沿用复盘 5.2 节建议,本轮不做
- Windows 端 codex App 是否重读 config 的问题(复盘 2.4 提到 9P 句柄不释放)— 保持原样,等 Windows 端跑通后观察
- 旧版 backup 目录清理(已存在的 `~/.codex/config.toml.bak.*` 继续保留)

## 3. 关键决策摘要

| 决策点 | 选择 | 理由 |
|---|---|---|
| 触发时机 | **只在 wrapper 退出时同步** | 复盘 5.2 建议"watcher 单独跑,不要塞进 wrapper 启动链里";本次简化为"只退出时同步" |
| 实现语言 | **Python 单文件 CLI** | TDD 友好;JSON/TOML 处理用成熟库;单文件可独立测试 |
| settings.json 合并策略 | **以 cc-switch 输出为骨架,补齐备份独有的顶层 key** | 简单、可预测;cc-switch 删过的 key 不应"复活" |
| WSL config.toml 合并策略 | **`[model_providers.*]` 段整体用 cc-switch 输出;其他段全保留备份版本;顶层标量取已知 cc-switch 改的字段** | 用户的明确需求;手工加的 provider 段不保留 |
| Windows config.toml 合并策略 | **只从 WSL 端取 `[model_providers]` + 顶层已知字段;其他全保留 Windows 备份** | 用户明确说"不能直接用 WSL 端已合并的 config.toml" |
| 备份目录 | **统一 `$HOME/.claude/backups/`** | 已有目录,减少新增路径 |
| 错误处理 | **合并/同步失败不掩盖 cc-switch 退出码**;Windows 同步失败不阻塞 WSL 合并 | 用户的退出码代表 cc-switch 自身状态,合并是辅助 |

## 4. 文件清单

| 路径 | 状态 | 说明 |
|---|---|---|
| `~/bin/cc-switch` | 改造 | 删除对已删脚本的引用;启动时增加 settings 备份;退出时调用合并脚本 |
| `~/.claude/backups/` | 复用 | 已有,继续使用 |
| `~/.claude/backups/settings-YYYYMMDD-HHMMSS.json` | 新增 | settings 备份 |
| `~/.claude/backups/config-toml-YYYYMMDD-HHMMSS.bak` | 已有 | 继续产生 |
| `~/.claude/backups/codex-windows-YYYYMMDD-HHMMSS.bak` | 新增 | Windows 端 codex config 备份 |
| `~/.codex/cc-switch-merge.py` | 新增 | 统一合并/同步入口,Python CLI |
| `~/.codex/cc-switch-merge_test.py` | 新增 | TDD 单元测试 |
| `/tmp/cc-switch-sim/` | 模拟用 | 模拟验证用 fixture 目录(运行时生成) |

## 5. 合并算法

### 5.1 settings.json (JSON,顶层 key 级)

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

- `env` 是嵌套 dict,但因整段来自 cc-switch,不需要递归处理
- 不存在的 after 视为致命错误(直接抛异常,wrapper 跳过合并)

### 5.2 WSL config.toml (TOML,段级 + 顶层字段)

**已知 cc-switch 改的顶层 key**(`CCS_KNOWN_TOP_KEYS`):
```python
{
    "model", "model_provider", "model_reasoning_effort",
    "preferred_auth_method", "disable_response_storage",
}
```

```python
def merge_codex(after: dict, before: dict) -> dict:
    """[model_providers] 段: 用 after; 其他段: 用 before; 顶层: 已知 key 用 after, 独有 key 保留。"""
    result = {}
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

**手工 provider 段不保留**:before 中 `[model_providers.myx-java]` 这类 cc-switch 不知道的段,合并后会被清除。

### 5.3 Windows config.toml (TOML,严格只取部分)

```python
WINDOWS_KEEP_INTACT_FROM_BACKUP = "all"  # 简化为:除了 WSL 提供的,全用 Windows 备份

def merge_codex_for_windows(wsl_merged: dict, windows_backup: dict) -> dict:
    """从 wsl_merged 取 [model_providers] + 顶层已知字段; 其他全用 windows_backup。"""
    result = {}
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

## 6. 错误处理

| 类别 | 例子 | 处理 | wrapper 退出码 |
|---|---|---|---|
| 致命:cc-switch 写出的文件损坏/不存在 | `after` parse 失败 | wrapper 跳过合并 | 沿用 cc-switch 退出码 |
| 致命:备份文件损坏 | `before` parse 失败 | 同上 | 同上 |
| 可恢复:Windows 同步失败 | 9P 挂载掉、文件只读 | 写 stderr,继续 WSL 合并 | 0 + warning |
| 可恢复:Windows 备份失败 | `/mnt/d/...` 不可写 | 跳过 Windows 同步 | 0 + warning |
| 信息:cc-switch 没改任何文件 | `after == before` | 跳过合并,直接写 | 0 |

合并脚本失败**不污染 cc-switch 退出码**:

```bash
wait "$CC_PID"
CC_EXIT=$?

python3 "$HOME/.codex/cc-switch-merge.py" all \
    || echo "[cc-switch] 合并/同步失败,见上面日志" >&2

exit $CC_EXIT
```

## 7. 备份保留

`$BACKUP_DIR/*` 保留最近 **20 份**,老的删除。统一在 `cc-switch-merge.py` 的 `prune_backups()` 函数里实现。

## 8. 测试策略

### 8.1 TDD 单元测试 (`cc-switch-merge_test.py`)

| 测试函数 | 覆盖点 |
|---|---|
| `test_merge_settings` | after 全保留、before 独有补齐、冲突 after wins、嵌套 env 不递归、空 before/after 边界 |
| `test_merge_codex` | `[model_providers]` 段用 after、其他段用 before、已知顶层 key 用 after、unique key 保留、手工 provider 不保留 |
| `test_merge_codex_for_windows` | 只取 `[model_providers]` + 顶层已知、Windows 独有段(`[mcp_servers.node_repl]`、`[desktop]`、`[windows]`)全保留 |
| `test_prune_backups` | 保留 20 份最新、缺失目录处理 |
| `test_integration_simulation` | 完整 pipeline 保留 `[projects]` 段、Windows 端保留 `node_repl` |

### 8.2 模拟端到端

`simulate-merge.sh`(不触碰真实 `$HOME`):

```bash
mkdir -p /tmp/cc-switch-sim
cp ~/.claude/settings.json   /tmp/cc-switch-sim/settings-before.json
cp ~/.codex/config.toml      /tmp/cc-switch-sim/codex-wsl-before.toml
cp /mnt/d/Users/doing/.codex/config.toml  /tmp/cc-switch-sim/codex-windows-before.toml
# fixture 里手工造一份"cc-switch 写过的"settings-after.json / codex-wsl-after.toml

python3 ~/.codex/cc-switch-merge.py all --sim /tmp/cc-switch-sim

# 验证输出
diff <(jq -S . /tmp/cc-switch-sim/settings-expected.json) \
     <(jq -S . /tmp/cc-switch-sim/settings-after.json)
```

### 8.3 真实环境(用户参与)

1. 启动 wrapper: `cc-switch`
2. GUI 切换 1 次 provider
3. 关闭 cc-switch
4. 验证日志 + 人工核对配置文件
5. 启动 Codex App,发一条消息,看 cc-switch log 出现 `>>> 请求 URL: ... (model=...)`

回滚命令:

```bash
ls -lt ~/.claude/backups/ | head -5
cp ~/.claude/backups/settings-XXX.json  ~/.claude/settings.json
cp ~/.claude/backups/config-toml-XXX.bak  ~/.codex/config.toml
```

## 9. 端到端数据流

```
[wrapper 启动]
  1. mkdir -p $BACKUP_DIR
  2. cp $SETTINGS_FILE   → backups/settings-XXX.json
  3. cp $CODEX_CONFIG    → backups/config-toml-XXX.bak         [WSL 端]
  4. cp $WINDOWS_CONFIG  → backups/codex-windows-XXX.bak      [Windows 端]
  5. [启动 VcXsrv + cc-switch + watchdog]

[cc-switch 运行中]
  * 用户切换 provider → cc-switch 改写:
    - $SETTINGS_FILE   (env.ANTHROPIC_BASE_URL 等)
    - $CODEX_CONFIG    ([model_providers.*] + model 等)

[wrapper 退出]
  6. python3 ~/.codex/cc-switch-merge.py merge-settings
       → 读 "cc-switch 写出的" settings.json + "备份"
       → 以 cc-switch 输出为骨架,补齐备份独有顶层 key
       → 写回 $SETTINGS_FILE
  7. python3 ~/.codex/cc-switch-merge.py merge-codex --scope wsl
       → 读 "cc-switch 写出的" config.toml + "WSL 备份"
       → TOML 段级合并
       → 写回 $CODEX_CONFIG
  8. **先把 Windows 配置复制到 WSL 临时位置** (`/tmp/cc-switch-windows-staging.toml`),
     **所有合并在 WSL 文件系统上完成**, 最后再 `cp` 回 Windows。
       - 读 WSL 临时位置 (已合并) + Windows 备份
       - **替换 base_url 为 WSL IP** (Windows 端不能用 127.0.0.1, 必须用 WSL IP)
         - 通过 UDP connect 技巧拿真实 eth0 IP, 不是 `gethostbyname_ex` 的 loopback
         - 优先 `192.168.x.x` (Win10 mirrored 模式, Windows 端可直连)
         - 跳过 `127.x.x.x` 和 `169.254.x.x`
       - 只取 [model_providers] + 顶层已知字段; 其他全用 Windows 备份
       - 写回 WSL 临时位置
       - `cp` 临时位置 → $WINDOWS_CONFIG
  9. exit $CC_EXIT
```

## 10. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 合并 bug 导致配置损坏 | 备份目录永远在; 用户可手动 `cp` 恢复 |
| Windows 端 Codex App 不重读 config | 复盘 2.4 已知问题;本轮不在范围,只先保证 config 内容正确,App 重读是下一轮的事 |
| **Windows 端 9P 句柄/IO 异常** | **所有合并在 WSL 文件系统上完成, 最后 `cp` 一次回 Windows** |
| Windows 端 9P 文件 `cp` 写失败 | `cp` 失败不阻塞 wrapper 退出, 写 stderr warning |
| cc-switch 3.16.0 升级破坏同步 | 升级前先备份整个 `~/.cc-switch/` 目录;复盘 5.3 建议的"升级前 checklist" 待补到 `AGENTS.md` |
| `prune_backups` 误删用户手工备份 | 只删 `settings-*.json` 和 `config-toml-*.bak` 模式的文件,不删其他 |

## 11. 后续 TODO(本轮不做)

- 升级 cc-switch 前后的 schema 差异自动化比对(复盘 5.3)
- Windows 端 Codex App 9P 句柄不释放的解法(复盘 2.4)
- 实时 trigger 重新设计(复盘 5.2)
