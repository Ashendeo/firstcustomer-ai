from dotenv import load_dotenv
load_dotenv()
import logging
import os

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DATABASE_URL = normalize_url(os.getenv("DATABASE_URL", "sqlite:///./firstcustomer.db"))
IS_SQLITE = DATABASE_URL.startswith("sqlite")
SQLITE_BUSY_TIMEOUT_MS = 10000

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=(
        {"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000}
        if IS_SQLITE else {}
    ),
)

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection, _):
        # Applied to every pooled SQLite connection, including background jobs.
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()

    # WAL lets readers (the dashboard, other requests) coexist with an
    # approval write. Journal mode persists in the SQLite database file.
    # This does not permit multiple simultaneous writers: keep transactions
    # short and retry harmless approval transitions on transient contention.
    if ":memory:" not in DATABASE_URL:
        try:
            with engine.connect() as connection:
                mode = connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar()
                if str(mode).lower() != "wal":
                    logger.warning("SQLite WAL mode not enabled (journal_mode=%s)", mode)
        except OperationalError:
            # An external process might already be holding a write lock.
            # Allow startup; approval will show an actionable 503 if still busy.
            logger.warning("Could not enable SQLite WAL; database may be locked", exc_info=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as db:
        yield db
