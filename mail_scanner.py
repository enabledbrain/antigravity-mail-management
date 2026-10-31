#!/usr/bin/env python3
"""
antigravity-mail-management
Thunderbird Maildir Scanner, Meilisearch Engine & Slack Notifier
- SQLite: 既読フラグとスレッド追跡のみ（数MBの極小維持で絶対に肥大化しない）
- Meilisearch: 日本語の超高速全文検索＆日付範囲フィルター（ホスト側永続化）
- Slack: スレッド維持＆AI要約通知
- Rules: rules/summary_rules.md による自然言語カスタマイズ (User Override)
"""

import os
import sys
import json
import sqlite3
import hashlib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import argparse
import requests
from datetime import datetime

CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
CONFIG_EXAMPLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.example.json")
MEILI_URL = os.environ.get("MEILI_URL", "http://search-engine:7700")
RULES_PATH = os.environ.get("RULES_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", "summary_rules.md"))

def get_db_connection(db_path):
    """SQLite 接続を取得（軽量管理テーブルのみ作成）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_state (
                message_id TEXT PRIMARY KEY,
                parent_id TEXT,
                slack_ts TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_parent_id ON email_state(parent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_slack_ts ON email_state(slack_ts)")
    return conn

def init_meilisearch(meili_url):
    """Meilisearch のインデックスおよびフィルター/ソート属性の初期化"""
    index_url = f"{meili_url}/indexes/emails"
    try:
        requests.post(f"{meili_url}/indexes", json={"uid": "emails", "primaryKey": "id"}, timeout=5)
        requests.put(f"{index_url}/settings/filterable-attributes", 
                     json=["timestamp", "date_iso", "sender", "subject", "message_id"], timeout=5)
        requests.put(f"{index_url}/settings/sortable-attributes", 
                     json=["timestamp", "date_iso"], timeout=5)
    except Exception as e:
        print(f"⚠️ Meilisearch 初期化警告: {e}", file=sys.stderr)

def get_doc_id(message_id):
    """Meilisearch 用の安全なドキュメント ID（ハッシュ）を生成"""
    return hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:32]

def parse_date_to_iso_and_timestamp(date_str):
    """メールの Date ヘッダー文字列を ISO形式 と UNIXタイムスタンプ に変換"""
    if not date_str:
        return "", 0
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S"), int(dt.timestamp())
    except Exception:
        return "", 0

def decode_mime_words(s):
    """MIME エンコードされたヘッダー文字列を UTF-8 に復元"""
    if not s:
        return ""
    decoded_fragments = decode_header(s)
    result = []
    for fragment, charset in decoded_fragments:
        if isinstance(fragment, bytes):
            charset = charset or "utf-8"
            try:
                result.append(fragment.decode(charset, errors="replace"))
            except (LookupError, UnicodeDecodeError):
                result.append(fragment.decode("utf-8", errors="replace"))
        else:
            result.append(str(fragment))
    return "".join(result)

def extract_body(msg):
    """email.message.Message から本文（text/plain 優先）を抽出"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except Exception:
                    return payload.decode("utf-8", errors="replace")
            elif content_type == "text/html" and not body and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                try:
                    body = payload.decode(charset, errors="replace")
                except Exception:
                    body = payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        try:
            body = payload.decode(charset, errors="replace")
        except Exception:
            body = payload.decode("utf-8", errors="replace")
    return body

def get_user_rules():
    """rules/summary_rules.md が存在する場合にその内容を読み込み"""
    if os.path.exists(RULES_PATH):
        try:
            with open(RULES_PATH, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def generate_summary(text, config):
    """LLM API (Gemini 等) またはフォールバックで要約を生成"""
    api_key = config.get("ai", {}).get("api_key")
    if not api_key or "YOUR_GEMINI_API_KEY" in api_key:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        snippet = "\n".join(lines[:6])
        return f"【簡易抜粋（APIキー未設定 / Antigravity連携用）】\n{snippet}..."

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config['ai'].get('model', 'gemini-2.5-flash')}:generateContent?key={api_key}"
        base_prompt = config["ai"].get("prompt_summary", "要約してください。")
        user_rules = get_user_rules()

        prompt = base_prompt
        if user_rules:
            prompt += f"\n\n【ユーザー個別適用ルール】\n{user_rules}"

        payload = {
            "contents": [{
                "parts": [{"text": f"{prompt}\n\n--- メール本文 ---\n{text[:4000]}"}]
            }]
        }
        res = requests.post(url, json=payload, timeout=30)
        res.raise_for_status()
        data = res.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        return f"（要約エラー: {e}）\n{text[:200]}..."

