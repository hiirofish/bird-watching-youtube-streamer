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

# Broadcast time settings: 朝・夕の2本立て。枠ごとにcronから1回起動する。
# 実際の値は broadcast_config.json の "windows" / "lead_minutes" で指定する
# （テンプレートは broadcast_config.json.example）。以下は未設定時のフォールバック。
DEFAULT_WINDOWS = [
    ('4:30', '6:30'),    # 朝
    ('17:30', '19:00'),  # 夕
]
# cronは枠の少し前に起動するので、この範囲内なら開始時刻まで待つ
DEFAULT_LEAD_MINUTES = 90
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


def load_windows(path=CONFIG_FILE):
    """Read broadcast windows from the config file. Returns (windows, lead_minutes).

    Falls back to the DEFAULT_* values on a missing or malformed config: a typo
    in hand-edited JSON must never silently cancel a day's broadcast.
    """
    try:
        with open(path) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return DEFAULT_WINDOWS, DEFAULT_LEAD_MINUTES
    except Exception as e:
        log.warning(f'{path.name} 読み込み失敗 ({e}) - デフォルトの配信枠を使う')
        return DEFAULT_WINDOWS, DEFAULT_LEAD_MINUTES

    windows = DEFAULT_WINDOWS
    if cfg.get('windows'):
        try:
            parsed = [(str(s), str(e)) for s, e in cfg['windows']]
            for s, e in parsed:
                _parse_hhmm(s), _parse_hhmm(e)
            windows = parsed
        except Exception as e:
            log.warning(f'windows の書式が不正 ({e}) - デフォルトの配信枠を使う')

    lead = DEFAULT_LEAD_MINUTES
    if 'lead_minutes' in cfg:
        try:
            lead = int(cfg['lead_minutes'])
        except Exception as e:
            log.warning(f'lead_minutes の書式が不正 ({e}) - {lead}分を使う')

    return windows, lead


WINDOWS, WINDOW_LEAD_MINUTES = load_windows()


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


def windows_label():
    return ', '.join(f'{s}-{e}' for s, e in WINDOWS)


def active_window(now, lead_minutes=WINDOW_LEAD_MINUTES):
    """Return (start, end) of the window we should serve, or None.

    If `now` falls inside a window, that window. Otherwise the next window
    starting within `lead_minutes` (cron fires shortly before each window).
    """
    for s, e in WINDOWS:
        cs, ce = _at(now, s), _at(now, e)
        if ce <= cs:
            ce += timedelta(days=1)
        if cs <= now < ce:
            return cs, ce
        if now < cs and (cs - now) <= timedelta(minutes=lead_minutes):
            return cs, ce
    return None


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


def main():
    if is_already_running():
        print('既に配信プロセスが実行中です')
        sys.exit(1)

    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        # Free the stream key from any leftover broadcast BEFORE we start.
        # This is what prevents an old orphan from swallowing the whole day.
        _safe_cleanup('startup')

        now = datetime.now()
        win = active_window(now)

        if '--now' in sys.argv:
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
                log.info(f'配信時間帯ではありません (枠: {windows_label()})')
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