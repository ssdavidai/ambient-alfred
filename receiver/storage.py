"""Transcript storage: saves transcribed audio segments as JSON files."""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class TranscriptStorage:
    def __init__(self, base_dir: str = "transcripts"):
        self.base_dir = Path(base_dir)

    def save(
        self,
        uid: str,
        text: str,
        language: str | None = None,
        audio_duration: float = 0.0,
        speech_duration: float = 0.0,
    ) -> Path:
        """Save a transcript as a JSON file organized by date. Returns the file path."""
        now = datetime.now()
        date_dir = self.base_dir / now.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        base_name = f"{now.strftime('%H-%M-%S')}_{uid}"
        file_path = date_dir / f"{base_name}.json"

        counter = 1
        while file_path.exists():
            file_path = date_dir / f"{base_name}_{counter}.json"
            counter += 1

        record = {
            "uid": uid,
            "text": text,
            "language": language,
            "timestamp": now.isoformat(),
            "audio_duration_seconds": round(audio_duration, 2),
            "speech_duration_seconds": round(speech_duration, 2),
        }

        file_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        logger.info(f"Transcript saved: {file_path}")
        return file_path
