#!/usr/bin/env python3
"""
antigravity-mail-management: Slack Socket Mode Listener
Issue #1: Slack スレッド内での遠隔対話 (AI 返信案・質問応答)
Issue #13: Slack 3種ボタン & Thunderbird 既読/スパム双方向同期
- ポート開放不要・サーバー契約不要（ローカル常駐の WebSocket / Socket Mode 接続）
"""

import os
import re
import sys
import time
import threading
import json
import sqlite3
import email
import ssl
import certifi
import requests
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.web.client import WebClient

from mail_scanner import (
    CONFIG_PATH,
    get_db_connection,
    mark_as_read_local,
    move_to_junk_local,
    extract_body_and_attachments,
    decode_mime_words,
    strip_quotes,
    scan_emails,
    parse_thunderbird_prefs
)

def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"⚠️ 設定ファイルが見つかりません: {CONFIG_PATH}", file=sys.stderr)
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def get_mail_content_by_message_id(db_file, message_id):
    """DB からメールファイルパスを取得し、メールの件名・差出人・本文をパース"""
    conn = get_db_connection(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT file_path, target_account FROM email_state WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    conn.close()

    if not row or not row["file_path"]:
        return None, None, None, None

    file_path = row["file_path"]
    if not os.path.exists(file_path):
        return None, None, None, file_path

    try:
        with open(file_path, "rb") as f:
            msg = email.message_from_binary_file(f)
        subject = decode_mime_words(msg.get("Subject", "(件名なし)"))
        sender = decode_mime_words(msg.get("From", "(不明)"))
        data_dir = os.path.dirname(os.path.abspath(db_file))
        body, _ = extract_body_and_attachments(msg, message_id, data_dir)
        return subject, sender, body, file_path
    except Exception as e:
        print(f"⚠️ メール解析エラー ({file_path}): {e}", file=sys.stderr)
        return None, None, None, file_path

def get_message_id_by_slack_ts(db_file, slack_ts):
    """Slack ts から message_id を取得"""
    conn = get_db_connection(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT message_id FROM email_state WHERE slack_ts = ?", (slack_ts,))
    row = cursor.fetchone()
    conn.close()
    return row["message_id"] if row else None

def generate_ai_reply(prompt, mail_context, config):
    """Gemini API 等を使って返信案や回答を生成"""
    api_key = config.get("ai", {}).get("api_key")
    model_name = config.get("ai", {}).get("model", "gemini-3.8-flash")

    if not api_key or "YOUR_GEMINI_API_KEY" in api_key:
        return f"💡 [ローカル返信モック]\nご指示「{prompt}」を承りました。\nGemini APIキーを設定すると、AIによる高精度な自動返信案・回答がここに生成されます。"

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        system_instruction = (
            "あなたは優秀なビジネスメールAIアシスタントです。\n"
            "対象メールの文脈を踏まえ、ユーザーからの指示に対して丁寧かつ的確なビジネス返信案や回答を作成してください。"
        )
        full_content = (
            f"{system_instruction}\n\n"
            f"--- 【対象メール情報】 ---\n{mail_context}\n\n"
            f"--- 【ユーザーからの指示】 ---\n{prompt}"
        )
        payload = {
            "contents": [{"parts": [{"text": full_content}]}]
        }
        res = requests.post(url, json=payload, timeout=30)
        res.raise_for_status()
        data = res.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        return f"⚠️ AI 生成エラー: {e}"

def create_slack_app(config, check_auth=True):
    bot_token = config.get("slack", {}).get("bot_token")
    if not bot_token or bot_token.startswith("xoxb-your"):
        print("⚠️ 有効な Slack Bot Token (xoxb-...) が設定されていません。", file=sys.stderr)
        return None

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    client = WebClient(token=bot_token, ssl=ssl_ctx)
    app = App(client=client, token_verification_enabled=check_auth)
    db_file = os.environ.get("DB_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), config.get("db_file", "mail_history.db")))

    # 1. [📄 全文を展開] ボタン
    @app.action("expand_full_body")
    def handle_expand_full_body(ack, body, client):
        ack()
        message_id = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        thread_ts = body["message"]["ts"]

        subject, sender, mail_body, file_path = get_mail_content_by_message_id(db_file, message_id)
        if not mail_body:
            client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="⚠️ メール本文を取得できませんでした。")
            return

        clean_body = strip_quotes(mail_body)
        display_body = clean_body if len(clean_body) <= 3000 else clean_body[:3000] + "\n\n... (長文のため一部省略)"
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"📄 *メール全文プレビュー:*\n```{display_body}```"
        )

    # 2. [🤖 Antigravityで開く] ボタン
    @app.action("antigravity_open")
    def handle_antigravity_open(ack, body, client):
        ack()
        message_id = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        thread_ts = body["message"]["ts"]

        _, _, _, file_path = get_mail_content_by_message_id(db_file, message_id)
        info_text = (
            f"🤖 *Antigravity 連携情報:*\n"
            f"• *Message-ID*: `{message_id}`\n"
            f"• *ローカルファイル*: `{file_path or '不明'}`\n\n"
            f"Antigravity のチャットで以下のように入力すると、このメールの深掘りや返信作成が可能です：\n"
            f"> `このメール（{message_id}）について返信案を作って`"
        )
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=info_text)

    # 3. [✅ 既読にする] ボタン
    @app.action("mark_as_read")
    def handle_mark_as_read(ack, body, client):
        ack()
        message_id = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        msg_ts = body["message"]["ts"]

        conn = get_db_connection(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM email_state WHERE message_id = ?", (message_id,))
        row = cursor.fetchone()

        success = False
        if row and row["file_path"]:
            success = mark_as_read_local(row["file_path"])

        if success:
            with conn:
                conn.execute("UPDATE email_state SET replied = 1 WHERE message_id = ?", (message_id,))
            conn.close()

            # ボタンの表示を「✅ 既読済み」に更新
            blocks = body["message"].get("blocks", [])
            for b in blocks:
                if b.get("type") == "actions":
                    for elem in b.get("elements", []):
                        if elem.get("action_id") == "mark_as_read":
                            elem["text"]["text"] = "✅ 既読済み"
                            elem["style"] = "primary"
            try:
                client.chat_update(channel=channel, ts=msg_ts, blocks=blocks, text=body["message"].get("text", ""))
                client.chat_postMessage(channel=channel, thread_ts=msg_ts, text="✅ Thunderbird 上で既読に設定しました。")
            except Exception as e:
                print(f"UI更新エラー: {e}", file=sys.stderr)
        else:
            conn.close()
            client.chat_postMessage(channel=channel, thread_ts=msg_ts, text="⚠️ ファイルの既読化に失敗しました。")

    # 4. [🚫 スパムに登録] ボタン
    @app.action("mark_as_spam")
    def handle_mark_as_spam(ack, body, client):
        ack()
        message_id = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        msg_ts = body["message"]["ts"]

        conn = get_db_connection(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM email_state WHERE message_id = ?", (message_id,))
        row = cursor.fetchone()

        success = False
        if row and row["file_path"]:
            success, _ = move_to_junk_local(row["file_path"])

        conn.close()
        if success:
            client.chat_postMessage(channel=channel, thread_ts=msg_ts, text="🚫 メールを Thunderbird の Junk フォルダへ移動しました。")
        else:
            client.chat_postMessage(channel=channel, thread_ts=msg_ts, text="⚠️ スパムフォルダへの移動に失敗しました。")

    # 5. [✅ 返信済みにする] ボタン
    @app.action("mark_as_replied")
    def handle_mark_as_replied(ack, body, client):
        ack()
        message_id = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        thread_ts = body["message"].get("thread_ts") or body["message"]["ts"]

        conn = get_db_connection(db_file)
        with conn:
            conn.execute("UPDATE email_state SET replied = 1 WHERE message_id = ?", (message_id,))
        conn.close()

        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="✅ ステータスを【返信対応済み】に更新しました。")

    # 6. [🤖 返信案を作成] ボタン
    @app.action("draft_reply")
    def handle_draft_reply(ack, body, client):
        ack()
        message_id = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        thread_ts = body["message"].get("thread_ts") or body["message"]["ts"]

        subject, sender, mail_body, _ = get_mail_content_by_message_id(db_file, message_id)
        mail_context = f"件名: {subject}\n差出人: {sender}\n本文:\n{strip_quotes(mail_body or '')[:2000]}"
        prompt = "このメールに対する丁寧で簡潔な承諾・返信案を作成してください。"

        reply_text = generate_ai_reply(prompt, mail_context, config)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"✉️ *AI 返信案:*\n```\n{reply_text}\n```"
        )

    # 7. 遠隔対話: メンション (@AI Mailer ...) または スレッド内返信 (Issue #1)
    @app.event("app_mention")
    def handle_mention(event, client):
        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        text = event.get("text", "")

        # メンション部分 (<@Uxxxx>) を除去
        user_prompt = re.sub(r'<@[\w]+>', '', text).strip()
        if not user_prompt:
            user_prompt = "このメールの内容について要約や次のアクションを教えてください。"

        # 親メッセージの ts からメール情報を特定
        message_id = get_message_id_by_slack_ts(db_file, thread_ts)
        mail_context = "（対象メールのコンテキストなし）"
        if message_id:
            subject, sender, mail_body, _ = get_mail_content_by_message_id(db_file, message_id)
            mail_context = f"件名: {subject}\n差出人: {sender}\n本文:\n{strip_quotes(mail_body or '')[:2500]}"

        ai_response = generate_ai_reply(user_prompt, mail_context, config)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"🤖 *Antigravity AI:*\n{ai_response}"
        )

    return app

