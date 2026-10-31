#!/usr/bin/env python3
"""
antigravity-mail-management
Thunderbird Maildir Scanner, Meilisearch Engine & Slack Notifier
- Quote Strip: 引用（> ... や過去ログ）の自動除去
- Multi-Account: prefs.js から複数アカウントを自動判別し宛先メールアドレスを表示
- Spam Filter & Rescue: 迷惑メール自動スキップ ＆ AI誤判定レスキュー通知
- Thread Backfill: 過去の未連携スレッドを自動遡及展開
- Interactive Buttons: [📄 全文を展開] [🤖 Antigravityで開く] [✅ 既読にする]
- Local Sync: Slack操作から Thunderbird の既読化（:2,S）および Junk 移動
- Attachment Handler: 添付ファイルをローカル分離保存し Antigravity 連携
- User Rules: rules/summary_rules.md による自然言語 override
"""

import os
import re
import sys
import json
import shutil
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
                file_path TEXT,
                target_account TEXT,
                needs_reply INTEGER DEFAULT 0,
                replied INTEGER DEFAULT 0,
                reply_deadline TEXT,
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
                     json=["timestamp", "date_iso", "sender", "subject", "message_id", "target_account"], timeout=5)
        requests.put(f"{index_url}/settings/sortable-attributes", 
                     json=["timestamp", "date_iso"], timeout=5)
    except Exception as e:
        print(f"⚠️ Meilisearch 初期化警告: {e}", file=sys.stderr)

def get_doc_id(message_id):
    """Meilisearch 用の安全なドキュメント ID（ハッシュ）を生成"""
    return hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:32]

