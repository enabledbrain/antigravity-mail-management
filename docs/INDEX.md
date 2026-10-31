# ドキュメント総合インデックス (docs/INDEX.md)

<details>
<summary>目次 / 概要</summary>

- [1. ドキュメント一覧](#1-ドキュメント一覧)
- [2. 仕様・ガイドライン](#2-仕様ガイドライン)
- [📝 変更履歴](#-変更履歴)
</details>

本リポジトリ内の各種設計書・ガイドライン・運用の総合インデックスです。  
ドキュメントを探索する際は、まず本ファイルを参照して目的のドキュメントを特定してください。

---

## 1. ドキュメント一覧

| ドキュメント | 概要 | 最終更新日 | キーワード |
| :--- | :--- | :---: | :--- |
| [**`README.md`**](../README.md) | プロジェクト概要、クイックスタート、基本機能、ロードマップ | 2026-10-31 | <details><summary>一覧</summary>Docker, Meilisearch, SQLite, Thunderbird, Slack</details> |
| [**`docs/slack-setup.md`**](slack-setup.md) | Slack アプリ（Bot）連携の趣旨・必要要件・作成手順ガイド | 2026-10-31 | <details><summary>一覧</summary>Slack Bot, chat:write, Manifest, スレッド維持</details> |
| [**`docs/agents/README.md`**](agents/README.md) | AI エージェント別（Antigravity, Claude Code, Cursor, Copilot）設定・運用比較ガイド | 2026-10-31 | <details><summary>一覧</summary>Antigravity, Claude Code, Cursor, Windsurf, Copilot, スケジューラ</details> |
| [**`AGENTS.md`**](../AGENTS.md) | 自律型 AI エージェント向け共通コアガードレール＆運用指示書 | 2026-10-31 | <details><summary>一覧</summary>マルチエージェント, ガードレール, セキュリティ, 対称同期</details> |
| [**`.agent/skills/ai-mailer-setup/SKILL.md`**](../.agent/skills/ai-mailer-setup/SKILL.md) | AI による対話型セットアップ・環境自動診断スキル | 2026-10-31 | <details><summary>一覧</summary>オンボーディング, 自動診断, プロファイル探索, 初期化</details> |
| [**`rules/summary_rules.md`**](../rules/summary_rules.md) | 利用者向け AI 要約・翻訳・重要度判定のカスタマイズルール設定書 | 2026-10-31 | <details><summary>一覧</summary>要約ルール, 翻訳, フィルタ, User Override</details> |

---

## 2. 仕様・ガイドライン
今後追加される機能仕様（Issue #1〜#10）の詳細設計は、順次 `docs/` 配下にドキュメントとして整備・リンクされます。

---

## 📝 変更履歴

| 日付 | バージョン | 変更内容 |
| :--- | :--- | :--- |
| 2026-10-31 | v0.1.0 | 初版作成（総合ドキュメントインデックス整備） |
