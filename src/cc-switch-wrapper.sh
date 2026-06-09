#!/usr/bin/env bash
# cc-switch wrapper — backs up settings.json & WSL/Windows config.toml,
# then executes cc-switch via VcXsrv (bypasses WSLg WebKit2GTK keyboard bug).
# After cc-switch exits, calls cc-switch-merge.py to merge and sync.
# X11 watchdog monitors display connection and auto-restarts cc-switch on loss.
set -euo pipefail

echo "============================================" > /tmp/cc-switch-wrapper.log
echo "[cc-switch WRAPPER v2] 启动备份 + 合并模式 (日志: /tmp/cc-switch-wrapper.log)"
echo "============================================" | tee -a /tmp/cc-switch-wrapper.log

SETTINGS_FILE="$HOME/.claude/settings.json"
CODEX_CONFIG="$HOME/.codex/config.toml"
WINDOWS_CONFIG="/mnt/d/Users/doing/.codex/config.toml"
CODEX_AUTH="$HOME/.codex/auth.json"
WINDOWS_AUTH="/mnt/d/Users/doing/.codex/auth.json"
BACKUP_DIR="$HOME/.claude/backups"
MERGE_SCRIPT="$HOME/.codex/cc-switch-merge.py"
REAL_CC_SWITCH="/usr/bin/cc-switch"
VCXSRV="/mnt/c/Program Files/VcXsrv/vcxsrv.exe"
VCX_DISPLAY=":1"
ORIGINAL_DISPLAY="${DISPLAY:-}"
TARGET_DISPLAY="$VCX_DISPLAY"

# 保存原始参数,供看门狗重启时使用
CC_SWITCH_ARGS=("$@")

# --- WSL 稳定性修复 ---
export LIBGL_ALWAYS_SOFTWARE=1
export WEBKIT_DISABLE_COMPOSITING_MODE=1
export GDK_BACKEND=x11

# D-Bus session bus (WebKit2GTK 依赖)
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
    eval "$(dbus-launch --sh-syntax)" 2>/dev/null || true
fi

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR" || true

CONFIG_BACKUP=""
CODEX_BACKUP=""

# Back up settings.json (保留)
if [[ -f "$SETTINGS_FILE" ]]; then
    ts=$(date +%Y%m%d-%H%M%S)
    CONFIG_BACKUP="$BACKUP_DIR/settings-${ts}.json"
    cp "$SETTINGS_FILE" "$CONFIG_BACKUP" || true
    echo "[cc-switch-backup] ✓ settings.json 已备份" | tee -a /tmp/cc-switch-wrapper.log
fi

# Back up WSL config.toml
if [[ -f "$CODEX_CONFIG" ]]; then
    ts=$(date +%Y%m%d-%H%M%S)
    CODEX_BACKUP="$BACKUP_DIR/config-toml-${ts}.bak"
    cp "$CODEX_CONFIG" "$CODEX_BACKUP" || true
    echo "[cc-switch-backup] ✓ WSL config.toml 已备份" | tee -a /tmp/cc-switch-wrapper.log
fi

# Back up Windows config.toml (新增)
# 9P 路径直接 cp 可能失败, 先复制到 WSL 临时位置再备份
WINDOWS_BACKUP=""
if [[ -f "$WINDOWS_CONFIG" ]]; then
    ts=$(date +%Y%m%d-%H%M%S)
    WINDOWS_STAGING="/tmp/cc-switch-win-staging-${ts}.toml"
    if cp "$WINDOWS_CONFIG" "$WINDOWS_STAGING" 2>/dev/null; then
        WINDOWS_BACKUP="$BACKUP_DIR/codex-windows-${ts}.bak"
        cp "$WINDOWS_STAGING" "$WINDOWS_BACKUP" || true
        rm -f "$WINDOWS_STAGING"
        echo "[cc-switch-backup] ✓ Windows config.toml 已备份" | tee -a /tmp/cc-switch-wrapper.log
    else
        echo "[cc-switch-backup] WARN: Windows config.toml 复制失败,跳过 Windows 同步" | tee -a /tmp/cc-switch-wrapper.log
    fi
fi