def parse_thunderbird_prefs(profile_path):
    """prefs.js から登録されているメールアドレスと同期間隔（分）を抽出"""
    prefs_file = os.path.join(profile_path, "prefs.js")
    accounts = []
    sync_intervals = []
    if not os.path.exists(prefs_file):
        return accounts, sync_intervals

    try:
        with open(prefs_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                # useremail の抽出
                m_email = re.search(r'pref\("mail\.identity\.id\d+\.useremail",\s*"([^"]+)"\);', line)
                if m_email:
                    email_addr = m_email.group(1).lower().strip()
                    if email_addr not in accounts:
                        accounts.append(email_addr)
                # check_time（同期間隔: 分）の抽出
                m_time = re.search(r'pref\("mail\.server\.server\d+\.check_time",\s*(\d+)\);', line)
                if m_time:
                    interval = int(m_time.group(1))
                    if interval not in sync_intervals:
                        sync_intervals.append(interval)
    except Exception as e:
        print(f"⚠️ prefs.js 解析警告: {e}", file=sys.stderr)

    return accounts, sync_intervals

def detect_target_account(msg, registered_accounts):
    """メールヘッダー（To, Delivered-To 等）から受信先のアカウントを判別"""
    headers_to_check = [
        msg.get("Delivered-To", ""),
        msg.get("X-Original-To", ""),
        msg.get("Envelope-To", ""),
        msg.get("To", ""),
        msg.get("Cc", "")
    ]
    all_targets = " ".join(headers_to_check).lower()
    for acc in registered_accounts:
        if acc in all_targets:
            return acc

    # 登録アカウントに完全一致しない場合は To ヘッダーから抽出
    to_hdr = decode_mime_words(msg.get("To", ""))
    m = re.search(r'[\w\.-]+@[\w\.-]+', to_hdr)
    return m.group(0) if m else "不明"

def strip_quotes(text):
    """メール本文から引用（> ... や過去ログヘッダー）を自動カット"""
    if not text:
        return ""
    lines = text.splitlines()
    clean_lines = []
    quote_markers = [
        r"^-----Original Message-----",
        r"^-----元のメッセージ-----",
        r"^On .* wrote:$",
        r"^.*wrote:$",
        r"^\d{4}年\d{1,2}月\d{1,2}日.*写し.*:$",
        r"^\d{4}/\d{1,2}/\d{1,2}.*:$",
        r"^_+\s*$",
    ]

    for line in lines:
        stripped = line.strip()
        # 引用記号 (>) で始まる行はスキップ
        if stripped.startswith(">"):
            continue
        # 引用開始ヘッダーにマッチしたら以降は過去ログとみなして打ち切り
        is_quote_start = False
        for marker in quote_markers:
            if re.search(marker, stripped, re.IGNORECASE):
                is_quote_start = True
                break
        if is_quote_start:
            break
        clean_lines.append(line)

    res = "\n".join(clean_lines).strip()
    return res if res else text.strip()

def check_spam(msg, file_path):
    """スパム判定チェック (Thunderbird / サーバー判定)"""
    # フォルダパスに Junk / Trash が含まれるか
    norm_path = file_path.lower()
    if "/junk" in norm_path or "/trash" in norm_path or "/spam" in norm_path:
        return True, "JUNK_FOLDER"

    # ヘッダーチェック
    spam_status = str(msg.get("X-Spam-Status", "")).lower()
    if "yes" in spam_status:
        return True, "HEADER_X_SPAM_STATUS"

    spam_flag = str(msg.get("X-Spam-Flag", "")).upper()
    if "YES" in spam_flag:
        return True, "HEADER_X_SPAM_FLAG"

    subject = decode_mime_words(msg.get("Subject", ""))
    if "[spam]" in subject.lower():
        return True, "SUBJECT_TAG"

    return False, "CLEAN"

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

def extract_body_and_attachments(msg, message_id, data_dir):
    """本文抽出および添付ファイルのローカル保存"""
    body = ""
    attachments = []
    safe_id = hashlib.md5(message_id.encode("utf-8")).hexdigest()[:12]
    attach_dir = os.path.join(data_dir, "attachments", safe_id)

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            filename = part.get_filename()

            if filename:
                decoded_fn = decode_mime_words(filename)
                payload = part.get_payload(decode=True)
                if payload:
                    os.makedirs(attach_dir, exist_ok=True)
                    target_file = os.path.join(attach_dir, decoded_fn)
                    try:
                        with open(target_file, "wb") as af:
                            af.write(payload)
                        size_kb = round(len(payload) / 1024, 1)
                        size_str = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb/1024, 2)} MB"
                        attachments.append({
                            "name": decoded_fn,
                            "size": size_str,
                            "path": target_file
                        })
                    except Exception as e:
                        print(f"⚠️ 添付保存エラー: {e}", file=sys.stderr)
            elif content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                try:
                    body = payload.decode(charset, errors="replace")
                except Exception:
                    body = payload.decode("utf-8", errors="replace")
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

    return body, attachments

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
    """LLM API (Gemini 等) またはフォールバックで要約＆要返信判定を生成"""
    api_key = config.get("ai", {}).get("api_key")
    if not api_key or "YOUR_GEMINI_API_KEY" in api_key:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        snippet = "\n".join(lines[:4])
        return f"【要約】\n{snippet}...", False, None

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config['ai'].get('model', 'gemini-2.5-flash')}:generateContent?key={api_key}"
        base_prompt = (
            "以下のメール本文から、要約と返信要否を判定してください。\n"
            "出力フォーマット:\n"
            "【要約】(3行以内)\n"
            "【要返信】(はい/いいえ)\n"
            "【返信推奨期限】(期日がある場合はYYYY-MM-DD、ない場合はなし)"
        )
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
        raw_output = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        needs_reply = "【要返信】はい" in raw_output or "【要返信】: はい" in raw_output
        m_date = re.search(r'【返信推奨期限】\s*(\d{4}-\d{2}-\d{2})', raw_output)
        deadline = m_date.group(1) if m_date else None
        return raw_output, needs_reply, deadline
    except Exception as e:
        return f"（要約エラー: {e}）\n{text[:150]}...", False, None

