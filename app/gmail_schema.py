"""Check the Gmail schema before starting or invoking automation."""
from sqlalchemy import inspect
from .models import GmailConversation, GmailMessage, GmailSendSlot


class GmailSchemaNotReady(RuntimeError):
    pass


def require_gmail_schema(bind):
    inspector = inspect(bind)
    missing = []
    for model in (GmailConversation, GmailMessage, GmailSendSlot):
        table = model.__table__
        if not inspector.has_table(table.name):
            missing.append(table.name)
            continue
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        missing.extend(table.name + "." + column.name
                       for column in table.columns if column.name not in actual)
    if missing:
        raise GmailSchemaNotReady(
            "Gmail database migration required (missing: " + ", ".join(missing) + "). "
            "Stop the app, then from the project directory run "
            r".\.venv\Scripts\python.exe -m alembic upgrade head"
            ". Restart the app afterwards. Run the command with the same DATABASE_URL "
            "and working directory as the server."
        )
