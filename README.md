# cc-switch-merge

cc-switch 退出时自动合并 settings.json 和 config.toml 并同步到 Windows 端。

## 包含

- `src/cc-switch-merge.py` — 合并/同步核心 (Python)
- `src/cc-switch-wrapper.sh` — 包装 cc-switch, 启动备份 + 退出合并 (bash)
- `tests/` — 单元测试 + 模拟脚本
- `docs/specs/` — 设计文档
- `docs/plans/` — 实现计划
- `docs/checkpoints/` — 进度节点
- `docs/cc-switch-sync-retrospective.md` — 复盘

## 解决什么问题

cc-switch 在"代理接管"模式下, 会重写 `~/.claude/settings.json` 和 `~/.codex/config.toml`, 但**只写代理相关字段** (`ANTHROPIC_BASE_URL` / `model_providers.custom.base_url`), 会清空用户的 `permissions` / `enabledPlugins` / `mcpServers` / `env.ANTHROPIC_MODEL` 等配置。

本工具在 cc-switch 启动时备份 4 个文件 (settings.json, WSL/Windows config.toml, auth.json), 退出时合并 (after 优先, before 补全), 同步到 Windows 端。

## 安装

```bash
# 1. 复制 wrapper 到 PATH
cp src/cc-switch-wrapper.sh $HOME/myx/bin/cc-switch
cp src/cc-switch-wrapper.sh $HOME/bin/cc-switch
chmod +x $HOME/myx/bin/cc-switch

# 2. 复制 merge 脚本到 ~/.codex
cp src/cc-switch-merge.py $HOME/.codex/

# 3. alias (如果还没有)
alias cc-switch=$HOME/myx/bin/cc-switch
```

## 测试

```bash
cd tests
python3 -m unittest cc-switch-merge_test -v    # 41 单测
bash cc-switch-sim.sh                           # 端到端模拟
```

## 关键修复历史

| 修复 | 描述 |
|---|---|
| A | settings.json 缺关键字段时从最近完整备份补全 (防御 cc-switch 清空) |
| B | Windows 端 base_url 保持 127.0.0.1 (WSL2 NAT 模式 inbound 要求) |
| C | merge_settings env 块 deep merge (after wins, before 补全) — 解决 ANTHROPIC_MODEL 丢失 |
| D | auth.json 备份 + mtime-based 跨端同步 |
| E | wrapper 日志 watcher 同时支持 codex + claude provider, 写 env.ANTHROPIC_MODEL |

## Debug

wrapper 启动时会在 `/tmp/cc-switch-watcher-debug.log` 写 watcher 每次循环的状态 (0.1KB/次):

- `[watcher] start` — watcher 启动
- `[watcher] iter=N LAST=X CUR=Y` — 每次循环, mtime 变化
- `[watcher] iter=N HOT SWITCH detected` — 检测到热切换
- `[watcher] iter=N after sleep 3, before merge` — 合并前
- `[watcher] iter=N merge done, RC=N` — 合并后, RC=0 成功
- `[watcher] EXIT` — 主循环正常退出 (cc-switch 死了)

如果"配置又只剩 env 了", 第一时间 `cat /tmp/cc-switch-watcher-debug.log` 看 watcher 在哪死的。

## cc-switch 升级后

如果 cc-switch 升级改了**日志格式** (热切换关键字) 或 **DB schema** (settings_config 字段), 跑这两个验证:

```bash
grep "热切换" ~/.cc-switch/logs/cc-switch.log | tail -1
python3 -c "import sqlite3; print(sqlite3.connect('~/.cc-switch/cc-switch.db').execute('SELECT settings_config FROM providers LIMIT 1').fetchone())"
```

不通过就改 `src/cc-switch-wrapper.sh` 的 grep 模式或 `src/cc-switch-merge.py` 的 `get_provider_model_from_db()`。
