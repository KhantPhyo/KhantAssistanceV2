from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from cryptography.fernet import Fernet


class Settings(BaseSettings):
    JWT_SECRET: str = "dev-secret-change-me"
    FERNET_KEY: str = ""
    DEFAULT_ADMIN_EMAIL: str = "khantphyo.myanmar@gmail.com"
    DEFAULT_ADMIN_PASSWORD: str = "Cisco@123"
    DEFAULT_ADMIN_TG_USERNAME: str = ""
    DEFAULT_REMINDER_MINUTES: int = 15
    TIMEZONE: str = "Asia/Yangon"
    UPLOAD_DIR: str = "./uploads"
    DB_PATH: str = "./data/app.db"
    CORS_ORIGINS: str = "http://localhost:5173"
    RATE_LIMIT_PER_MIN: int = 30

    # ---- Notion sync ----
    NOTION_TOKEN: str = ""
    NOTION_JOBS_DATABASE_ID: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_PATH = (BASE_DIR / settings.UPLOAD_DIR).resolve() if not Path(settings.UPLOAD_DIR).is_absolute() else Path(settings.UPLOAD_DIR)
DB_FILE = (BASE_DIR / settings.DB_PATH).resolve() if not Path(settings.DB_PATH).is_absolute() else Path(settings.DB_PATH)
UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
(UPLOAD_PATH / "reports").mkdir(parents=True, exist_ok=True)
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

# Persist Fernet key across restarts
FERNET_KEY_FILE = DB_FILE.parent / ".fernet_key"
if not settings.FERNET_KEY:
    if FERNET_KEY_FILE.exists():
        settings.FERNET_KEY = FERNET_KEY_FILE.read_text().strip()
    else:
        settings.FERNET_KEY = Fernet.generate_key().decode()
        FERNET_KEY_FILE.write_text(settings.FERNET_KEY)
        try:
            FERNET_KEY_FILE.chmod(0o600)
        except Exception:
            pass

fernet = Fernet(settings.FERNET_KEY.encode() if isinstance(settings.FERNET_KEY, str) else settings.FERNET_KEY)


def encrypt(text: str) -> str:
    if not text:
        return text
    return fernet.encrypt(text.encode()).decode()


def decrypt(token: str) -> str | None:
    if not token:
        return token
    try:
        return fernet.decrypt(token.encode()).decode()
    except Exception:
        return None