def start_periodic_scanner(config):
    """リスナー稼働中にバックグラウンドで定期的にメールスキャン＆Slack転送を実行"""
    profile_path = os.environ.get("THUNDERBIRD_CONTAINER_PATH") or ("/data/thunderbird" if os.path.exists("/data/thunderbird") else config.get("thunderbird", {}).get("profile_path"))
    _, intervals = parse_thunderbird_prefs(profile_path)
    # 同期間隔: config に秒数指定があれば優先、なければ prefs.js の分単位、デフォルトは 60 秒
    interval_sec = config.get("sync_interval_seconds") or (intervals[0] * 60 if intervals else 60)

    def loop():
        print(f"⏱️ 【自動巡回】{interval_sec}秒ごとに新着メールを自動チェック＆Slack転送します。")
        while True:
            try:
                time.sleep(interval_sec)
                scan_emails(config, dry_run=False)
            except Exception as e:
                print(f"⚠️ 自動巡回スキャンエラー: {e}", file=sys.stderr)

    t = threading.Thread(target=loop, daemon=True)
    t.start()

def main():
    config = load_config()
    app_token = config.get("slack", {}).get("app_token") or os.environ.get("SLACK_APP_TOKEN")

    if not app_token or app_token.startswith("xapp-your"):
        print("=" * 60)
        print("⚠️ Slack App-Level Token (xapp-...) が設定されていません。")
        print("Socket Mode を利用するには、Slack アプリ設定で以下が必要です：")
        print("1. api.slack.com/apps で Socket Mode を有効化 (Enable Socket Mode)")
        print("2. 'App-Level Tokens' で 'connections:write' スコープを持つトークンを発行")
        print("3. config.json の slack.app_token に 'xapp-...' を設定")
        print("=" * 60)
        sys.exit(1)

    app = create_slack_app(config)
    if not app:
        sys.exit(1)

    # バックグラウンドでの自動巡回スレッドを起動
    start_periodic_scanner(config)

    print("🚀 Slack Socket Mode リスナーを起動中（完全ローカル・ポート開放不要）...")
    handler = SocketModeHandler(app, app_token)
    try:
        handler.start()
    except KeyboardInterrupt:
        print("\n🛑 リスナーを終了しました。")

if __name__ == "__main__":
    main()
