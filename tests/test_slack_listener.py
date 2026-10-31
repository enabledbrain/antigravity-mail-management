import os
import unittest
import tempfile
import shutil
from email.message import EmailMessage
from mail_scanner import get_db_connection
from slack_listener import (
    get_mail_content_by_message_id,
    get_message_id_by_slack_ts,
    generate_ai_reply,
    create_slack_app
)

class TestSlackListener(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.conn = get_db_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_mail_content_and_ts_mapping(self):
        # テストメール作成
        mail_file = os.path.join(self.test_dir, "sample.eml")
        msg = EmailMessage()
        msg["Subject"] = "お見積書の件"
        msg["From"] = "tanaka@example.com"
        msg.set_content("お世話になっております。お見積書をお送りします。")
        with open(mail_file, "wb") as f:
            f.write(msg.as_bytes())

        # DB に登録
        with self.conn:
            self.conn.execute("""
                INSERT INTO email_state (message_id, slack_ts, file_path, target_account)
                VALUES ('<test_msg_001@example.com>', '1700000001.000200', ?, 'work@example.com')
            """, (mail_file,))

        # ts -> message_id 取得
        msg_id = get_message_id_by_slack_ts(self.db_path, "1700000001.000200")
        self.assertEqual(msg_id, "<test_msg_001@example.com>")

        # message_id -> 本文取得
        subj, sender, body, fpath = get_mail_content_by_message_id(self.db_path, msg_id)
        self.assertEqual(subj, "お見積書の件")
        self.assertEqual(sender, "tanaka@example.com")
        self.assertIn("お見積書をお送りします。", body)
        self.assertEqual(fpath, mail_file)

    def test_generate_ai_reply_mock(self):
        config = {"ai": {"api_key": ""}}
        res = generate_ai_reply("このメールに返信案を作って", "件名: テスト", config)
        self.assertIn("[ローカル返信モック]", res)

    def test_create_slack_app_with_token(self):
        config = {
            "slack": {"bot_token": "xoxb-mock-token-for-test"},
            "db_file": self.db_path
        }
        app = create_slack_app(config, check_auth=False)
        self.assertIsNotNone(app)

if __name__ == "__main__":
    unittest.main()
