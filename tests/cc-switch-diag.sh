#!/usr/bin/env bash
# cc-switch-diag.sh — 一键诊断脚本
# 用法: bash ~/.codex/cc-switch-diag.sh
# 跑完后看输出, 把有问题的地方修.

set -e

echo "=================================================="
echo "cc-switch 同步链路诊断"
echo "=================================================="

echo ""
echo "1. PATH 检查 (确认 wrapper 是 HOME 下的版本)"
which cc-switch
head -1 $(which cc-switch)
echo "    ↑ 如果是 /home/doing/bin/cc-switch 就对了"
echo "    ↑ 如果是 /usr/bin/cc-switch, 说明 PATH 没设好"

echo ""
echo "2. 合并脚本检查"
MERGE="$HOME/.codex/cc-switch-merge.py"
if [[ -f "$MERGE" ]]; then
    echo "    ✓ $MERGE 存在"
    python3 -c "compile(open('$MERGE').read(), 'cc-switch-merge.py', 'exec')" && echo "    ✓ 语法 OK"
else
    echo "    ✗ $MERGE 不存在!"
    exit 1
fi

echo ""
echo "3. TDD 测试"
cd ~/.codex && python3 -m unittest cc-switch-merge_test.py 2>&1 | tail -3

echo ""
echo "4. 备份目录 (最后 5 个)"
ls -lt ~/.claude/backups/ 2>/dev/null | head -6

echo ""
echo "5. Windows 端配置可达性"
if [[ -f /mnt/d/Users/doing/.codex/config.toml ]]; then
    echo "    ✓ /mnt/d/Users/doing/.codex/config.toml 存在"
    SIZE=$(stat -c%s /mnt/d/Users/doing/.codex/config.toml)
    echo "    大小: $SIZE 字节"
else
    echo "    ✗ Windows config 不可达!"
fi

echo ""
echo "6. 模拟合并流程 (用最近 1 份备份)"
SETTINGS_BACKUP=$(ls -t ~/.claude/backups/settings-*.json 2>/dev/null | head -1)
CODEX_BACKUP=$(ls -t ~/.claude/backups/config-toml-*.bak 2>/dev/null | head -1)
WIN_BACKUP=$(ls -t ~/.claude/backups/codex-windows-*.bak 2>/dev/null | head -1)

if [[ -z "$SETTINGS_BACKUP" ]]; then
    echo "    ✗ 无 settings 备份, 跳过"
elif [[ -z "$CODEX_BACKUP" ]]; then
    echo "    ✗ 无 WSL codex 备份, 跳过"
else
    echo "    Settings backup: $SETTINGS_BACKUP"
    echo "    Codex backup:    $CODEX_BACKUP"
    echo "    Win backup:      ${WIN_BACKUP:-<none>}"
    echo ""
    echo "    跑一次合并 (注意: 这次会真的写文件, 但内容应该不变):"
    python3 "$MERGE" \
        --settings "$HOME/.claude/settings.json" \
        --settings-backup "$SETTINGS_BACKUP" \
        --wsl-config "$HOME/.codex/config.toml" \
        --wsl-backup "$CODEX_BACKUP" \
        --windows-config "/mnt/d/Users/doing/.codex/config.toml" \
        --windows-backup "${WIN_BACKUP:-/dev/null}" \
        --backup-dir "$HOME/.claude/backups" \
        all 2>&1 || echo "    (合并失败, 看上面错误)"
fi

echo ""
echo "7. 验证结果"
echo "    WSL model: $(grep -E '^model = ' ~/.codex/config.toml)"
echo "    Win  model: $(grep -E '^model = ' /mnt/d/Users/doing/.codex/config.toml)"

echo ""
echo "=================================================="
echo "诊断完成"
echo "=================================================="
echo ""
echo "如果你看到 [cc-switch WRAPPER v2] 但没有 [cc-switch] cc-switch 已退出,"
echo "说明 wrapper 启动后没到合并段 (可能 VcXsrv/cc-switch 启动失败)."
echo ""
echo "看到 [cc-switch] 合并/同步完成, exit=0 但 Windows 没更新,"
echo "是 cmd_sync_windows 内部问题, 请把诊断输出给我看."
