import os
import sys
import logging
from pathlib import Path


# Determine Project Paths
CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent

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
        return default

def _get_float(key: str, default: float = 0.0) -> float:
    val = os.getenv(key)
    if val is None or not val.strip():
        return default
    try:
        return float(val.strip())
    except ValueError:
        return default

# 1. Channel Toggles
ENABLE_WECHAT = _get_bool("ENABLE_WECHAT", False)

# 2. AI Provider Configuration
# Supported providers: "agy" (default) | "openai" (DeepSeek and other compatible endpoints)
AI_PROVIDER = os.getenv("AI_PROVIDER", "agy").strip().lower()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat").strip()
LLM_TEMPERATURE = _get_float("LLM_TEMPERATURE", 0.1)

AGY_BIN_PATH = os.getenv("AGY_BIN_PATH", "agy").strip()
AGY_MODEL = os.getenv("AGY_MODEL", "gemini-3.8-flash-low").strip()

# 3. WeChat Channel Configuration
WECHAT_BASE_URL = os.getenv("WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com").rstrip("/")
WECHAT_DATA_DIR = Path(os.getenv("WECHAT_DATA_DIR", str(PROJECT_ROOT / "data" / "wechat")))

# 4. Microsoft To Do Integration
ENABLE_MS_TODO = _get_bool("ENABLE_MS_TODO", True)
MS_TODO_DEFAULT_LIST_ID = os.getenv("MS_TODO_DEFAULT_LIST_ID", "").strip()
MS_TODO_AUTH_MODULE_PATH = os.getenv(
    "MS_TODO_AUTH_MODULE_PATH",
    "/usr/lib/node_modules/@mag-cie/mcp-microsoft-todo/dist/auth.js"
)
DEFAULT_REMINDER_ADVANCE_MINUTES = _get_int("DEFAULT_REMINDER_ADVANCE_MINUTES", 15)

# 5. Persistence & Logging
HISTORY_FILE = Path(os.getenv("HISTORY_FILE_PATH", str(PROJECT_ROOT / "data" / "group_history.json")))
MEMORY_FILE = Path(os.getenv("MEMORY_FILE_PATH", str(PROJECT_ROOT / "data" / "synced_todos.json")))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

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
