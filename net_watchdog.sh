#!/bin/bash
# wlan0 死活監視ウォッチドッグ
#
# 想定する障害: アソシエーションは維持されたまま wlan0 の TX が固着し、
# NetworkManager も wpa_supplicant も「リンクUP」と判断して再接続しない状態。
# 2026-09-20 07:54 に発生し、電源抜き差しした 09-21 13:48 まで約30時間全断した。
# 自己修復が効かないため、段階的にエスカレーションして最終的に再起動する。
#
# 判定はデフォルトゲートウェイへの ICMP のみ。名前解決は意図的に見ない
# (MagicDNS の瞬断で再起動させないため)。
#
# cron: */5 * * * * /home/pi/net_watchdog.sh

LOGDIR=/home/pi/bird-watching-youtube-streamer/stream_logs
LOG=$LOGDIR/net_watchdog_$(date +%Y%m).log

# 連続失敗カウンタ。tmpfs に置き、再起動で自動的に 0 に戻す
COUNT_FILE=/dev/shm/net_watchdog.count
# 再起動クールダウン用。再起動をまたいで残す必要があるので実ディスクに置く
LAST_REBOOT_FILE=$LOGDIR/.net_watchdog_last_reboot

RECONNECT_AT=2   # 連続2回 (約10分) で nmcli 再接続
REBOOT_AT=4      # 連続4回 (約20分) で再起動
REBOOT_COOLDOWN=3600  # 再起動ループ防止: 1時間は再度再起動しない

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

count=$(cat "$COUNT_FILE" 2>/dev/null)
[[ $count =~ ^[0-9]+$ ]] || count=0

GW=$(ip route | awk '/^default/{print $3; exit}')

if [ -n "$GW" ] && ping -c 3 -W 2 -I wlan0 "$GW" >/dev/null 2>&1; then
    # 正常。障害から復帰した時だけ記録する (毎回書くとログが膨らむため)
    if [ "$count" -gt 0 ]; then
        log "recovered: gw=$GW に到達 (連続失敗 ${count} 回で終息)"
    fi
    echo 0 > "$COUNT_FILE"
    exit 0
fi

count=$((count + 1))
echo "$count" > "$COUNT_FILE"
log "FAIL ${count}: gw=${GW:-none} 到達不可"

if [ "$count" -eq "$RECONNECT_AT" ]; then
    log "action: nmcli device reconnect wlan0"
    out=$(sudo nmcli device reconnect wlan0 2>&1)
    log "  -> rc=$? ${out}"
    exit 0
fi

if [ "$count" -ge "$REBOOT_AT" ]; then
    now=$(date +%s)
    last=$(cat "$LAST_REBOOT_FILE" 2>/dev/null)
    [[ $last =~ ^[0-9]+$ ]] || last=0
    elapsed=$((now - last))

    if [ "$elapsed" -lt "$REBOOT_COOLDOWN" ]; then
        log "action: 再起動をスキップ (前回から ${elapsed}s、クールダウン ${REBOOT_COOLDOWN}s)"
        exit 0
    fi

    log "action: 復旧しないため再起動する (連続失敗 ${count} 回)"
    echo "$now" > "$LAST_REBOOT_FILE"
    sync
    sudo systemctl reboot
fi

exit 0