def mark_as_read_local(file_path):
    """Thunderbird Maildir ファイル名を :2,S にリネームして既読化"""
    if not os.path.exists(file_path):
        return False
    dirname, filename = os.path.split(file_path)
    if ":2," in filename:
        if "S" in filename.split(":2,")[-1]:
            return True  # すでに既読
        new_filename = filename + "S"
    else:
        new_filename = filename + ":2,S"
    new_path = os.path.join(dirname, new_filename)
    try:
        os.rename(file_path, new_path)
        return True
    except Exception as e:
        print(f"⚠️ 既読リネームエラー: {e}", file=sys.stderr)
        return False

def move_to_junk_local(file_path):
    """Thunderbird Maildir ファイルを Junk ディレクトリへ移動"""
    if not os.path.exists(file_path):
        return False, "File not found"
    dirname, filename = os.path.split(file_path)
    parent_dir = os.path.dirname(dirname)
    junk_dir = os.path.join(parent_dir, "Junk")
    if not os.path.exists(junk_dir):
        junk_candidates = [
            os.path.join(dirname, "Junk"),
            os.path.join(parent_dir, "Junk"),
            os.path.join(os.path.dirname(parent_dir), "Local Folders", "Junk")
        ]
        junk_dir = next((c for c in junk_candidates if os.path.exists(c)), None)
        if not junk_dir:
            junk_dir = os.path.join(parent_dir, "Junk")
            os.makedirs(junk_dir, exist_ok=True)

    dest_path = os.path.join(junk_dir, filename)
    try:
        shutil.move(file_path, dest_path)
        return True, dest_path
    except Exception as e:
        print(f"⚠️ スパム移動エラー: {e}", file=sys.stderr)
        return False, str(e)

