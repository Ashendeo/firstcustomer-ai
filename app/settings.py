from dotenv import load_dotenv
load_dotenv()
import os


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


DEMO_MODE = env_bool("DEMO_MODE", "true")
CRUSTDATA_API_KEY = os.getenv("CRUSTDATA_API_KEY", "")
CRUSTDATA_INCLUDE_TECHNOGRAPHICS = env_bool("CRUSTDATA_INCLUDE_TECHNOGRAPHICS", "false")
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3:4b")
QWEN_MODEL_SERVER = os.getenv("QWEN_MODEL_SERVER", "http://localhost:11434/v1")
QWEN_TIMEOUT_SECONDS = max(15, min(int(os.getenv("QWEN_TIMEOUT_SECONDS", "120")), 600))
QWEN_NUM_PREDICT = max(150, min(int(os.getenv("QWEN_NUM_PREDICT", "900")), 3000))
QWEN_PLAN_WITH_MODEL = env_bool("QWEN_PLAN_WITH_MODEL", "false")
MAX_PROSPECT_ENRICHMENTS = max(0, min(int(os.getenv("MAX_PROSPECT_ENRICHMENTS", "5")), 10))
MAX_CONTACT_ENRICHMENTS = max(0, min(int(os.getenv("MAX_CONTACT_ENRICHMENTS", "5")), 20))
MAX_COMPANIES = max(1, min(int(os.getenv("MAX_COMPANIES", "5")), 10))
MAX_PEOPLE_SEARCHES = max(0, min(int(os.getenv("MAX_PEOPLE_SEARCHES", "3")), 10))
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_WEBHOOK_SECRET = os.getenv("RESEND_WEBHOOK_SECRET", "")
OUTREACH_CRON_SECRET = os.getenv("OUTREACH_CRON_SECRET", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "")
CALENDLY_SIGNING_KEY = os.getenv("CALENDLY_SIGNING_KEY", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
CSRF_SECRET = os.getenv("CSRF_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# Explicit, local-only Gmail SMTP sandbox for demo drafts.
# Neither address is embedded in production code; sender and recipient MUST match.
GMAIL_DEMO_SENDER = os.getenv("GMAIL_DEMO_SENDER", "").strip().lower()
GMAIL_DEMO_TO = os.getenv("GMAIL_DEMO_TO", "").strip().lower()
GMAIL_DEMO_APP_PASSWORD = os.getenv("GMAIL_DEMO_APP_PASSWORD", "").replace(" ", "")

# Existing demo credentials remain sufficient for sandbox conversations.
GMAIL_ACCOUNT = os.getenv("GMAIL_ACCOUNT", "").strip().lower()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
GMAIL_LIVE_ENABLED = env_bool("GMAIL_LIVE_ENABLED")
GMAIL_IMAP_FOLDER = os.getenv("GMAIL_IMAP_FOLDER", "INBOX")
GMAIL_AUTOMATION_ENABLED = env_bool("GMAIL_AUTOMATION_ENABLED")
GMAIL_POLL_SECONDS = max(30, int(os.getenv("GMAIL_POLL_SECONDS", "60")))
GMAIL_FOLLOWUP_DAYS = (3, 7)
CALENDLY_BOOKING_URL = os.getenv("CALENDLY_BOOKING_URL", "").strip()
