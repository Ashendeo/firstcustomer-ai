import unittest
from sqlalchemy import create_engine, text
from app.db import Base
from app.gmail_schema import GmailSchemaNotReady, require_gmail_schema
from app.models import GmailConversation, GmailMessage, GmailSendSlot


class GmailSchemaTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")

    def tearDown(self):
        self.engine.dispose()

    def test_missing_tables_explain_migration(self):
        with self.assertRaisesRegex(GmailSchemaNotReady, "alembic upgrade head"):
            require_gmail_schema(self.engine)

    def test_partial_schema_is_not_ready(self):
        GmailConversation.__table__.create(self.engine)
        with self.assertRaisesRegex(GmailSchemaNotReady, "gmail_messages"):
            require_gmail_schema(self.engine)

    def test_missing_columns_are_reported(self):
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE gmail_conversations (lead_id TEXT PRIMARY KEY)"))
        with self.assertRaisesRegex(GmailSchemaNotReady, "gmail_conversations.account"):
            require_gmail_schema(self.engine)

    def test_current_schema_passes(self):
        Base.metadata.create_all(self.engine)
        require_gmail_schema(self.engine)


if __name__ == "__main__":
    unittest.main()
