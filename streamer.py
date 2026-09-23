"""配信制御スクリプト v4 - 統合モード + 状態ファイル + リトライ + Telegram通知

v4の変更点:
  - ネットワーク/DNS一時障害に耐えるリトライ (with_retry)
  - 異常終了・配信開始をTelegramに通知 (notify.py)
  これにより 'Temporary failure in name resolution' のような一時障害で
  1日分の配信を失う事故を防ぐ。
"""
import os
import sys
import subprocess
import time
import json
import signal
import logging
from datetime import datetime, timedelta
from pathlib import Path
import youtube_api
import notify

WORK_DIR = Path(__file__).parent
PID_FILE = WORK_DIR / 'streamer.pid'
STATUS_FILE = WORK_DIR / 'stream_status.json'
LOG_DIR = WORK_DIR / 'stream_logs'
STREAM_SCRIPT = WORK_DIR / 'stream_ffmpeg.py'
CONFIG_FILE = WORK_DIR / 'broadcast_config.json'

# 配信時刻は crontab の引数だけが情報源。日の出・日の入りや巣の状況で頻繁に
# 変わるので、コードにも設定ファイルにも持たない（二重表記を作らないため）。
#   20 17 * * * ... streamer.py 17:30 18:30
# cronは枠の少し前に起動するので、開始時刻まで待つ。ただしこれより先の枠は
# 「cronの時刻がずれている」とみなして走らせない。
MAX_LEAD_MINUTES = 90
MAX_SEGMENT_HOURS = 8  # Max 8 hours per segment to avoid 12h limit

# Retry settings for transient network/DNS failures
RETRY_ATTEMPTS = 5
RETRY_WAIT_SEC = 30

logging.getLogger('googleapiclient').setLevel(logging.ERROR)
logging.getLogger('google').setLevel(logging.ERROR)
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"streamer_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO, format='%(asctime)s %(message)s',
    handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)


def _parse_hhmm(hhmm):
    """Parse 'H:MM' into (hour, minute). Raises ValueError if out of range."""
    h, m = map(int, str(hhmm).split(':'))
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f'時刻が範囲外: {hhmm}')
    return h, m


def parse_args(argv):
    """Parse `[--now] [開始 終了]`. Returns (manual, start, end) with H:MM strings.

    Raises ValueError on a malformed time or a stray argument, so a crontab typo
    fails loudly instead of streaming at the wrong hour.
    """
    manual = '--now' in argv
    times = [a for a in argv[1:] if not a.startswith('-')]
    if len(times) == 2:
        for t in times:
            _parse_hhmm(t)
        return manual, times[0], times[1]
    if times:
        raise ValueError(f'引数は「開始 終了」の2つだけ: {" ".join(times)}')
    return manual, None, None


def with_retry(fn, attempts=RETRY_ATTEMPTS, wait=RETRY_WAIT_SEC, label=''):
    """Call fn() with retries to survive transient DNS/network failures.

    Retries on ANY exception (DNS errors surface as gaierror/ServerNotFoundError
    deep inside httplib2, so we cannot narrow the type cleanly). Raises
    RuntimeError only after all attempts are exhausted.
    """
    last_err = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            log.warning(f'{label} 失敗 (試行 {i}/{attempts}): {e}')
            if i < attempts:
                time.sleep(wait)
    raise RuntimeError(f'{label} が {attempts} 回の試行後に失敗: {last_err}')


def is_already_running():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            pass
    PID_FILE.write_text(str(os.getpid()))
    return False


def write_status(segment_end, final_end, broadcast_id, mode):
    STATUS_FILE.write_text(json.dumps({
        'segment_end': segment_end.strftime('%H:%M') if segment_end else None,
        'final_end': final_end.strftime('%H:%M') if final_end else None,
        'broadcast_id': broadcast_id,
        'mode': mode,
        'updated': datetime.now().strftime('%H:%M:%S'),
    }))


