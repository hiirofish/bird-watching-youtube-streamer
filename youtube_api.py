"""YouTube Live Streaming API ヘルパー"""
import json
import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TOKEN_FILE = os.path.join(os.path.dirname(__file__), 'credentials/token.json')

def get_youtube_service():
    """認証済みYouTubeサービスを取得（トークン自動更新）"""
    with open(TOKEN_FILE) as f:
        token_data = json.load(f)
    creds = Credentials(
        token=token_data['token'],
        refresh_token=token_data['refresh_token'],
        token_uri=token_data['token_uri'],
        client_id=token_data['client_id'],
        client_secret=token_data['client_secret'],
        scopes=token_data['scopes']
    )
    if creds.expired:
        creds.refresh(Request())
        with open(TOKEN_FILE, 'w') as f:
            json.dump({
                'token': creds.token,
                'refresh_token': creds.refresh_token,
                'token_uri': creds.token_uri,
                'client_id': creds.client_id,
                'client_secret': creds.client_secret,
                'scopes': list(creds.scopes)
            }, f)
    return build('youtube', 'v3', credentials=creds)

def get_or_create_stream(youtube):
    """Default stream keyを取得"""
    res = youtube.liveStreams().list(part='snippet,cdn', mine=True).execute()
    for stream in res.get('items', []):
        if 'Default' in stream['snippet'].get('title', ''):
            return stream
    if res.get('items'):
        return res['items'][0]
    raise Exception('ストリームキーが見つかりません')

def create_broadcast(youtube, config_file=None):
    """Broadcastを作成（設定ファイルから読み込み）"""
    from datetime import datetime, timedelta, timezone
    import json as _json

    # 設定読み込み
    cfg = {'title': 'ツバメ配信', 'description': '', 'category_id': '15', 'privacy': 'public'}
    if config_file and os.path.exists(config_file):
        with open(config_file) as f:
            cfg.update(_json.load(f))

    broadcast = youtube.liveBroadcasts().insert(
        part='snippet,status,contentDetails',
        body={
            'snippet': {
                'title': cfg['title'],
                'description': cfg.get('description', ''),
                'scheduledStartTime': (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
                'defaultLanguage': cfg.get('language', 'ja'),
            },
            'status': {
                'privacyStatus': cfg.get('privacy', 'public'),
                'selfDeclaredMadeForKids': False,
            },
            'contentDetails': {
                'enableAutoStart': True,
                'enableAutoStop': False,
                'categoryId': cfg.get('category_id', '15'),
            }
        }
    ).execute()
    return broadcast

def bind_broadcast_to_stream(youtube, broadcast_id, stream_id):
    """BroadcastとStreamを紐付け"""
    youtube.liveBroadcasts().bind(
        part='id,contentDetails',
        id=broadcast_id,
        streamId=stream_id
    ).execute()

def end_broadcast(youtube, broadcast_id):
    """Broadcastを確実に終了（ready状態は削除）"""
    try:
        res = youtube.liveBroadcasts().list(part='status', id=broadcast_id).execute()
        items = res.get('items', [])
        if not items:
            return
        status = items[0]['status']['lifeCycleStatus']
        if status in ('live', 'testing'):
            youtube.liveBroadcasts().transition(
                broadcastStatus='complete', id=broadcast_id, part='id,status'
            ).execute()
        elif status == 'ready':
            youtube.liveBroadcasts().delete(id=broadcast_id).execute()
    except Exception as e:
        print(f'Broadcast終了エラー ({broadcast_id}): {e}')

def cleanup_orphans(youtube, keep_id=None):
    """Free the stream key: complete any active broadcasts, delete unused ones.

    This is the key fix for the "orphan broadcast holds the shared stream key"
    problem. Active (live/testing/liveStarting) leftovers are transitioned to
    complete; not-yet-started (created/ready) leftovers are deleted. Already
    complete/revoked broadcasts are left untouched.
    """
    try:
        res = youtube.liveBroadcasts().list(
            part='id,status', mine=True, maxResults=50
        ).execute()
    except Exception as e:
        print(f'cleanup_orphans list error: {e}')
        return
    for b in res.get('items', []):
        bid = b['id']
        if keep_id and bid == keep_id:
            continue
        status = b['status']['lifeCycleStatus']
        try:
            if status in ('live', 'testing', 'liveStarting'):
                youtube.liveBroadcasts().transition(
                    broadcastStatus='complete', id=bid, part='id,status'
                ).execute()
                print(f'cleanup: completed {bid} ({status})')
            elif status in ('created', 'ready'):
                youtube.liveBroadcasts().delete(id=bid).execute()
                print(f'cleanup: deleted {bid} ({status})')
        except Exception as e:
            print(f'cleanup {bid} ({status}) error: {e}')

def get_stream_key(stream):
    """StreamオブジェクトからRTMP URLとキーを取得"""
    cdn = stream['cdn']['ingestionInfo']
    return cdn['ingestionAddress'], cdn['streamName']

# --- テスト用 ---
if __name__ == '__main__':
    yt = get_youtube_service()

    # ストリーム取得
    stream = get_or_create_stream(yt)
    rtmp_url, stream_key = get_stream_key(stream)
    print(f'RTMP URL: {rtmp_url}')
    print(f'Stream Key: {stream_key[:10]}...')

    # テストBroadcast作成 → 紐付け → 削除
    bc = create_broadcast(yt, 'API統合テスト', privacy='unlisted')
    print(f'Broadcast作成: {bc["id"]}')
    bind_broadcast_to_stream(yt, bc['id'], stream['id'])
    print('紐付け完了')
    yt.liveBroadcasts().delete(id=bc['id']).execute()
    print('削除完了 - youtube_api.py 正常動作!')