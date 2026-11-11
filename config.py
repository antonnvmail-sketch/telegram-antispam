import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name('.env'))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
CHAT_IDS = [int(x) for x in os.getenv("CHAT_IDS", "").split(",") if x]


def load_moderator_chats(raw):
    try:
        assignments = json.loads(raw)
        if not isinstance(assignments, dict):
            raise ValueError
        result = {}
        for actor, chats in assignments.items():
            if (not actor.isascii() or not actor.isdecimal() or int(actor) <= 0
                    or not isinstance(chats, list) or not chats
                    or any(type(chat) is not int or chat not in CHAT_IDS for chat in chats)):
                raise ValueError
            result[int(actor)] = frozenset(chats)
        return result
    except (ValueError, TypeError):
        raise ValueError("Set MODERATOR_CHAT_IDS to a JSON object mapping Telegram user IDs to connected chat IDs") from None


MODERATOR_CHAT_IDS = load_moderator_chats(os.getenv("MODERATOR_CHAT_IDS", "{}"))
DATASET_PATH = os.getenv("DATASET_PATH", "dataset.sqlite")
MODEL_PATH = os.getenv("MODEL_PATH", "model.joblib")
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "on"}
EXPERIMENTAL_REVIEW_ALERTS = os.getenv("EXPERIMENTAL_REVIEW_ALERTS", "false").strip().lower() in {
    "1", "true", "yes", "on",
}

# Порог для удаления и модерации
SPAM_DELETE_THRESHOLD = float(os.getenv("SPAM_DELETE_THRESHOLD", 0.7))
SPAM_MODERATE_THRESHOLD = float(os.getenv("SPAM_MODERATE_THRESHOLD", 0.4))

# Логирование
LOG_FILE = os.getenv("LOG_FILE", "bot.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 1048576))  # 1 MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 3))


def validate_runtime():
    """Check explicit local setup before creating clients or runtime files."""
    from aiogram.utils.token import validate_token
    if not TELEGRAM_TOKEN:
        raise ValueError("Set TELEGRAM_TOKEN in .env; see .env.example")
    validate_token(TELEGRAM_TOKEN)
    if not ADMIN_IDS or not CHAT_IDS:
        raise ValueError("Set ADMIN_IDS and CHAT_IDS in .env")
    if not 0 <= SPAM_MODERATE_THRESHOLD <= SPAM_DELETE_THRESHOLD <= 1:
        raise ValueError("Thresholds must satisfy 0 <= moderate <= delete <= 1")
    if not Path(MODEL_PATH).is_file():
        raise ValueError("No model. Run: python prepare_demo.py (or train your own local model)")
