# Multi-AI Agent Global Instructions (AGENTS.md)

このドキュメントは、**Antigravity, Cursor, Claude Code, GitHub Copilot, Codex 等** の自律型 AI エージェントが本リポジトリで作業する際の共通ガードレールおよび運用指示書です。

---

## 🎯 リポジトリ概要
`antigravity-mail-management` は、Thunderbird（POP3 / Maildir 形式）の新着メールを自動検知し、AI による要約・Slack へのスレッド維持通知・超高速日本語全文検索（Meilisearch）を提供する自動化パッケージです。

---

## 🛡️ ガードレール & セキュリティ規約
1. **秘密情報・クレデンシャルの保護**:
   - `config.json`, `.env`, API キー、Webhook URL、パスワード、および実メールデータ（`data/` 配下）を Git にコミットしてはなりません（STRICTLY FORBIDDEN）。
2. **個人情報・端末環境の保護**:
   - サンプルファイルやドキュメントに実在の個人名、個人メールアドレス、固有の端末絶対パスを混入させないこと。常にプレースホルダーを使用すること。
3. **ブランチ運用**:
   - `main` ブランチへの直接プッシュは避け、GitHub Issue に紐づくトピックブランチ（`feat/#<Issue番号>-...`, `fix/#<Issue番号>-...`）を作成して PR を作成すること。

---

## 🤖 AI アシスタント向け運用サポート指示
ユーザーから「セットアップして」「初期化して」「メールチェックして」「過去メールを検索して」と指示された場合、AI は以下の手順でサポートしてください：

### 1. 初回セットアップの支援
- OS（macOS / Windows）を判別し、Thunderbird のプロファイルディレクトリ候補を探索・提示して `.env` を作成。
- `config.example.json` を `config.json` にコピーし、Slack Webhook/Bot Token の設定をユーザーに案内。
- `run.sh --init-catchup`（Windows: `run.bat --init-catchup`）を実行して、過去メールを既読登録＆Meilisearch にインデックス化。

### 2. 定期実行＆メール検索の支援
- 30分間隔の定期巡回リクエストに対しては、バックグラウンドスケジューラを稼働。
- 「〇〇に関するメールを検索して」という指示に対しては、`./run.sh --search "..."` を自律実行して結果を分かりやすく要約して回答。
