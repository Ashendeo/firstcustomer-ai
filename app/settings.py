from dotenv import load_dotenv
load_dotenv()
import os


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


DEMO_MODE = env_bool("DEMO_MODE", "true")
CRUSTDATA_API_KEY = os.getenv("CRUSTDATA_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MAX_COMPANIES = max(1, min(int(os.getenv("MAX_COMPANIES", "5")), 10))
MAX_PEOPLE_SEARCHES = max(0, min(int(os.getenv("MAX_PEOPLE_SEARCHES", "3")), 10))
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "")
CALENDLY_SIGNING_KEY = os.getenv("CALENDLY_SIGNING_KEY", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
CSRF_SECRET = os.getenv("CSRF_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
