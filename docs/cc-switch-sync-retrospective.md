# Codex App + Codex CLI 同步 cc-switch — 会话复盘

> 范围:2026-05-27 ~ 2026-06-03,跨越多个会话,目标是把 Windows 端 Codex App 和 WSL 端 Codex CLI 统一通过 cc-switch 切换第三方模型(DeepSeek/Kimi/GLM/Qwen)。本文档复盘整段过程、踩过的坑、当前的清理状态和未完成的工作。

## 1. 任务流程

### 1.1 目标与约束

- **Codex App**(Windows 桌面端)读 `D:\Users\doing\.codex\config.toml`
- **Codex CLI**(WSL)读 `/home/doing/.codex/config.toml`
- **cc-switch 3.16.0**(WSL 侧)提供本地代理 `127.0.0.1:15721`,转发到各第三方模型
- 用户希望两边共用 cc-switch 的切换,**不重复配置、不重启会话就能切模型**

### 1.2 阶段划分

| 阶段 | 时间 | 关键事件 | 状态 |
| --- | --- | --- | --- |
| 方案调研 | 05-27 | 提出 4 个方案横向比较 | 已完成,选 C |
| 方案 A PoC | 05-28 | 验证 WSL 到 Windows 的网络可达性 | 已完成,可用 VirtioProxy |
| 代理启动 | 05-29 | cc-switch 监听 `0.0.0.0`,Windows 端 `curl 127.0.0.1:15721` 通 | 已完成 |
| 同步原型 v1 | 05-30 | `cc-switch-config-merge.py` 合并 config.toml | 已废 |
| 同步原型 v2 | 05-31 | inotify + 日志 trigger + DB 反查 model | 偶发可用 |
| 同步原型 v3 | 06-01 | 加 TDD 13 + 20 用例,日志 trigger 提为唯一信号 | 偶发可用 |
| cc-switch 重编译 | 06-02 | 3.16.0 新版覆盖旧版,**所有同步功能失效** | 失联 |
| 清理旧代码 | 06-03 | 用户要求"全部先删了" | 部分完成 |
| 重新设计 | 待开始 | 重新做同步方案 | 未开始 |

### 1.3 方案选择路径

最初四个方案:

- **方案 A**:Codex App 直连 cc-switch 代理(WSL 上的 `127.0.0.1:15721`)
- **方案 B**:在 Windows 端再起一个 cc-switch 实例,WSL 通过 SSH/WSL 桥同步
- **方案 C**:改写/包装 cc-switch,让两边都读同一份配置
- **方案 D**:用 Codex App 的 `model_provider` 字段切到 WSL 转发端口

用户选了 C,但先用 A 验证网络链路。链路验证成功后又回到 C 的子方案(在 WSL 端做同步)。

## 2. 探索过程中的问题

### 2.1 网络层问题

- **WSL → Windows `127.0.0.1:15721` 不可达**:Windows 端用 `127.0.0.1` 访问 WSL 端口默认不通
- **WSL → Windows `192.168.1.x:15721` 也不通**:Win10 Home 不支持 WSL NAT 模式(`wsl --status` 显示默认是 mirrored/VirtioProxy)
- **解决路径**:把 cc-switch DB 里 `proxy_config.listen_address` 改成 `0.0.0.0`,开启 `localhostForwarding=true`,Windows 端就能用 `127.0.0.1:15721` 直连
- **为什么"突然就好了"**:EADDRINUSE 失败后,旧的 cc-switch 进程其实已经在用 `0.0.0.0` 监听(前面 DB 改过),新启动的反而冲突

### 2.2 cc-switch 行为问题

- **GUI 切 provider 时,WSL config 的 `model` 字段不变**:只改内部代理路由
- **catalog 文件只在启动/退出时写**:不能作为实时切换信号
- **切换后必须调用 API 才会"激活"**:WSL config 改完了,新模型不调一次不会变
- **live takeover 启动时**会改 WSL config 改成 `model_provider="custom"`,退出时从 `proxy_live_backup` 恢复

### 2.3 实时信号问题

找过四类信号,只有一类稳定:

1. ❌ WSL config 文件 mtime — 切 provider 时不动
2. ❌ cc-switch DB `providers.last_used_at` — 字段名/位置不确定,改过 schema
3. ❌ catalog 文件 mtime — 只在启动/退出写
4. ✅ cc-switch 日志的 `热切换 codex 的目标供应商为 <uuid>` 行 — 每次切必出

### 2.4 同步机制问题

- 监控到 trigger 后,**WSL 端 config 改对了,Windows 端不动**:`/mnt/d/...` 是 9P 驱动挂载
  - 实际原因:Windows 端 Codex App 持有文件句柄,Python `shutil.copy2` 写入不会被 App 自动重读
  - 后来用 `codex-wsl-proxy.js`(Node 起的 15721 端口 HTTP 代理)兜底,Codex App 实际不读 config 的 model 字段,而是发请求时按 model_provider 查 base_url
