import os
import unittest
import tempfile
import shutil
import email
from email.message import EmailMessage
from mail_scanner import (
    strip_quotes,
    parse_thunderbird_prefs,
    detect_target_account,
    check_spam,
    mark_as_read_local,
    move_to_junk_local,
    extract_body_and_attachments,
    check_and_remind_pending_replies,
    get_db_connection,
    get_doc_id
)

class TestMailScanner(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_strip_quotes(self):
        # 引用行 (>) の除去
        text_with_quotes = (
            "こんにちは、お疲れ様です。\n"
            "ご確認お願いいたします。\n"
            "> 昨日の件ですがどうなりましたか？\n"
            "> よろしくお願いします。"
        )
        cleaned = strip_quotes(text_with_quotes)
        self.assertIn("ご確認お願いいたします。", cleaned)
        self.assertNotIn("昨日の件ですがどうなりましたか？", cleaned)

        # 過去ログヘッダー（On ... wrote / 元のメッセージ）以降の除去
        text_with_header = (
            "承知いたしました。来週対応します。\n\n"
            "-----Original Message-----\n"
            "From: tanaka@example.com\n"
            "Sent: Friday, October 30, 2026\n"
            "見積もりの件よろしくお願いします。"
        )
        cleaned_header = strip_quotes(text_with_header)
        self.assertEqual(cleaned_header, "承知いたしました。来週対応します。")

    def test_parse_thunderbird_prefs(self):
        prefs_content = (
            'pref("mail.identity.id1.useremail", "user1@example.com");\n'
            'pref("mail.identity.id2.useremail", "USER2@EXAMPLE.COM");\n'
            'pref("mail.server.server1.check_time", 15);\n'
            'pref("mail.server.server2.check_time", 30);\n'
        )
        prefs_path = os.path.join(self.test_dir, "prefs.js")
        with open(prefs_path, "w", encoding="utf-8") as f:
            f.write(prefs_content)

        accounts, intervals = parse_thunderbird_prefs(self.test_dir)
        self.assertEqual(accounts, ["user1@example.com", "user2@example.com"])
        self.assertEqual(intervals, [15, 30])

    def test_detect_target_account(self):
        registered = ["work@company.com", "sub@company.com"]

        msg1 = EmailMessage()
        msg1["To"] = "Team <work@company.com>"
        self.assertEqual(detect_target_account(msg1, registered), "work@company.com")

        msg2 = EmailMessage()
        msg2["Delivered-To"] = "sub@company.com"
        msg2["To"] = "all@company.com"
        self.assertEqual(detect_target_account(msg2, registered), "sub@company.com")

        msg3 = EmailMessage()
        msg3["To"] = "other@unknown.com"
        self.assertEqual(detect_target_account(msg3, registered), "other@unknown.com")

    def test_check_spam(self):
        # フォルダ判定
        msg = EmailMessage()
        is_spam, reason = check_spam(msg, "/path/to/Thunderbird/Mail/Junk/12345.eml")
        self.assertTrue(is_spam)
        self.assertEqual(reason, "JUNK_FOLDER")

        # ヘッダー判定
        msg_spam = EmailMessage()
        msg_spam["X-Spam-Status"] = "Yes, score=7.5"
        is_spam, reason = check_spam(msg_spam, "/path/to/Inbox/12345.eml")
        self.assertTrue(is_spam)
        self.assertEqual(reason, "HEADER_X_SPAM_STATUS")

        # クリーン
        msg_clean = EmailMessage()
        msg_clean["Subject"] = "通常のご連絡"
        is_spam, reason = check_spam(msg_clean, "/path/to/Inbox/12345.eml")
        self.assertFalse(is_spam)
        self.assertEqual(reason, "CLEAN")

    def test_mark_as_read_local(self):
        # 未読ファイル作成
        mail_file = os.path.join(self.test_dir, "1234567890.M123P456.host")
        with open(mail_file, "w") as f:
            f.write("test mail content")

        success = mark_as_read_local(mail_file)
        self.assertTrue(success)
        expected_file = mail_file + ":2,S"
        self.assertTrue(os.path.exists(expected_file))
        self.assertFalse(os.path.exists(mail_file))

        # 既にフラグがある場合（:2, 形式）
        flagged_file = os.path.join(self.test_dir, "2345678901.M234P567.host:2,")
        with open(flagged_file, "w") as f:
            f.write("flagged mail")
        success2 = mark_as_read_local(flagged_file)
        self.assertTrue(success2)
        self.assertTrue(os.path.exists(flagged_file + "S"))

    def test_extract_body_and_attachments(self):
        msg = EmailMessage()
        msg["Subject"] = "Test Multipart"
        msg.set_content("本文プレーンテキストです。")
        msg.add_attachment(b"dummy pdf content", maintype="application", subtype="pdf", filename="test.pdf")

        body, attachments = extract_body_and_attachments(msg, "<test@id>", self.test_dir)
        self.assertIn("本文プレーンテキストです。", body)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["name"], "test.pdf")
        self.assertTrue(os.path.exists(attachments[0]["path"]))

    def test_move_to_junk_local(self):
        inbox_dir = os.path.join(self.test_dir, "Mail", "Local Folders", "Inbox")
        os.makedirs(inbox_dir, exist_ok=True)
        mail_file = os.path.join(inbox_dir, "test_spam.eml")
        with open(mail_file, "w") as f:
            f.write("spam message content")

        success, new_path = move_to_junk_local(mail_file)
        self.assertTrue(success)
        self.assertFalse(os.path.exists(mail_file))
        self.assertTrue(os.path.exists(new_path))
        self.assertIn("Junk", new_path)

    def test_check_and_remind_pending_replies(self):
        db_path = os.path.join(self.test_dir, "test.db")
        conn = get_db_connection(db_path)
        with conn:
            conn.execute("""
                INSERT INTO email_state (message_id, slack_ts, file_path, needs_reply, replied, reply_deadline)
                VALUES ('<req1@example.com>', '1700000000.000100', '/path/to/mail', 1, 0, '2026-10-30')
            """)
        conn.close()

        config = {
            "db_file": db_path,
            "slack": {"bot_token": "xoxb-mock", "channel": "#test"}
        }
        # dry_run でエラーなく実行されるか
        check_and_remind_pending_replies(config, dry_run=True)

if __name__ == "__main__":
    unittest.main()
