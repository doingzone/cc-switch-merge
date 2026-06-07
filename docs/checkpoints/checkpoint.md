# Checkpoint — cc-switch 退出合并/同步 (你醒来后看这个)

> **更新时间**: 2026-06-05
> **状态**: 全部代码修复完成, 等你真实环境测试

## 你醒来后要做的

### 1. 跑诊断脚本 (1 步)
```bash
bash ~/.codex/cc-switch-diag.sh
```
看输出,贴给我任何异常。

### 2. 跑真实环境测试
```bash
cc-switch
```

应该能看到 wrapper 顶部:
```
============================================
[cc-switch WRAPPER v2] 启动备份 + 合并模式
============================================
[cc-switch-backup] ✓ settings.json 已备份
[cc-switch-backup] ✓ WSL config.toml 已备份
[cc-switch-backup] ✓ Windows config.toml 已备份
```

切 provider → 关掉窗口,看:
```
[cc-switch] cc-switch 已退出,准备合并/同步...
[merge-settings] Merged -> ...
[merge-codex-wsl] Merged -> ...
[sync-windows] base_url [custom]: http://127.0.0.1:15721/v1 -> http://192.168.1.15:15721/v1
[sync-windows] Synced -> ...
[cc-switch] 合并/同步完成, exit=0
```

### 3. 验证 Windows 端
```bash
grep -A1 'base_url' /mnt/d/Users/doing/.codex/config.toml
# 应该看到 http://192.168.1.15:15721/v1, 不是 127.0.0.1
```

## 这一轮修了什么

| 问题 | 修复 |
|---|---|
| wrapper 引用已删脚本 | 移除 |
| wrapper 调合并脚本参数顺序错 (argparse 要求 --opts 在子命令前) | 改为 `python3 merge.py --opts ... all` |
| Windows 备份 9P 路径 cp 失败 | wrapper 先 cp 到 WSL 临时位置再备份 |
| 空 Windows backup 报 IsADirectoryError | 改用 `is_file()` 检查, 优雅跳过 |
| Windows sync 失败阻塞 | `cmd_all` 不让 sync 失败阻塞退出 |
| **base_url 仍是 127.0.0.1** | **WSL IP 重写**: UDP connect 拿真实 eth0 IP, 优先 192.168.x.x |
| WSL IP 选错 (用 hostname 拿到 169.254.x) | 改用 UDP connect 技巧, 跳 v6/loopback/link-local |

## 关键文件位置

| 文件 | 状态 |
|---|---|
| `/home/doing/.codex/cc-switch-merge.py` | 完成, 含 WSL IP 重写 |
| `/home/doing/.codex/cc-switch-merge_test.py` | 29 tests, 全部通过 |
| `/home/doing/.codex/cc-switch-sim.sh` | 模拟端到端, 全部通过 |
| `/home/doing/.codex/cc-switch-diag.sh` | **新加** 一键诊断 |
| `/home/doing/.codex/cc-switch-wrapper-test.sh` | **新加** wrapper 端到端测试 (用 dummy cc-switch) |
| `/home/doing/bin/cc-switch` | 完成, 含 [WRAPPER v2] 标识 |

## 备份策略

合并前自动备份 3 份,保留 20 份最新,旧的自动删。备份目录:
- `~/.claude/backups/settings-YYYYMMDD-HHMMSS.json`
- `~/.claude/backups/config-toml-YYYYMMDD-HHMMSS.bak`
- `~/.claude/backups/codex-windows-YYYYMMDD-HHMMSS.bak`

回滚命令:
```bash
cp ~/.claude/backups/settings-XXX.json  ~/.claude/settings.json
cp ~/.claude/backups/config-toml-XXX.bak  ~/.codex/config.toml
```

## 还可能踩的坑

1. **WSL IP 每次启动可能变** — 每次 wrapper 退出都会自动获取当前 IP 重写,不需要手动改
2. **cc-switch 还在跑时切换 provider** — 只有 claude 的 provider 切换会改 settings.json;codex 的 provider 切换才会改 config.toml
3. **VcXsrv 启动失败** — 看 wrapper 是否有 "VcXsrv on :1 未就绪" 警告
4. **9P 路径 IO 失败** — 合并脚本优雅降级,不会阻塞 wrapper 退出

## 没做的事 (下一轮)

- 实时 trigger (切 provider 立即同步, 而不是退出时)
- Codex App 9P 句柄不释放的解法
- cc-switch 升级 checklist 写入 AGENTS.md