- 切换后必须发一次 API 才会"热切换"实际生效:log 里没出现 `>>> 请求 URL: ... (model=xxx)` 就是没生效

## 3. 踩过的坑和解决方案

### 3.1 配置和环境类

| 坑 | 症状 | 解决方案 |
| --- | --- | --- |
| 沙箱写 `~/.codex/` 报 Read-only file system | `apply_patch` / `rm` 失败 | 用 `sandbox_permissions: "require_escalated"` 重新请求 |
| `set -euo pipefail` + `cp` 失败杀掉 wrapper | `cp` 找不到源文件时整个 wrapper 退出 | 所有 `cp`/`mkdir` 加 `\|\| true` |
| 端口冲突 `EADDRINUSE` | `node codex-wsl-proxy.js` 失败 | 先 `lsof -i :15721` 查占用,而不是直接删进程 |
| WSL `xdg-open` 失败 | cc-switch 启动后无窗口 | wrapper 走 VcXsrv `:1`,禁用 GPU 加速 |
| VcXsrv X11 连接丢失 | WebKit2GTK 卡死 | 加 X11 watchdog,最多重启 3 次 |

### 3.2 代码和流程类

| 坑 | 症状 | 解决方案 |
| --- | --- | --- |
| Python 脚本语法错误没及时发现 | watcher/sync 装上去后不工作 | 加到全局 `AGENTS.md`,`cp` 前必须 `python3 -c "compile(open(...).read(), ..., 'exec')"` |
| 多个 patch 叠加让 wrapper 重复 | 同时有两个 `wait "$CC_PID"` | 每次改动先 `diff` 当前版本,有重复就重写 |
| inotify 监控文件而非目录 | cc-switch 用原子 rename 替换 log 时事件丢失 | 监控目录,事件回调里 `os.path.getmtime` + `tail -8192` |
| 日志 trigger 行格式不固定 | "热切换" 后面接的可能是 UUID 也可能是名字 | 正则要兼容 `热切换 codex 的目标供应商为 ([a-f0-9-]+)`,名字走 DB 二次查 |
| DB schema 字段名假设错 | 假设 `currentProviderCodex` 存在,实际叫别的 | 启动时先 `sqlite3 cc-switch.db ".schema"`,用 `schema` 命令实查 |
| provider name → model slug 映射写死 | 加新 provider 后忘了改 mapping | 用 `tomli` 读 codex `model_providers` 表反查,而不是手写 if/elif |
| 切换后没立即调 API 验证 | 改完 config 但不确定是否生效 | 同步脚本里跑一次 `curl -X POST http://127.0.0.1:15721/v1/chat/completions` 触发 |

### 3.3 流程类

| 坑 | 症状 | 解决方案 |
| --- | --- | --- |
| 多个迭代后改动叠加,旧代码没清 | 用户感觉越改越乱,失去方向感 | 用户叫停:"想好再做",统一删旧重写 |
| 关闭 cc-switch 才同步 | 用户来回切模型体验差 | 改成日志实时 trigger,关掉也同步兜底 |
| 切换后必须调 API 才会"激活" | 切了模型但显示还是旧的 | 日志 trigger 后立即用 `/v1/responses` 发个空请求唤醒 |
| 没 TDD | Python 脚本改一行整个坏掉 | 加 13+20 个单元测试覆盖 `extract_hot_switch_id`、`resolve_provider_model`、`should_sync` |

## 4. 完成确认方式

### 4.1 单元/集成测试

- `extract_hot_switch_id`:验证不同日志格式都能拿到 provider UUID
- `provider_name_to_model`:验证映射表
- `should_sync`:验证去重(同一 provider 不重复同步)
- `inotify` 集成:mock 一次 `IN_CLOSE_WRITE` 验证触发

### 4.2 端到端验证

1. WSL 启动 cc-switch GUI
2. 切换 provider
3. 5 秒内观察:
   - `tail -f ~/.cc-switch/logs/cc-switch.log` 出现 `热切换` 行
   - `/tmp/cc-switch-watcher.log` 出现 `Hot-switch: ... → model=..., syncing...`
   - `/home/doing/.codex/config.toml` 的 `model` 字段更新
   - `/mnt/d/Users/doing/.codex/config.toml` 的 `model` 字段更新(通过 codex-wsl-proxy 转发路径)
4. Codex App 发起对话,`tail -f log` 出现 `>>> 请求 URL: ... (model=xxx)`,model 名和刚切的一致

### 4.3 备份策略

每次同步写 Windows 端 config 之前,`shutil.copy2` 到 `config.toml.bak.YYYYMMDD-HHMMSS`,保留最近 20 份,老的删除。

### 4.4 验证命令模板