def notify_slack_interactive(summary, mail_info, config, attachments=None, is_rescue=False, parent_ts=None, dry_run=False):
    """Slack Block Kit による 3種ボタン付き通知"""
    bot_token = config.get("slack", {}).get("bot_token")
    channel = config.get("slack", {}).get("channel", "#general")

    target_badge = f"`[{mail_info.get('target_account', '宛先不明')}]`"
    if is_rescue:
        header_text = f"⚠️ *スパム判定されていますが重要メールの可能性があります* {target_badge}"
    elif parent_ts:
        header_text = f"💬 *【返信】メールを受信しました* {target_badge}"
    else:
        header_text = f"📬 *新着メールを受信しました* {target_badge}"

    meta_text = (
        f"• *件名*: {mail_info['subject']}\n"
        f"• *差出人*: {mail_info['from']}\n"
        f"• *日時*: {mail_info['date']}"
    )

    attach_text = ""
    if attachments:
        attach_names = [f"📎 `{a['name']}` ({a['size']})" for a in attachments]
        attach_text = "\n• *添付*: " + ", ".join(attach_names)

    main_text = f"{header_text}\n{meta_text}{attach_text}\n\n📝 *AI 要約:*\n{summary}"

    # Block Kit UI の構築
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": main_text}
        }
    ]

    # アクションボタン
    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "📄 全文を展開", "emoji": True},
            "action_id": "expand_full_body",
            "value": mail_info["message_id"]
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "🤖 Antigravityで開く", "emoji": True},
            "action_id": "antigravity_open",
            "value": mail_info["message_id"]
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "✅ 既読にする", "emoji": True},
            "action_id": "mark_as_read",
            "value": mail_info["message_id"]
        }
    ]

    if is_rescue:
        buttons.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "🚫 スパムに登録", "emoji": True},
            "style": "danger",
            "action_id": "mark_as_spam",
            "value": mail_info["message_id"]
        })

    blocks.append({
        "type": "actions",
        "elements": buttons
    })

    if dry_run:
        print(f"\n[DRY-RUN] Slack Block Kit 送信スキップ:")
        print(f"宛先: {target_badge} / 件名: {mail_info['subject']}")
        print(f"要約:\n{summary}\n")
        return "mock_ts_1234567890.123456"

    if bot_token and not bot_token.startswith("xoxb-your"):
        api_url = "https://slack.com/api/chat.postMessage"
        headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        payload = {
            "channel": channel,
            "text": f"{header_text} - {mail_info['subject']}",
            "blocks": blocks
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

    return None

def backfill_past_threads(conn, references, channel, bot_token):
    """過去スレッド自動遡及: 未連携の親メールがあれば Slack スレッドに履歴展開"""
    if not references or not bot_token or bot_token.startswith("xoxb-your"):
        return None

    placeholders = ",".join(["?"] * len(references))
    cursor = conn.cursor()
    cursor.execute(f"SELECT message_id, slack_ts, file_path FROM email_state WHERE message_id IN ({placeholders})", references)
    rows = cursor.fetchall()

    parent_ts = None
    for r in rows:
        if r["slack_ts"]:
            parent_ts = r["slack_ts"]
            break

    return parent_ts

def scan_emails(config, dry_run=False, catchup=False):
    """Thunderbird のディレクトリをスキャンして新着メールを処理"""
    profile_path = os.environ.get("THUNDERBIRD_CONTAINER_PATH") or ("/data/thunderbird" if os.path.exists("/data/thunderbird") else config["thunderbird"]["profile_path"])
    db_file = os.environ.get("DB_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), config.get("db_file", "mail_history.db")))
    data_dir = os.path.dirname(os.path.abspath(db_file))

    # prefs.js から複数アカウント & 同期間隔を読み取り
    registered_accounts, sync_intervals = parse_thunderbird_prefs(profile_path)
    if registered_accounts:
        print(f"📧 検出された受信アカウント: {', '.join(registered_accounts)}")
    if sync_intervals:
        print(f"⏱️ 検出されたThunderbird受信間隔: {sync_intervals} 分")

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
        print("⚡ [キャッチアップモード] 過去メールを一括登録中...")

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

            # スパム判定チェック
            is_spam, spam_reason = check_spam(msg, file_path)

            in_reply_to = msg.get("In-Reply-To", "").strip("<> \r\n")
            references_raw = msg.get("References", "")
            references = [ref.strip("<> \r\n") for ref in references_raw.split() if ref.strip("<> \r\n")]

            date_str = decode_mime_words(msg.get("Date", ""))
            date_iso, ts_val = parse_date_to_iso_and_timestamp(date_str)
            subject = decode_mime_words(msg.get("Subject", "(件名なし)"))
            sender = decode_mime_words(msg.get("From", "(不明)"))

            target_account = detect_target_account(msg, registered_accounts)

            mail_info = {
                "message_id": message_id,
                "in_reply_to": in_reply_to,
                "references": references,
                "subject": subject,
                "from": sender,
                "date": date_str,
                "date_iso": date_iso,
                "timestamp": ts_val,
                "target_account": target_account,
                "file_path": file_path,
            }

            body, attachments = extract_body_and_attachments(msg, message_id, data_dir)
            body_clean = strip_quotes(body)

            if catchup:
                meili_doc = {
                    "id": get_doc_id(message_id),
                    "message_id": message_id,
                    "subject": subject,
                    "sender": sender,
                    "date_str": date_str,
                    "date_iso": date_iso,
                    "timestamp": ts_val,
                    "target_account": target_account,
                    "summary": "（過去メール同期済み）",
                    "body": body_clean[:2000],
                    "file_path": file_path,
                }
                meili_batch.append(meili_doc)
                sqlite_batch.append((message_id, None, None, file_path, target_account, 0, 0, None))
                processed_ids.add(message_id)
                new_count += 1

                if len(meili_batch) >= 500:
                    try:
                        requests.post(f"{MEILI_URL}/indexes/emails/documents", json=meili_batch, timeout=10)
                    except Exception:
                        pass
                    with conn:
                        conn.executemany("""
                            INSERT OR IGNORE INTO email_state 
                            (message_id, parent_id, slack_ts, file_path, target_account, needs_reply, replied, reply_deadline) 
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, sqlite_batch)
                    meili_batch.clear()
                    sqlite_batch.clear()
                continue

            # 通常スキャン時: スパムかつ AI 誤判定の疑いがない場合はスキップ
            is_rescue = False
            if is_spam:
                # 簡易AIレスキュー判定（重要なキーワードやドメインが含まれるか）
                if any(w in body_clean.lower() or w in subject.lower() for w in ["請求書", "お見積", "契約", "障害", "緊急", "ご確認"]):
                    is_rescue = True
                else:
                    # 完全にスパムとしてスキップ
                    with conn:
                        conn.execute("INSERT OR IGNORE INTO email_state (message_id, file_path, target_account) VALUES (?, ?, ?)",
                                     (message_id, file_path, target_account))
                    processed_ids.add(message_id)
                    continue

            # AI 要約生成 (引用カット済み本文を入力)
            summary, needs_reply, deadline = generate_summary(body_clean, config)

            # 過去スレッド自動遡及取り込み
            parent_ts = backfill_past_threads(conn, references, config.get("slack", {}).get("channel"), config.get("slack", {}).get("bot_token"))

            slack_ts = notify_slack_interactive(summary, mail_info, config, attachments=attachments, is_rescue=is_rescue, parent_ts=parent_ts, dry_run=dry_run)
            if slack_ts:
                if not dry_run:
                    with conn:
                        conn.execute("""
                            INSERT OR REPLACE INTO email_state 
                            (message_id, parent_id, slack_ts, file_path, target_account, needs_reply, replied, reply_deadline)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (message_id, in_reply_to or None, slack_ts, file_path, target_account, 1 if needs_reply else 0, 0, deadline))

                    meili_doc = {
                        "id": get_doc_id(message_id),
                        "message_id": message_id,
                        "subject": subject,
                        "sender": sender,
                        "date_str": date_str,
                        "date_iso": date_iso,
                        "timestamp": ts_val,
                        "target_account": target_account,
                        "summary": summary,
                        "body": body_clean[:2000],
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
            conn.executemany("""
                INSERT OR IGNORE INTO email_state 
                (message_id, parent_id, slack_ts, file_path, target_account, needs_reply, replied, reply_deadline) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, sqlite_batch)
        meili_batch.clear()
        sqlite_batch.clear()

    conn.close()
    if catchup:
        print(f"✅ キャッチアップ完了: 過去メール {new_count} 件を登録しました。")
    else:
        print(f"✅ 処理完了: 新着 {new_count} 件を処理しました。")

def check_and_remind_pending_replies(config, dry_run=False):
    """要返信かつ未対応（replied=0）のメールをチェックし、Slack スレッドにリマインド通知 (Issue #2)"""
    db_file = os.environ.get("DB_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), config.get("db_file", "mail_history.db")))
    if not os.path.exists(db_file):
        print("DB ファイルが存在しません。")
        return

    conn = get_db_connection(db_file)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT message_id, slack_ts, file_path, target_account, reply_deadline, created_at
        FROM email_state
        WHERE needs_reply = 1 AND replied = 0 AND slack_ts IS NOT NULL
        ORDER BY created_at ASC
    """)
    rows = cursor.fetchall()

    if not rows:
        print("✅ 未返信の要対応メールはありません。")
        conn.close()
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    bot_token = config.get("slack", {}).get("bot_token")
    channel = config.get("slack", {}).get("channel", "#general")

    print(f"📋 要返信・未対応メール: {len(rows)} 件")
    remind_count = 0

    for r in rows:
        msg_id = r["message_id"]
        slack_ts = r["slack_ts"]
        deadline = r["reply_deadline"]
        file_path = r["file_path"]

        # 件名や送信者の取得（メールファイルが存在する場合）
        subject = "(件名不明)"
        sender = "(不明)"
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, "rb") as f:
                    m = email.message_from_binary_file(f)
                    subject = decode_mime_words(m.get("Subject", "(件名なし)"))
                    sender = decode_mime_words(m.get("From", "(不明)"))
            except Exception:
                pass

        status_badge = "⏳ 【未対応リマインド】"
        if deadline:
            if deadline < today_str:
                status_badge = f"🚨 【返信期限超過 ({deadline})】"
            elif deadline == today_str:
                status_badge = f"⏰ 【本日返信締切 ({deadline})】"
            else:
                status_badge = f"📅 【返信期限: {deadline}】"

        remind_text = (
            f"{status_badge} このメールへの返信はお済みですか？\n"
            f"• *件名*: {subject}\n"
            f"• *差出人*: {sender}\n"
            f"返信が完了している場合は、下のボタンを押してステータスを更新してください。"
        )

        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": remind_text}
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ 返信済みにする", "emoji": True},
                        "action_id": "mark_as_replied",
                        "value": msg_id
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🤖 返信案を作成", "emoji": True},
                        "action_id": "draft_reply",
                        "value": msg_id
                    }
                ]
            }
        ]

        if dry_run:
            print(f"[DRY-RUN] リマインド通知: {subject} (期限: {deadline or 'なし'})")
            remind_count += 1
            continue

        if bot_token and not bot_token.startswith("xoxb-your"):
            try:
                api_url = "https://slack.com/api/chat.postMessage"
                headers = {
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json; charset=utf-8"
                }
                payload = {
                    "channel": channel,
                    "thread_ts": slack_ts,
                    "text": f"{status_badge} {subject}",
                    "blocks": blocks
                }
                res = requests.post(api_url, headers=headers, json=payload, timeout=10)
                if res.status_code == 200 and res.json().get("ok"):
                    remind_count += 1
            except Exception as e:
                print(f"⚠️ リマインド送信エラー ({msg_id}): {e}", file=sys.stderr)

    conn.close()
    print(f"🔔 {remind_count} 件のメールスレッドにリマインドを送信しました。")

def search_emails(config, keyword=None, since=None, until=None, limit=10):
    """Meilisearch から超高速・日本語全文検索＆日付範囲絞り込みを実行"""
    search_url = f"{MEILI_URL}/indexes/emails/search"

    filter_conditions = []
    if since:
        try:
            since_dt = datetime.strptime(since.strip(), "%Y-%m-%d")
            filter_conditions.append(f"timestamp >= {int(since_dt.timestamp())}")
        except Exception:
            pass

    if until:
        try:
            until_dt = datetime.strptime(until.strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            filter_conditions.append(f"timestamp <= {int(until_dt.timestamp())}")
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
            acc_badge = f"[{hit.get('target_account', '宛先不明')}] " if hit.get("target_account") else ""
            print(f"[{idx}] {acc_badge}{hit.get('subject')}")
            print(f"   👤 差出人: {hit.get('sender')}")
            print(f"   📅 日時: {hit.get('date_str')} ({hit.get('date_iso')})")
            print(f"   💬 Slack ts: {hit.get('slack_ts') or '未連携'}")
            print(f"   📝 要約: {hit.get('summary', '')[:120]}...")
            print("-" * 60)

    except Exception as e:
        print(f"Meilisearch 検索エラー: {e}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="antigravity-mail-management")
    parser.add_argument("--dry-run", action="store_true", help="Slack 送信や DB 更新を行わずにコンソール出力")
    parser.add_argument("--init-catchup", action="store_true", help="初回実行用: 過去メールを既読として DB & Meilisearch に一括登録（Slack 通知はスキップ）")
    parser.add_argument("--remind", action="store_true", help="要返信かつ未対応のメールをチェックし、Slack スレッドにリマインド通知")
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

    if args.remind:
        check_and_remind_pending_replies(config, dry_run=args.dry_run)
    elif args.search or args.since or args.until:
        search_emails(config, keyword=args.search, since=args.since, until=args.until, limit=args.limit)
    else:
        scan_emails(config, dry_run=args.dry_run, catchup=args.init_catchup)

if __name__ == "__main__":
    main()
