"""Configuration for the Ambient Alfred receiver.

All settings are driven by environment variables with sensible defaults.
"""

import os


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_int(key: str, default: int = 0) -> int:
    return int(os.environ.get(key, str(default)))


def env_float(key: str, default: float = 0.0) -> float:
    return float(os.environ.get(key, str(default)))


# --- Receiver ---
RECEIVER_HOST = env("ALFRED_RECEIVER_HOST", "0.0.0.0")
RECEIVER_PORT = env_int("ALFRED_RECEIVER_PORT", 8080)

# --- Transcription ---
TRANSCRIPTION_PROVIDER = env("ALFRED_TRANSCRIPTION_PROVIDER", "assemblyai")
TRANSCRIPTION_API_KEY = env("ALFRED_TRANSCRIPTION_API_KEY", "")
TRANSCRIPTION_URL = env("ALFRED_TRANSCRIPTION_URL", "")
TRANSCRIPTION_MODEL = env("ALFRED_TRANSCRIPTION_MODEL", "")
TRANSCRIPTION_LANGUAGE = env("ALFRED_TRANSCRIPTION_LANGUAGE", "")

# --- Chunker ---
CHUNKER_SILENCE_THRESHOLD = env_float("ALFRED_CHUNKER_SILENCE_THRESHOLD", 10.0)
CHUNKER_MAX_SEGMENT_DURATION = env_float("ALFRED_CHUNKER_MAX_SEGMENT_DURATION", 300.0)
CHUNKER_MIN_SEGMENT_SPEECH = env_float("ALFRED_CHUNKER_MIN_SEGMENT_SPEECH", 0.5)
CHUNKER_STALE_SESSION_TIMEOUT = env_float("ALFRED_CHUNKER_STALE_TIMEOUT", 120.0)
CHUNKER_CLEANUP_INTERVAL = env_float("ALFRED_CHUNKER_CLEANUP_INTERVAL", 60.0)

# --- Storage ---
TRANSCRIPTS_DIR = env("ALFRED_TRANSCRIPTS_DIR", "transcripts")
QUEUE_DIR = env("ALFRED_QUEUE_DIR", "queue")