def find_parent_slack_ts(conn, in_reply_to, references):
    """In-Reply-To または References から過去の Slack ts を検索"""
    candidate_ids = []
    if in_reply_to:
        candidate_ids.append(in_reply_to)
    if references:
        candidate_ids.extend(references)

    if not candidate_ids:
        return None, None

    placeholders = ",".join(["?"] * len(candidate_ids))
    cursor = conn.cursor()
    cursor.execute(f"SELECT message_id, slack_ts FROM email_state WHERE message_id IN ({placeholders}) AND slack_ts IS NOT NULL LIMIT 1", candidate_ids)
    row = cursor.fetchone()
    if row:
        return row["message_id"], row["slack_ts"]
    return None, None

def notify_slack(summary, mail_info, config, parent_ts=None, dry_run=False):
    """Slack に通知を送信（スレッド維持対応）"""
    bot_token = config.get("slack", {}).get("bot_token")
    channel = config.get("slack", {}).get("channel", "#general")
    webhook_url = config.get("slack", {}).get("webhook_url")

    is_reply = parent_ts is not None
    header_prefix = "💬 *【返信】メールを受信しました*" if is_reply else "📬 *新着メールを受信しました*"

    text = (
        f"{header_prefix}\n"
        f"• *件名*: {mail_info['subject']}\n"
        f"• *差出人*: {mail_info['from']}\n"
        f"• *日時*: {mail_info['date']}\n\n"
        f"📝 *AI 要約:*\n{summary}"
    )

    if dry_run:
        target_info = f"（スレッド親 ts: {parent_ts} に返信）" if is_reply else "（新規親スレッドとして投稿）"
        print(f"\n[DRY-RUN] Slack 送信スキップ {target_info}:")
        print(f"件名: {mail_info['subject']}")
        print(f"差出人: {mail_info['from']}")
        print(f"日時: {mail_info['date']}")
        print(f"要約:\n{summary}\n")
        return "mock_ts_1234567890.123456"

    # Bot Token 優先
    if bot_token and not bot_token.startswith("xoxb-your"):
        api_url = "https://slack.com/api/chat.postMessage"
        headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        payload = {
            "channel": channel,
            "text": text,
        }
        if parent_ts:
            payload["thread_ts"] = parent_ts

        try:
            res = requests.post(api_url, headers=headers, json=payload, timeout=10)
            res.raise_for_status()
            res_data = res.json()
            if not res_data.get("ok"):
                print(f"Slack API エラー: {res_data.get('error')}", file=sys.stderr)
                return None
            return res_data.get("ts")
        except Exception as e:
            print(f"Slack 送信エラー: {e}", file=sys.stderr)
            return None

    # Webhook フォールバック
    if webhook_url and not webhook_url.startswith("https://hooks.slack.com/services/YOUR"):
        payload = {"text": text}
        if parent_ts:
            payload["thread_ts"] = parent_ts
        try:
            res = requests.post(webhook_url, json=payload, timeout=10)
            res.raise_for_status()
            return parent_ts or f"webhook_{datetime.now().timestamp()}"
        except Exception as e:
            print(f"Slack Webhook 送信エラー: {e}", file=sys.stderr)
            return None

    print("[WARN] Slack の認証情報（bot_token または webhook_url）が未設定です。", file=sys.stderr)
    return None

def get_profile_path(config):
    """プロファイルパスの解決（Docker コンテナ内マウントパス優先）"""
    if os.environ.get("THUNDERBIRD_CONTAINER_PATH"):
        return os.environ.get("THUNDERBIRD_CONTAINER_PATH")
    if os.path.exists("/data/thunderbird"):
        return "/data/thunderbird"
    return config["thunderbird"]["profile_path"]

