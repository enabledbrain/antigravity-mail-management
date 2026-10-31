---
name: ai-mailer-setup
description: AI Mailer (antigravity-mail-management) の初期導入・環境自動診断・対話型セットアップスキル
---

# AI Mailer Setup Skill

このスキルは、新規ユーザーが本リポジトリを開いて「セットアップして」「初期設定をして」と指示した際に、AI エージェントが自律的かつ安全に環境構築・初期化を進行するためのプロトコルです。

---

## 🎯 セットアップ手順

### ステップ 1: OS と Thunderbird プロファイルの自動探索
1. ホスト OS（macOS または Windows）を判定します。
2. 以下の標準パスを探索し、Thunderbird のプロファイルディレクトリを特定します：
   - **macOS**:
     - `~/Library/Thunderbird/Profiles/`
     - `~/Library/CloudStorage/Dropbox/appData/Thunderbird/Profiles/` (Dropbox同期環境)
     - `~/Library/Application Support/Thunderbird/Profiles/`
   - **Windows**:
     - `%APPDATA%\Thunderbird\Profiles\`
     - `C:\Users\%USERNAME%\Dropbox\appData\Thunderbird\Profiles\`
3. 見つかったプロファイル（直近で更新されている `.default` または `.default-release` ディレクトリ）をユーザーに提示し、「このプロファイルを使用しますか？」と確認します。

### ステップ 2: 環境設定ファイル (`.env`, `config.json`) の生成
1. `.env.example` から `.env` を作成し、ステップ1で確定した `THUNDERBIRD_PROFILE_PATH` を書き込みます。
2. `config.example.json` から `config.json` を作成します。
3. ユーザーに以下をヒアリングします：
   - Slack 通知先の Webhook URL（または Bot Token）
   - （任意）Gemini API キー（Antigravity 連携時は不要であることを案内）

### ステップ 3: Docker コンテナの起動確認 ＆ 初回キャッチアップ
1. Docker Desktop が起動しているか確認します（`docker info`）。
2. 初回大量メールによる Slack 誤通知を防ぐため、**必ず `--init-catchup` を実行**します：
   - macOS / Linux: `./run.sh --init-catchup`
   - Windows: `run.bat --init-catchup`
3. 過去メールが SQLite および Meilisearch に高速インデックス登録されたことを確認します。

### ステップ 4: 動作検証（ドライラン）
1. `./run.sh --dry-run` を実行し、新着メールスキャンがエラーなく完了することを確認します。
2. ユーザーに「セットアップが完了しました。30分ごとの定期巡回を開始しますか？」と案内します。