# Back up WSL auth.json (与 config.toml/settings.json 同等处理)
if [[ -f "$CODEX_AUTH" ]]; then
    ts=$(date +%Y%m%d-%H%M%S)
    CODEX_AUTH_BACKUP="$BACKUP_DIR/auth-wsl-${ts}.json"
    cp "$CODEX_AUTH" "$CODEX_AUTH_BACKUP" || true
    echo "[cc-switch-backup] ✓ WSL auth.json 已备份" | tee -a /tmp/cc-switch-wrapper.log
fi

# Check if VcXsrv is already functional on :1
if ! xdpyinfo -display "$VCX_DISPLAY" >/dev/null 2>&1; then
    echo "[cc-switch] 启动 VcXsrv on $VCX_DISPLAY ..."
    "$VCXSRV" "$VCX_DISPLAY" -ac -multiwindow -clipboard -nowgl 2>/dev/null &
    for i in $(seq 1 10); do
        if xdpyinfo -display "$VCX_DISPLAY" >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
fi

if ! xdpyinfo -display "$VCX_DISPLAY" >/dev/null 2>&1; then
    if [[ -n "$ORIGINAL_DISPLAY" ]] && xdpyinfo -display "$ORIGINAL_DISPLAY" >/dev/null 2>&1; then
        TARGET_DISPLAY="$ORIGINAL_DISPLAY"
        echo "[cc-switch] VcXsrv on $VCX_DISPLAY 未就绪,回退到现有 DISPLAY=$TARGET_DISPLAY"
    else
        echo "[cc-switch] 警告: $VCX_DISPLAY 不可用,继续尝试启动 cc-switch"
    fi
fi

# Run cc-switch via VcXsrv
export DISPLAY="$TARGET_DISPLAY"

# --- X11 看门狗 ---
WATCHDOG_MAX_RESTARTS=3
WATCHDOG_INTERVAL=5

x11_watchdog() {
    local cc_pid=$1
    local display=$2
    local restart_count=0

    while true; do
        sleep "$WATCHDOG_INTERVAL"

        if ! kill -0 "$cc_pid" 2>/dev/null; then
            return 0
        fi

        if ! xdpyinfo -display "$display" >/dev/null 2>&1; then
            restart_count=$((restart_count + 1))
            echo "[cc-switch-watchdog] X11 显示 $display 连接丢失 (第${restart_count}次/${WATCHDOG_MAX_RESTARTS}次上限)"

            if [ "$restart_count" -gt "$WATCHDOG_MAX_RESTARTS" ]; then
                echo "[cc-switch-watchdog] 已达最大重启次数,放弃监控"
                kill "$cc_pid" 2>/dev/null || true
                return 1
            fi

            kill "$cc_pid" 2>/dev/null || true
            wait "$cc_pid" 2>/dev/null || true
            sleep 1

            "$VCXSRV" "$display" -ac -multiwindow -clipboard -nowgl 2>/dev/null &
            sleep 2

            if ! xdpyinfo -display "$display" >/dev/null 2>&1; then
                echo "[cc-switch-watchdog] VcXsrv 重启失败,放弃"
                return 1
            fi

            echo "[cc-switch-watchdog] 重新启动 cc-switch ..."
            "$REAL_CC_SWITCH" "${CC_SWITCH_ARGS[@]}" &
            cc_pid=$!
        fi
    done
}

# 启动 cc-switch 并 fork 看门狗
"$REAL_CC_SWITCH" "${CC_SWITCH_ARGS[@]}" &
CC_PID=$!

x11_watchdog "$CC_PID" "$TARGET_DISPLAY" &
WATCHDOG_PID=$!