def scan_emails(config, dry_run=False, catchup=False):
    """Thunderbird のディレクトリをスキャンして新着メールを処理"""
    profile_path = get_profile_path(config)
    db_file = os.environ.get("DB_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), config.get("db_file", "mail_history.db")))

    db_dir = os.path.dirname(os.path.abspath(db_file))
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    init_meilisearch(MEILI_URL)

    conn = get_db_connection(db_file)
    cursor = conn.cursor()

    cursor.execute("SELECT message_id FROM email_state")
    processed_ids = set(row[0] for row in cursor.fetchall())

    found_eml_files = []
    for rel_dir in config["thunderbird"].get("watch_dirs", ["Mail/Local Folders"]):
        target_dir = os.path.join(profile_path, rel_dir)
        if os.path.exists(target_dir):
            for root, _, files in os.walk(target_dir):
                for file in files:
                    if file.endswith(config["thunderbird"].get("file_extension", ".eml")):
                        found_eml_files.append(os.path.join(root, file))

    print(f"🔍 スキャン対象ファイル総数: {len(found_eml_files)} 件 (既処理: {len(processed_ids)} 件)")

    if catchup:
        print("⚡ [キャッチアップモード] 過去メールを一括登録中（Meilisearch へインデックス化、Slack 通知はスキップ）...")

    found_eml_files.sort(key=lambda x: os.path.getmtime(x), reverse=False)

    new_count = 0
    meili_batch = []
    sqlite_batch = []

    for file_path in found_eml_files:
        try:
            with open(file_path, "rb") as f:
                msg = email.message_from_binary_file(f)

            message_id = (msg.get("Message-ID") or os.path.basename(file_path)).strip("<> \r\n")
            if message_id in processed_ids:
                continue

            in_reply_to = msg.get("In-Reply-To", "").strip("<> \r\n")
            references_raw = msg.get("References", "")
            references = [ref.strip("<> \r\n") for ref in references_raw.split() if ref.strip("<> \r\n")]

            date_str = decode_mime_words(msg.get("Date", ""))
            date_iso, ts_val = parse_date_to_iso_and_timestamp(date_str)
            subject = decode_mime_words(msg.get("Subject", "(件名なし)"))
            sender = decode_mime_words(msg.get("From", "(不明)"))

            mail_info = {
                "message_id": message_id,
                "in_reply_to": in_reply_to,
                "references": references,
                "subject": subject,
                "from": sender,
                "date": date_str,
                "date_iso": date_iso,
                "timestamp": ts_val,
                "file_path": file_path,
            }

            body = extract_body(msg)
            body_snippet = body[:2000] if body else ""

            if catchup:
                meili_doc = {
                    "id": get_doc_id(message_id),
                    "message_id": message_id,
                    "subject": subject,
                    "sender": sender,
                    "date_str": date_str,
                    "date_iso": date_iso,
                    "timestamp": ts_val,
                    "summary": "（過去メール同期済み）",
                    "body": body_snippet,
                    "file_path": file_path,
                }
                meili_batch.append(meili_doc)
                sqlite_batch.append((message_id, None, None))
                processed_ids.add(message_id)
                new_count += 1

                if len(meili_batch) >= 500:
                    try:
                        requests.post(f"{MEILI_URL}/indexes/emails/documents", json=meili_batch, timeout=10)
                    except Exception as me:
                        print(f"Meilisearch バッチ登録警告: {me}", file=sys.stderr)
                    with conn:
                        conn.executemany("INSERT OR IGNORE INTO email_state (message_id, parent_id, slack_ts) VALUES (?, ?, ?)", sqlite_batch)
                    meili_batch.clear()
                    sqlite_batch.clear()
                continue

            summary = generate_summary(body, config)
            parent_msg_id, parent_slack_ts = find_parent_slack_ts(conn, in_reply_to, references)

            slack_ts = notify_slack(summary, mail_info, config, parent_ts=parent_slack_ts, dry_run=dry_run)
            if slack_ts:
                if not dry_run:
                    with conn:
                        conn.execute("""
                            INSERT OR REPLACE INTO email_state (message_id, parent_id, slack_ts)
                            VALUES (?, ?, ?)
                        """, (message_id, parent_msg_id, slack_ts))

                    meili_doc = {
                        "id": get_doc_id(message_id),
                        "message_id": message_id,
                        "subject": subject,
                        "sender": sender,
                        "date_str": date_str,
                        "date_iso": date_iso,
                        "timestamp": ts_val,
                        "summary": summary,
                        "body": body_snippet,
                        "slack_ts": slack_ts,
                        "file_path": file_path,
                    }
                    try:
                        requests.post(f"{MEILI_URL}/indexes/emails/documents", json=[meili_doc], timeout=5)
                    except Exception as me:
                        print(f"Meilisearch 登録エラー: {me}", file=sys.stderr)

                    processed_ids.add(message_id)
                new_count += 1

        except Exception as e:
            print(f"エラー発生 ({file_path}): {e}", file=sys.stderr)

    if catchup and meili_batch:
        try:
            requests.post(f"{MEILI_URL}/indexes/emails/documents", json=meili_batch, timeout=10)
        except Exception:
            pass
        with conn:
            conn.executemany("INSERT OR IGNORE INTO email_state (message_id, parent_id, slack_ts) VALUES (?, ?, ?)", sqlite_batch)
        meili_batch.clear()
        sqlite_batch.clear()

    conn.close()
    if catchup:
        print(f"✅ キャッチアップ完了: 過去メール {new_count} 件を Meilisearch & SQLite に登録しました。")
    else:
        print(f"✅ 処理完了: 新着 {new_count} 件を処理しました。")

def search_emails(config, keyword=None, since=None, until=None, limit=10):
    """Meilisearch から超高速・日本語全文検索＆日付範囲絞り込みを実行"""
    search_url = f"{MEILI_URL}/indexes/emails/search"

    filter_conditions = []
    if since:
        try:
            since_dt = datetime.strptime(since.strip(), "%Y-%m-%d")
            since_ts = int(since_dt.timestamp())
            filter_conditions.append(f"timestamp >= {since_ts}")
        except Exception:
            pass

    if until:
        try:
            until_dt = datetime.strptime(until.strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            until_ts = int(until_dt.timestamp())
            filter_conditions.append(f"timestamp <= {until_ts}")
        except Exception:
            pass

    payload = {
        "q": keyword or "",
        "limit": limit,
        "sort": ["timestamp:desc"],
    }
    if filter_conditions:
        payload["filter"] = " AND ".join(filter_conditions)

    criteria = []
    if keyword:
        criteria.append(f"キーワード: '{keyword}'")
    if since:
        criteria.append(f"開始: {since}")
    if until:
        criteria.append(f"終了: {until}")
    criteria_str = ", ".join(criteria) if criteria else "全件"

    try:
        res = requests.post(search_url, json=payload, timeout=10)
        res.raise_for_status()
        data = res.json()
        hits = data.get("hits", [])
        total_hits = data.get("estimatedTotalHits", len(hits))
        processing_ms = data.get("processingTimeMs", 0)

        print(f"\n🔎 Meilisearch 検索条件 [{criteria_str}] (該当: 約{total_hits}件 / 検索速度: {processing_ms}ms)\n" + "=" * 60)
        if not hits:
            print("該当するメールは見つかりませんでした。")
            return

        for idx, hit in enumerate(hits, 1):
            print(f"[{idx}] {hit.get('subject')}")
            print(f"   👤 差出人: {hit.get('sender')}")
            print(f"   📅 日時: {hit.get('date_str')} ({hit.get('date_iso')})")
            print(f"   💬 Slack ts: {hit.get('slack_ts') or '未連携'}")
            print(f"   📝 要約: {hit.get('summary', '')[:120]}...")
            print("-" * 60)

    except Exception as e:
        print(f"Meilisearch 検索エラー: {e}", file=sys.stderr)
        print("💡 ヒント: 'docker compose up -d search-engine' で検索エンジンが起動しているか確認してください。")

def main():
    parser = argparse.ArgumentParser(description="antigravity-mail-management")
    parser.add_argument("--dry-run", action="store_true", help="Slack 送信や DB 更新を行わずにコンソール出力")
    parser.add_argument("--init-catchup", action="store_true", help="初回実行用: 過去メールを既読として DB & Meilisearch に一括登録（Slack 通知はスキップ）")
    parser.add_argument("--search", type=str, default=None, help="キーワード全文検索 (日本語形態素解析対応)")
    parser.add_argument("--since", type=str, default=None, help="開始日 (例: 2026-10-01)")
    parser.add_argument("--until", type=str, default=None, help="終了日 (例: 2026-10-31)")
    parser.add_argument("--limit", type=int, default=10, help="検索結果の最大表示件数 (デフォルト: 10)")
    args = parser.parse_args()

    if not os.path.exists(CONFIG_PATH):
        print(f"⚠️ 設定ファイルが見つかりません。{CONFIG_EXAMPLE_PATH} をコピーして {CONFIG_PATH} を作成してください。")
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    if args.search or args.since or args.until:
        search_emails(config, keyword=args.search, since=args.since, until=args.until, limit=args.limit)
    else:
        scan_emails(config, dry_run=args.dry_run, catchup=args.init_catchup)

if __name__ == "__main__":
    main()