```bash
# 1. 确认 watcher 还在跑
ps -ef | grep -E "log-watcher|cc-switch" | grep -v grep

# 2. 看 watcher 日志
cat /tmp/cc-switch-watcher.log

# 3. 看 cc-switch 最近的热切换行
grep "热切换" ~/.cc-switch/logs/cc-switch.log | tail -3

# 4. 看 WSL 端 model
grep "^model = " /home/doing/.codex/config.toml

# 5. 看 Windows 端 model
grep "^model = " /mnt/d/Users/doing/.codex/config.toml

# 6. 看 Codex App 是否在用新 model
grep "model=" ~/.cc-switch/logs/cc-switch.log | tail -1
```

## 5. 未完成任务和建议

### 5.1 当前状态(2026-06-03)

已清理:

- ✅ `~/.codex/cc-switch-log-watcher.py`(3.8 KB)
- ✅ `~/.codex/cc-switch-sync-windows.py`(2.3 KB)
- ✅ `~/.codex/cc-switch-model-catalog.json`(46 KB)
- ✅ `/tmp/cc-switch-*` 共 30+ 文件

保留(用户明确说先不删):

- ⏸ `/tmp/cc-switch.db.bak`(4.1 MB)— 旧版 cc-switch DB 备份
- ⏸ `/tmp/cc-switch-model-catalog.json.bak.20260531-191804`(93 KB)— 旧 catalog 备份

未完成(本轮被中断):

- ⏸ `~/bin/cc-switch` wrapper 精简:当前 5164 字节,包含旧同步调用;备份在 `~/bin/cc-switch.bak.full-20260603-104242`

### 5.2 待办

1. **精简 wrapper**:把 `~/bin/cc-switch` 里的 `python3 ~/.codex/cc-switch-log-watcher.py` 和退出时的 `python3 ~/.codex/cc-switch-sync-windows.py` 两处删掉,保留 X11 启动 + watchdog
2. **重新做同步 PoC**:cc-switch 3.16.0 重编译后,先做以下观察,不要直接改代码:
   - `sqlite3 ~/.cc-switch/cc-switch.db ".tables"` 看 schema
   - `sqlite3 ~/.cc-switch/cc-switch.db ".schema providers"` 看字段
   - `sqlite3 ~/.cc-switch/cc-switch.db "SELECT * FROM providers LIMIT 3"` 看实际数据
   - 启动 cc-switch,切一个 provider,看 `tail -f ~/.cc-switch/logs/cc-switch.log` 找 trigger
   - 确认 `热切换 codex 的目标供应商为 <uuid>` 这行**仍然存在**,格式是否变了
3. **重新设计 PoC 之前先回答的问题**:
   - Codex App 是发请求时读 config 还是启动时缓存?
   - cc-switch live takeover 在 3.16.0 还是同样的"改 base_url 留 model"行为?
   - WSL config 改 model 字段时,cc-switch 代理是否立即识别?(看 log 是否有相关 INFO)
4. **如果重新做,先做端到端冒烟**:写一个 30 行的 Python,只做一件事:监听 log 出现 `热切换` → 输出新 model 名到 stdout。不写 config,不调 API,先验证 trigger 信号
5. **不要急着合并 wrapper**:watcher 单独跑,不要塞进 wrapper 启动链里,等稳定了再合并

### 5.3 建议

- **先观察再动手**:cc-switch 重编译后,3.16.0 跟旧版的日志格式、DB schema、takeover 行为可能都变了。盲改会重复之前 8 轮迭代的坑
- **保留 3.16.0 升级的样本**:`/tmp/cc-switch.db.bak`(旧版数据)和现在 `~/.cc-switch/cc-switch.db` 还在,可以 diff schema 对比
- **把"重编译后如何保持同步"加到 `cc-switch` 工具自身的 AGENTS.md 里**,作为以后升级前的 checklist
- **AGENTS.md 已经有"Python 脚本安装前语法检查"** — 这条规则救了好几次,继续保持

## 6. 关键文件路径速查

| 用途 | 路径 |
| --- | --- |
| cc-switch 二进制 | `/usr/bin/cc-switch` |
| cc-switch DB | `/home/doing/.cc-switch/cc-switch.db` |
| cc-switch 日志 | `/home/doing/.cc-switch/logs/cc-switch.log` |
| cc-switch 设置 | `/home/doing/.cc-switch/settings.json` |
| cc-switch 启动 wrapper | `/home/doing/bin/cc-switch`(待精简) |
| wrapper 备份 | `/home/doing/bin/cc-switch.bak.full-20260603-104242` |
| WSL Codex 配置 | `/home/doing/.codex/config.toml` |
| Windows Codex 配置 | `/mnt/d/Users/doing/.codex/config.toml` |
| 旧同步脚本(已删) | `~/.codex/cc-switch-{log-watcher,sync-windows,model-catalog,config-merge}.{py,json}` |
| 旧版同步数据备份(保留) | `/tmp/cc-switch.db.bak`、`/tmp/cc-switch-model-catalog.json.bak.20260531-191804` |
