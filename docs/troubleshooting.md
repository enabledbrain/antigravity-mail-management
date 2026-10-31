# トラブルシューティングガイド (Troubleshooting)

<details>
<summary>目次</summary>

- [1. スクリプト実行時の「Operation not permitted」エラー](#1-スクリプト実行時のoperation-not-permittedエラー)
- [2. Slack Socket Mode リスナーが起動しない (App Token エラー)](#2-slack-socket-mode-リスナーが起動しない-app-token-エラー)
- [3. メールの本文要約が「簡易抜粋」になってしまう](#3-メールの本文要約が簡易抜粋になってしまう)
- [4. SQLite DB のカラム不足エラー (table email_state has no column...)](#4-sqlite-db-のカラム不足エラー-table-email_state-has-no-column)
</details>

---

## 1. スクリプト実行時の「Operation not permitted」エラー

### 症状
macOS 端末で `./run.sh` や `bash run.sh` を実行した際に、以下のようなエラーが表示されて実行できない：

```bash
bash: ./run.sh: Operation not permitted
# または
xattr: [Errno 1] Operation not permitted: 'run.sh'
```

### 原因
本リポジトリが **Google Drive や Dropbox などのクラウド同期フォルダ（`~/Library/CloudStorage/...`）配下** に置かれている場合、macOS のセキュリティ機能（TCC / Gatekeeper / SIP）がターミナルアプリによるスクリプトの直接実行や属性変更を自動的にブロックします。

### 解決策 A: 直接 Docker コマンドを実行する（推奨・即効）
スクリプトファイルを経由せず、直接 Docker コマンドを実行することで、macOS のアクセス制限を完全に回避できます：

```bash
# ① 通常の新着スキャン＆通知
docker compose run --rm ai-mailer

# ② ドライラン（テスト実行）
docker compose run --rm ai-mailer --dry-run

# ③ Slack Socket Mode リスナーの起動
docker compose run --rm --entrypoint python3 ai-mailer slack_listener.py

# ④ 全文検索
docker compose run --rm ai-mailer --search "キーワード"
```

### 解決策 B: ターミナルに「フルディスクアクセス」を付与する（根本解決）
今後 `./run.sh` を直接ターミナルから叩けるようにする場合の設定手順です：
1. Mac の **「システム設定」** ➔ **「プライバシーとセキュリティ」** を開く
2. **「フルディスクアクセス」** をクリック
3. 一覧にある **「ターミナル」**（またはお使いの `iTerm` 等）のトグルを **ON** にする  
   *(一覧にない場合は下部の `+` ボタンから `/Applications/Utilities/Terminal.app` を追加)*
4. ターミナルを再起動する

---

## 2. Slack Socket Mode リスナーが起動しない (App Token エラー)

### 症状
`./run.sh --listen` を実行した際に「`⚠️ Slack App-Level Token (xapp-...) が設定されていません。`」と表示されて停止する。

### 原因
Slack の双方向ボタン操作や遠隔対話（Socket Mode）には、通常の Bot Token（`xoxb-...`）に加えて、WebSocket 接続用の **App-Level Token（`xapp-...`）** が必要です。

### 解決策
1. [api.slack.com/apps](https://api.slack.com/apps) を開く
2. 対象の Slack アプリを選択し、左メニュー **「Basic Information」** を開く
3. 「**App-Level Tokens**」セクションで「**Generate Token and Scopes**」をクリック
   - Token Name: `socket-mode`
   - Scope: **`connections:write`** を追加して生成
4. 発行された **`xapp-...`** をコピーし、`config.json` に設定する：
   ```json
   "slack": {
     "bot_token": "xoxb-...",
     "app_token": "xapp-発行したトークン",
     "channel": "C0CEKLN2XGQ",
     "use_threads": true
   }
   ```

---

## 3. メールの本文要約が「簡易抜粋」になってしまう

### 症状
Slack 通知は届くが、AI 要約がメール冒頭の挨拶（「いつもお世話になっております」）やフォーム項目名の切り貼りのみになっており、用件が要約されない。

### 原因
`config.json` の `ai.api_key` が空（未設定）のため、API コストを発生させない安全策としての「簡易フォールバック（先頭行抜粋）」が動作しています。

### 解決策
Google AI Studio（無料）で Gemini API キーを取得し、`config.json` に設定してください：
1. [aistudio.google.com](https://aistudio.google.com/) にアクセスし、Google アカウントでログイン
2. 「**Get API key**」➔「**Create API key**」をクリックしてキー（`AIzaSy...`）を発行
3. `config.json` に設定：
   ```json
   "ai": {
     "provider": "gemini",
     "api_key": "AIzaSyあなたのキー",
     "model": "gemini-2.5-flash"
   }
   ```
*(Gemini 2.5 Flash は無料枠の範囲内で十分に高速・高精度に動作します)*

---

## 4. SQLite DB のカラム不足エラー (table email_state has no column...)

### 症状
`table email_state has no column named file_path` などのエラーが発生する。

### 解決策
最新バージョンの `mail_scanner.py` では、データベース接続時に自動マイグレーション（`ALTER TABLE email_state ADD COLUMN ...`）が実行されるため、Git の最新コードを pull して Docker イメージを再ビルドすることで自動解決します：
```bash
git pull origin main
docker compose build
```
