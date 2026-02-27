"""Configuration for the Ambient Alfred conversation pipeline.

All settings are driven by environment variables with sensible defaults.
"""

import os


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_int(key: str, default: int = 0) -> int:
    return int(os.environ.get(key, str(default)))


def env_float(key: str, default: float = 0.0) -> float:
    return float(os.environ.get(key, str(default)))


def env_bool(key: str, default: bool = True) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes")


# --- Transcripts ---
TRANSCRIPTS_DIR = env("ALFRED_TRANSCRIPTS_DIR", "transcripts")

# --- Conversations ---
CONVERSATION_GAP_SECONDS = env_int("ALFRED_CONVERSATION_GAP_SECONDS", 600)
MIN_WORDS = env_int("ALFRED_MIN_WORDS", 30)
DEBOUNCE_SECONDS = env_int("ALFRED_DEBOUNCE_SECONDS", 120)

# --- Command detection ---
COMMAND_DETECTION_ENABLED = env_bool("ALFRED_COMMAND_DETECTION_ENABLED", True)
AGENT_NAME = env("ALFRED_AGENT_NAME", "Alfred")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = env("ALFRED_OPENROUTER_MODEL", "google/gemini-2.0-flash-001")
SUBAGENT_ID = env("ALFRED_SUBAGENT_ID", "subalfred")

# --- Storage ---
VAULT_INBOX_DIR = os.path.expanduser(env("ALFRED_VAULT_INBOX_DIR", "~/vault/inbox"))
STATE_FILE = env("ALFRED_STATE_FILE", ".ambient-alfred-state.json")

# --- Notifications ---
NOTIFICATION_CHANNEL = env("ALFRED_NOTIFICATION_CHANNEL", "")
NOTIFICATION_CHANNEL_TYPE = env("ALFRED_NOTIFICATION_CHANNEL_TYPE", "slack")

# --- OpenClaw Gateway ---
GATEWAY_URL = env("OPENCLAW_GATEWAY_URL", "http://localhost:18789")
GATEWAY_TOKEN = env("OPENCLAW_GATEWAY_TOKEN", "")