def _at(now, hhmm):
    """Return `now`'s date at HH:MM."""
    h, m = _parse_hhmm(hhmm)
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def resolve_window(now, start, end, lead_minutes=MAX_LEAD_MINUTES):
    """Turn the crontab-supplied 開始/終了 into concrete datetimes, or None.

    None means "do not stream now": the window has already ended, or cron fired
    so early that its schedule no longer matches the window it was given.
    """
    cs, ce = _at(now, start), _at(now, end)
    if ce <= cs:
        ce += timedelta(days=1)  # 日付を跨ぐ枠
        if now < cs and now < _at(now, end):
            # 深夜0時を過ぎた側にいる: 継続中なのは前日開始の枠
            cs, ce = cs - timedelta(days=1), ce - timedelta(days=1)
    if now >= ce:
        return None
    if cs - now > timedelta(minutes=lead_minutes):
        return None
    return cs, ce


def run_segment(seg_end, mode):
    """Run ONE broadcast segment until seg_end. Returns actual run duration (sec).

    All YouTube API setup calls are wrapped in with_retry so a transient DNS
    blip at segment boundary (the v3 failure mode) no longer kills the day.
    """
    now = datetime.now()
    # --- API setup with retry (this block previously died on the first DNS error) ---
    yt = with_retry(youtube_api.get_youtube_service, label='get_youtube_service')
    stream = with_retry(lambda: youtube_api.get_or_create_stream(yt), label='get_or_create_stream')
    _, stream_key = youtube_api.get_stream_key(stream)
    bc = with_retry(lambda: youtube_api.create_broadcast(yt, config_file=str(CONFIG_FILE)),
                    label='create_broadcast')
    broadcast_id = bc['id']
    with_retry(lambda: youtube_api.bind_broadcast_to_stream(yt, broadcast_id, stream['id']),
               label='bind_broadcast_to_stream')
    log.info(f'Broadcast開始: {broadcast_id} (終了予定 {seg_end.strftime("%H:%M")})')

    write_status(seg_end, None, broadcast_id, mode)

    env = os.environ.copy()
    env['YOUTUBE_STREAM_KEY'] = stream_key
    started = time.time()
    try:
        process = subprocess.Popen(
            ['python3', str(STREAM_SCRIPT), now.strftime('%H:%M'), seg_end.strftime('%H:%M')],
            env=env, cwd=str(WORK_DIR)
        )
        process.wait()
    except Exception as e:
        log.error(f'ffmpegエラー: {e}')
    finally:
        # Always close the broadcast we opened so it cannot become an orphan.
        # end_broadcast already swallows its own errors, but wrap in retry too
        # so a DNS blip here does not leave an orphan holding the stream key.
        try:
            with_retry(lambda: youtube_api.end_broadcast(yt, broadcast_id),
                       attempts=3, label='end_broadcast')
        except Exception as e:
            log.error(f'Broadcast終了に失敗（孤立の可能性）: {e}')
        log.info(f'Broadcast終了: {broadcast_id}')
    return time.time() - started


def stream_session(mode, win_end):
    """Run segments until `win_end`. Each segment <= MAX_SEGMENT_HOURS.

    `win_end` is None for a manual run outside any window, which means a
    single 8h block and then stop.
    """
    short_fail = 0
    while True:
        now = datetime.now()
        cap = now + timedelta(hours=MAX_SEGMENT_HOURS)

        # Never exceed 8h, and never run past the end of the window
        seg_end = min(cap, win_end) if win_end else cap

        log.info(f'セグメント: {now.strftime("%H:%M")} → {seg_end.strftime("%H:%M")} (mode={mode})')

        # run_segment can now raise (retries exhausted). Treat as a short-fail
        # rather than crashing the whole session, so we keep trying / notify.
        try:
            dur = run_segment(seg_end, mode)
        except Exception as e:
            log.error(f'セグメント開始に失敗（リトライ枯渇）: {e}')
            notify.send(f'⚠️ 配信セグメント開始に失敗（API/ネット障害の可能性）\n{e}')
            dur = 0

        # Early-failure guard: if ffmpeg never really ran (died within 2 min),
        # do not churn out dozens of dead broadcasts like the zmq-broken morning.
        if dur < 120:
            short_fail += 1
            log.warning(f'セグメントが約{int(dur)}秒で終了（失敗の可能性 {short_fail}/3）')
            if short_fail >= 3:
                log.error('短時間終了が3回連続。配信を中止します（原因を確認してください）')
                notify.send('🔴 配信を中止しました\n短時間終了が3回連続。原因の確認が必要です。')
                break
            time.sleep(10)
            continue
        short_fail = 0

        if not win_end:
            break  # single 8h block (manual, outside any window)
        if datetime.now() >= win_end - timedelta(minutes=3):
            break  # reached the end of the window
        log.info('8時間分割のため次のセグメントを作成...')
        time.sleep(5)


