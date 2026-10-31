# Slack アプリ（Bot）連携 設定ガイド

<details>
<summary>目次 / 概要</summary>

- [1. 連携の目的・趣旨](#1-連携の目的趣旨)
- [2. 必要な最小要件](#2-必要な最小要件)
- [3. 設定方法 A: マニフェスト利用 (推奨・最短)](#3-設定方法-a-マニフェスト利用-推奨最短)
- [4. 設定方法 B: 手動作成 (Blank app)](#4-設定方法-b-手動作成-blank-app)
- [5. チャンネルへの Bot 招待](#5-チャンネルへの-bot-招待)
- [6. config.json への設定](#6-configjson-への設定)
- [📝 変更履歴](#-変更履歴)
</details>

本ドキュメントは、Thunderbird の新着メールを Slack に要約通知し、スレッド維持を行うための Slack App（Bot）のセットアップガイドです。  
Slack の UI は頻繁に更新されますが、**「何のために何が必要か」の趣旨** を把握していれば、どのような UI でも迷わず設定できます。

---

## 1. 連携の目的・趣旨

| 機能 | 目的・必要となる理由 |
| :--- | :--- |
| **新着要約の通知** | Thunderbird が検知したメールの AI 要約を Slack チャンネルに投稿するため。 |
| **スレッド維持** | 返信メール（`In-Reply-To`）を受信した際、Slack 上でも親メッセージのスレッド内に返信としてぶら下げ、やり取りの文脈を保つため。 |

> [!NOTE]
> Incoming Webhook 単体では「初回投稿時のメッセージID（`ts`）」を確実に取得できず、スレッド維持が制限される場合があります。  
> そのため、本ツールでは **Slack Bot Token（`xoxb-...`）** の利用を標準推奨としています（無料プランで完全利用可能）。

---

## 2. 必要な最小要件

Slack 側で作成するアプリに求められるのは以下の **1 点のみ** です：

- **Bot Token Scopes (権限)**:
  - **`chat:write`**（メッセージ投稿およびスレッド返信を送信する権限）

取得する値：
- **`Bot User OAuth Token`**（`xoxb-` で始まるトークン文字列）
- **`App-Level Token`**（`xapp-` で始まるトークン文字列 ※ボタン操作や遠隔対話を利用する場合）

---

## 3. 設定方法 A: マニフェスト利用 (推奨・最短)

UI の変化に最も影響されず、1クリックで権限設定まで完了する方法です。

1. [api.slack.com/apps](https://api.slack.com/apps) を開き、**「Create new app」** をクリック。
2. **「From a manifest」** を選択し、通知先のワークスペースを選びます。
3. 以下の YAML を貼り付けて「Next」➔「Create」をクリック：

```yaml
display_information:
  name: AI Mailer
  description: Thunderbird mail notification & thread-aware summary bot
features:
  bot_user:
    display_name: AI Mailer
    always_online: true
oauth_config:
  scopes:
    bot:
      - chat:write
      - app_mentions:read
settings:
  interactivity:
    is_enabled: true
  socket_mode_enabled: true
  event_subscriptions:
    bot_events:
      - app_mention
```

4. 左メニュー「**Install App**」から「**Install to Workspace**」をクリックして許可。
5. 表示された **`Bot User OAuth Token` (`xoxb-...`)** をコピーします。
6. 左メニュー「**Basic Information**」➔「**App-Level Tokens**」で「**Generate Token and Scopes**」をクリック。
   - Token Name: `socket-mode`
   - Scope: **`connections:write`** を追加して生成
   - 発行された **`App-Level Token` (`xapp-...`)** をコピーします。

---

## 4. 設定方法 B: 手動作成 (Blank app)

1. [api.slack.com/apps](https://api.slack.com/apps) で **「Blank app」**（旧: From scratch）を選択。
2. アプリ名（例: `AI Mailer`）とワークスペースを入力して作成。
3. 左メニュー **「OAuth & Permissions」** を開く。
4. 「Scopes」➔「Bot Token Scopes」で **`chat:write`** を追加。
5. ページ上部の **「Install to Workspace」** をクリックして許可。
6. 発行された **`Bot User OAuth Token` (`xoxb-...`)** をコピー。

---

## 5. チャンネルへの Bot 招待

Slack アプリを開き、通知を受け取りたいチャンネル（例: `#general` や `#mail-notifications`）で以下を入力して Bot を参加させます：

```text
/invite @AI Mailer
```

---

## 6. config.json への設定

取得したトークンとチャンネル名を `config.json` に設定します：

```json
{
  "slack": {
    "bot_token": "xoxb-あなたのボットトークン",
    "app_token": "xapp-あなたのアプリートークン",
    "channel": "#mail-notifications",
    "use_threads": true
  }
}
```

---

## 📝 変更履歴

| 日付 | バージョン | 変更内容 |
| :--- | :--- | :--- |
| 2026-10-31 | v0.1.0 | 初版作成（最新 UI 対応: Blank app, From a manifest による趣旨別ガイド） |
