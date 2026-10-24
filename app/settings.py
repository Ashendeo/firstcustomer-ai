from dotenv import load_dotenv
load_dotenv()
import os


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


DEMO_MODE = env_bool("DEMO_MODE", "true")
CRUSTDATA_API_KEY = os.getenv("CRUSTDATA_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3:4b")
QWEN_MODEL_SERVER = os.getenv("QWEN_MODEL_SERVER", "http://localhost:11434/v1")
QWEN_TIMEOUT_SECONDS = max(15, min(int(os.getenv("QWEN_TIMEOUT_SECONDS", "120")), 600))
QWEN_NUM_PREDICT = max(150, min(int(os.getenv("QWEN_NUM_PREDICT", "900")), 3000))
QWEN_PLAN_WITH_MODEL = env_bool("QWEN_PLAN_WITH_MODEL", "false")
MAX_COMPANIES = max(1, min(int(os.getenv("MAX_COMPANIES", "5")), 10))
MAX_PEOPLE_SEARCHES = max(0, min(int(os.getenv("MAX_PEOPLE_SEARCHES", "3")), 10))
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "")
CALENDLY_SIGNING_KEY = os.getenv("CALENDLY_SIGNING_KEY", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
CSRF_SECRET = os.getenv("CSRF_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
