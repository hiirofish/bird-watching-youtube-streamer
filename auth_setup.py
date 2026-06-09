"""初回認証スクリプト - 1回だけ実行してトークンを保存"""
import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = ['https://www.googleapis.com/auth/youtube']
CLIENT_SECRET = 'credentials/client_secret.json'
TOKEN_FILE = 'credentials/token.json'

def main():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=8090, open_browser=False)

        with open(TOKEN_FILE, 'w') as f:
            json.dump({
                'token': creds.token,
                'refresh_token': creds.refresh_token,
                'token_uri': creds.token_uri,
                'client_id': creds.client_id,
                'client_secret': creds.client_secret,
                'scopes': list(creds.scopes)
            }, f)
        print(f'トークン保存完了: {TOKEN_FILE}')
    else:
        print('既に有効なトークンがあります')

if __name__ == '__main__':
    main()
