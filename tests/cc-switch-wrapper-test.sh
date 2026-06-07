#!/usr/bin/env bash
# cc-switch-wrapper-test.sh — 用 dummy cc-switch 测试 wrapper 完整流程
# 不触碰真实文件

set -e

DUMMY_DIR=$(mktemp -d /tmp/cc-switch-wtest-XXXXXX)
BACKUP_REAL=$(mktemp -d /tmp/cc-switch-realbk-XXXXXX)

# 1. 备份真实文件
cp ~/.claude/settings.json $BACKUP_REAL/
cp ~/.codex/config.toml $BACKUP_REAL/

# 2. 创建 dummy cc-switch
cat > $DUMMY_DIR/cc-switch <<'EOF'
#!/usr/bin/env bash
echo "[DUMMY] cc-switch 启动"
sleep 2
echo "[DUMMY] cc-switch 退出"
exit 0
EOF
chmod +x $DUMMY_DIR/cc-switch

# 3. 跑 wrapper
echo "=== 跑 wrapper (用 dummy cc-switch) ==="
PATH="$DUMMY_DIR:$PATH" /home/doing/bin/cc-switch </dev/null 2>&1
RC=$?
echo "=== wrapper 退出码: $RC ==="

# 4. 验证
echo ""
echo "=== 合并后 settings.json 关键字段 ==="
python3 -c "
import json
s = json.load(open('/home/doing/.claude/settings.json'))
print('  hooks:', 'hooks' in s)
print('  permissions:', 'permissions' in s)
print('  env.ANTHROPIC_BASE_URL:', s.get('env', {}).get('ANTHROPIC_BASE_URL', 'MISSING'))
"

# 5. 还原 + 清理
cp $BACKUP_REAL/settings.json ~/.claude/settings.json
cp $BACKUP_REAL/config.toml ~/.codex/config.toml
rm -rf $DUMMY_DIR $BACKUP_REAL
echo ""
echo "=== 真实文件已还原 ==="