def _kill_ffmpeg():
    """Kill any lingering ffmpeg processes to prevent zombie accumulation."""
    try:
        subprocess.run(['pkill', '-f', 'ffmpeg.*rtmp'], timeout=5)
    except Exception:
        pass

def _safe_cleanup(reason=''):
    """Best-effort orphan sweep; never raises."""
    _kill_ffmpeg()
    try:
        yt = with_retry(youtube_api.get_youtube_service, attempts=3, label='cleanup get_service')
        youtube_api.cleanup_orphans(yt)
    except Exception as e:
        log.warning(f'クリーンアップ失敗{(" ("+reason+")") if reason else ""}: {e}')


def _on_sigterm(*_):
    # Telegram stop sends SIGTERM; turn it into SystemExit so `finally` runs
    # and the in-flight broadcast gets cleaned up instead of orphaned.
    raise SystemExit(0)


USAGE = '使い方: streamer.py 開始 終了   (例: streamer.py 17:30 18:30) / 手動: streamer.py --now'


def main():
    # 引数はpidファイルに触る前に検証する（不正な起動で実行中の配信を邪魔しない）
    try:
        manual, start, end = parse_args(sys.argv)
    except ValueError as e:
        log.error(f'引数エラー: {e}\n{USAGE}')
        notify.send(f'🔴 配信を開始できません（crontabの時刻指定が不正）\n{e}')
        sys.exit(2)

    if not manual and not start:
        log.error(f'配信枠が指定されていません。crontabの行に開始・終了を書いてください\n{USAGE}')
        notify.send('🔴 配信枠の指定がありません\ncrontabの行に開始・終了時刻を追加してください')
        sys.exit(2)

    if is_already_running():
        print('既に配信プロセスが実行中です')
        sys.exit(1)

    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        # Free the stream key from any leftover broadcast BEFORE we start.
        # This is what prevents an old orphan from swallowing the whole day.
        _safe_cleanup('startup')

        now = datetime.now()
        win = resolve_window(now, start, end) if start else None

        if manual:
            # Manual: run to the end of the window we are inside, else one 8h block
            win_end = win[1] if (win and win[0] <= now) else None
            if win_end:
                log.info(f'=== 手動配信開始（配信枠内: {win_end.strftime("%H:%M")}まで）===')
            else:
                log.info('=== 手動配信開始（配信枠外: 8時間で自動停止）===')
            notify.send('🟢 手動配信を開始しました')
            stream_session('manual', win_end)
        else:
            if not win:
                log.info(f'配信時間帯ではありません (指定枠: {start}-{end})')
                return
            cs, ce = win
            if now < cs:
                wait = (cs - now).total_seconds()
                log.info(f'開始時刻まで待機 ({int(wait // 60)}分)')
                time.sleep(wait)
            label = f'{cs.strftime("%H:%M")}-{ce.strftime("%H:%M")}'
            log.info(f'=== 自動配信開始 ({label}) ===')
            notify.send(f'🟢 自動配信を開始しました ({label})')
            stream_session('auto', ce)
    except (KeyboardInterrupt, SystemExit):
        log.info('中断されました')
    except Exception as e:
        # Catch-all so an unexpected crash still notifies the phone.
        log.error(f'予期しないエラーで終了: {e}')
        notify.send(f'🔴 配信制御が予期しないエラーで終了しました\n{e}')
    finally:
        # Close anything we may have left open (e.g. on SIGTERM mid-segment)
        _safe_cleanup('shutdown')
        PID_FILE.unlink(missing_ok=True)
        STATUS_FILE.unlink(missing_ok=True)
        log.info('配信制御終了')


if __name__ == '__main__':
    main()