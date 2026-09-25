import os
import sys
import logging
from pathlib import Path
from typing import List

# Determine Project Paths
CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
CONFIG_ERRORS: List[str] = []

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env file with native parser fallback
def _load_env_file(env_file_path: Path):
    if not env_file_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_file_path)
    except ImportError:
        try:
            with open(env_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse .env file: {e}\n")

_load_env_file(PROJECT_ROOT / ".env")
_load_env_file(Path(".env"))

def _get_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "y", "on")

def _get_int(key: str, default: int = 0) -> int:
    val = os.getenv(key)
    if val is None or not val.strip():
        return default
    try:
        return int(val.strip())
    except ValueError:
        CONFIG_ERRORS.append(f"{key} must be an integer")
        return default

def _get_float(key: str, default: float = 0.0) -> float:
    val = os.getenv(key)
    if val is None or not val.strip():
        return default
    try:
        return float(val.strip())
    except ValueError:
        CONFIG_ERRORS.append(f"{key} must be a number")
        return default

# 1. Channel Toggles
ENABLE_QQ = _get_bool("ENABLE_QQ", False)
ENABLE_WECHAT = _get_bool("ENABLE_WECHAT", False)

# 2. AI Provider Configuration
# Supported: openai (HTTP), agy (default), codex, opencode, claude (local CLIs).
AI_PROVIDER = os.getenv("AI_PROVIDER", "agy").strip().lower()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat").strip()
LLM_TEMPERATURE = _get_float("LLM_TEMPERATURE", 0.1)
AUTO_INSTALL_CLI = _get_bool("AUTO_INSTALL_CLI", False)

# 3. QQ Channel Configuration
ADMIN_QQ = _get_int("ADMIN_QQ", 0)
_raw_groups = os.getenv("TARGET_GROUP_IDS", "").strip()
TARGET_GROUP_IDS: List[int] = []
if _raw_groups:
    for g in _raw_groups.split(","):
        g = g.strip()
        if g.isdigit():
            TARGET_GROUP_IDS.append(int(g))

NAPCAT_HTTP_URL = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3000").rstrip("/")
NAPCAT_WS_URL = os.getenv("NAPCAT_WS_URL", "ws://127.0.0.1:3001")
NAPCAT_TOKEN = os.getenv("NAPCAT_TOKEN", "").strip()

# 4. WeChat Channel Configuration
WECHAT_BASE_URL = os.getenv("WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com").rstrip("/")
WECHAT_DATA_DIR = Path(os.getenv("WECHAT_DATA_DIR", str(PROJECT_ROOT / "data" / "wechat")))

# 5. Microsoft To Do Integration
ENABLE_MS_TODO = _get_bool("ENABLE_MS_TODO", True)
MS_TODO_DEFAULT_LIST_ID = os.getenv("MS_TODO_DEFAULT_LIST_ID", "").strip()
MS_TODO_AUTH_MODULE_PATH = os.getenv(
    "MS_TODO_AUTH_MODULE_PATH",
    "/usr/lib/node_modules/@mag-cie/mcp-microsoft-todo/dist/auth.js"
)
DEFAULT_REMINDER_ADVANCE_MINUTES = _get_int("DEFAULT_REMINDER_ADVANCE_MINUTES", 15)

# 6. Persistence & Logging
HISTORY_FILE = Path(os.getenv("HISTORY_FILE_PATH", str(PROJECT_ROOT / "data" / "group_history.json")))
MEMORY_FILE = Path(os.getenv("MEMORY_FILE_PATH", str(PROJECT_ROOT / "data" / "synced_todos.json")))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def validate_config() -> List[str]:
    """Return actionable configuration errors without making imports fail."""
    errors = list(CONFIG_ERRORS)
    if AI_PROVIDER not in {"openai", "deepseek", "default", "agy", "codex", "opencode", "claude"}:
        errors.append("AI_PROVIDER must be openai, agy, codex, opencode, or claude")
    if not 0 <= LLM_TEMPERATURE <= 2:
        errors.append("LLM_TEMPERATURE must be between 0 and 2")
    if DEFAULT_REMINDER_ADVANCE_MINUTES < 0:
        errors.append("DEFAULT_REMINDER_ADVANCE_MINUTES cannot be negative")
    if ENABLE_QQ:
        if ADMIN_QQ <= 0:
            errors.append("ADMIN_QQ must be a positive integer when ENABLE_QQ=true")
        if not TARGET_GROUP_IDS:
            errors.append("TARGET_GROUP_IDS must contain at least one group when ENABLE_QQ=true")
        if not NAPCAT_HTTP_URL.startswith(("http://", "https://")):
            errors.append("NAPCAT_HTTP_URL must be an HTTP(S) URL")
        if not NAPCAT_WS_URL.startswith(("ws://", "wss://")):
            errors.append("NAPCAT_WS_URL must be a WebSocket URL")
    if ENABLE_WECHAT and not WECHAT_BASE_URL.startswith(("http://", "https://")):
        errors.append("WECHAT_BASE_URL must be an HTTP(S) URL")
    if ENABLE_MS_TODO and not MS_TODO_AUTH_MODULE_PATH:
        errors.append("MS_TODO_AUTH_MODULE_PATH is required when ENABLE_MS_TODO=true")
    return errors

# Ensure storage directories exist
HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
WECHAT_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Logger setup helper
def setup_logging():
    numeric_level = getattr(logging, LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