# 启动日志 watcher: 监听"热切换"行, 切 provider 后 3 秒触发合并
# (用户切了 provider 不需要等关 GUI 就能同步)
WATCHER_PID=""
CC_LOG="$HOME/.cc-switch/logs/cc-switch.log"
DEBUG_LOG="/tmp/cc-switch-watcher-debug.log"
if [[ -f "$CC_LOG" ]]; then
    (
        LAST_SIZE=0
        ITER=0
        echo "[watcher] start, CC_PID=$CC_PID, CC_LOG=$CC_LOG" >> "$DEBUG_LOG"
        while kill -0 "$CC_PID" 2>/dev/null; do
            ITER=$((ITER + 1))
            sleep 2
            [[ -f "$CC_LOG" ]] || { echo "[watcher] iter=$ITER CC_LOG gone, continue" >> "$DEBUG_LOG"; continue; }
            CUR_SIZE=$(stat -c%s "$CC_LOG" 2>/dev/null || echo 0)
            echo "[watcher] iter=$ITER LAST=$LAST_SIZE CUR=$CUR_SIZE" >> "$DEBUG_LOG"
            # 处理 cc-switch.log truncate/rotate: CUR 突然变小 (e.g. cc-switch 重新打开 log 文件)
            # 之前的 LAST_SIZE 是 truncate 前的, 新内容 size < 老 size, 必须重置 LAST_SIZE=0 重新读
            if [[ "$CUR_SIZE" -lt "$LAST_SIZE" ]]; then
                echo "[watcher] iter=$ITER log truncated/rotated: $LAST_SIZE -> $CUR_SIZE, reset LAST_SIZE=0" >> "$DEBUG_LOG"
                LAST_SIZE=0
            fi
            if [[ "$CUR_SIZE" -gt "$LAST_SIZE" ]]; then
                NEW=$(tail -c +$((LAST_SIZE + 1)) "$CC_LOG" 2>/dev/null)
                if echo "$NEW" | grep -qE '热切换 (codex|claude) 的目标供应商'; then
                    echo "[watcher] iter=$ITER HOT SWITCH detected" >> "$DEBUG_LOG"
                    HOT_LINE=$(echo "$NEW" | grep -E '热切换.*目标供应商' | tail -1)
                    PROVIDER_ID=$(echo "$HOT_LINE" | grep -oP '目标供应商为 \K[a-f0-9-]+')
                    APP_TYPE=$(echo "$HOT_LINE" | grep -oP '热切换 \K\S+' | head -1)
                    MODEL_OVERRIDE=""
                    if [[ -n "$PROVIDER_ID" && -n "$APP_TYPE" ]]; then
                        MODEL_OVERRIDE=$(python3 -c "
import sqlite3, json, re, sys
try:
    conn = sqlite3.connect('$HOME/.cc-switch/cc-switch.db')
    row = conn.execute('SELECT settings_config FROM providers WHERE id = ? AND app_type = ?', ('$PROVIDER_ID', '$APP_TYPE')).fetchone()
    conn.close()
    if row:
        sc = json.loads(row[0])
        if '$APP_TYPE' == 'claude':
            print(sc.get('env', {}).get('ANTHROPIC_MODEL', ''))
        else:
            cfg = sc.get('config','')
            m = re.search(r'^model\s*=\s*\"([^\"]+)\"', cfg, re.M)
            print(m.group(1) if m else '')
except: pass
" 2>/dev/null)
                    fi
                    if [[ -n "$MODEL_OVERRIDE" ]]; then
                        echo "[cc-switch] 热切换: provider=$PROVIDER_ID model=$MODEL_OVERRIDE" | tee -a /tmp/cc-switch-wrapper.log
                    else
                        echo "[cc-switch] 热切换: provider=$PROVIDER_ID (未找到 model)" | tee -a /tmp/cc-switch-wrapper.log
                    fi
                    sleep 3
                    echo "[watcher] iter=$ITER after sleep 3, before merge" >> "$DEBUG_LOG"
                    if [[ -f "$MERGE_SCRIPT" ]]; then
                        EXTRA_ARGS=""
                        if [[ -n "$MODEL_OVERRIDE" ]]; then
                            EXTRA_ARGS="--override-model $MODEL_OVERRIDE"
                        fi
                        echo "[cc-switch] 自动合并: $MERGE_SCRIPT $EXTRA_ARGS" | tee -a /tmp/cc-switch-wrapper.log
                        python3 "$MERGE_SCRIPT" \
                            --settings "$SETTINGS_FILE" \
                            --settings-backup "$CONFIG_BACKUP" \
                            --wsl-config "$CODEX_CONFIG" \
                            --wsl-backup "$CODEX_BACKUP" \
                            --windows-config "$WINDOWS_CONFIG" \
                            --windows-backup "$WINDOWS_BACKUP" \
                            --wsl-auth "$CODEX_AUTH" \
                            --windows-auth "$WINDOWS_AUTH" \
                            --backup-dir "$BACKUP_DIR" \
                            $EXTRA_ARGS \
                            all 2>&1 | tee -a /tmp/cc-switch-wrapper.log
                        echo "[watcher] iter=$ITER merge done, RC=${PIPESTATUS[0]}" >> "$DEBUG_LOG"

                        # Race condition 防护: cc-switch 接管时会写 settings.json 覆盖 wrapper 的合并
                        # 合并后等 5 秒, 看 settings.json keys 数量, < 10 重新合并 (最多 3 次)
                        for RETRY in 1 2 3; do
                            sleep 5
                            KEY_COUNT=$(python3 -c "
import json
try:
    d = json.load(open('$SETTINGS_FILE'))
    print(len(d))
except: print(0)
" 2>/dev/null)
                            echo "[watcher] iter=$ITER retry=$RETRY settings.json keys=$KEY_COUNT" >> "$DEBUG_LOG"
                            if [[ "$KEY_COUNT" -ge 10 ]]; then
                                echo "[watcher] iter=$ITER stable after retry=$RETRY" >> "$DEBUG_LOG"
                                break
                            fi
                            echo "[watcher] iter=$ITER cc-switch 覆盖了, retry=$RETRY" | tee -a /tmp/cc-switch-wrapper.log
                            python3 "$MERGE_SCRIPT" \
                                --settings "$SETTINGS_FILE" \
                                --settings-backup "$CONFIG_BACKUP" \
                                --wsl-config "$CODEX_CONFIG" \
                                --wsl-backup "$CODEX_BACKUP" \
                                --windows-config "$WINDOWS_CONFIG" \
                                --windows-backup "$WINDOWS_BACKUP" \
                                --wsl-auth "$CODEX_AUTH" \
                                --windows-auth "$WINDOWS_AUTH" \
                                --backup-dir "$BACKUP_DIR" \
                                $EXTRA_ARGS \
                                all 2>&1 | tee -a /tmp/cc-switch-wrapper.log
                        done
                    fi
                fi
                LAST_SIZE=$CUR_SIZE
            fi
        done
        echo "[watcher] EXIT: kill -0 $CC_PID returned false" >> "$DEBUG_LOG"
    ) &
    WATCHER_PID=$!
    echo "[watcher] started, PID=$WATCHER_PID" >> "$DEBUG_LOG"
fi

# 等待 cc-switch 完成 (用户关 GUI / Ctrl+C)
wait "$CC_PID" 2>/dev/null || true
CC_EXIT=$?

# 关闭看门狗 (不要让 kill/wait 的失败触发 set -e)
kill "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true

# 关闭 watcher
[[ -n "$WATCHER_PID" ]] && kill "$WATCHER_PID" 2>/dev/null || true
[[ -n "$WATCHER_PID" ]] && wait "$WATCHER_PID" 2>/dev/null || true

# 退出前最后合并一次 (兜底, 即使 watcher 没触发)
echo "[cc-switch] cc-switch 已退出 (CC_EXIT=$CC_EXIT), 最后一次合并..." | tee -a /tmp/cc-switch-wrapper.log
if [[ -f "$MERGE_SCRIPT" ]]; then
    echo "[cc-switch] 调用: python3 $MERGE_SCRIPT ... all" | tee -a /tmp/cc-switch-wrapper.log
    python3 "$MERGE_SCRIPT" \
        --settings "$SETTINGS_FILE" \
        --settings-backup "$CONFIG_BACKUP" \
        --wsl-config "$CODEX_CONFIG" \
        --wsl-backup "$CODEX_BACKUP" \
        --windows-config "$WINDOWS_CONFIG" \
        --windows-backup "$WINDOWS_BACKUP" \
        --wsl-auth "$CODEX_AUTH" \
        --windows-auth "$WINDOWS_AUTH" \
        --backup-dir "$BACKUP_DIR" \
        all 2>&1 | tee -a /tmp/cc-switch-wrapper.log
    RC=${PIPESTATUS[0]}
    echo "[cc-switch] 合并/同步完成, exit=$RC" | tee -a /tmp/cc-switch-wrapper.log
    if [[ $RC -ne 0 ]]; then
        echo "[cc-switch] 合并/同步失败,见上面日志" >&2 | tee -a /tmp/cc-switch-wrapper.log
    fi
else
    echo "[cc-switch] WARN: $MERGE_SCRIPT 不存在,跳过合并" >&2 | tee -a /tmp/cc-switch-wrapper.log
fi

exit $CC_EXIT
