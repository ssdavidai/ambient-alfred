"""Transcript scanner: groups JSON transcript files into conversations.

Scans the transcripts directory, groups segments by time proximity
(configurable gap = new conversation), and filters low-content noise.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from pipeline.config import (
    CONVERSATION_GAP_SECONDS,
    MIN_WORDS,
    STATE_FILE,
    TRANSCRIPTS_DIR,
)

logger = logging.getLogger(__name__)

MIN_SPEECH_SECONDS = 0.5


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"processed_files": [], "last_run": None}


def save_state(state: dict) -> None:
    """Atomically save state."""
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
        f.flush()
    os.replace(tmp, STATE_FILE)


def mark_files_processed(rel_paths: list[str]) -> None:
    """Mark files as processed in the state file."""
    state = load_state()
    processed = set(state.get("processed_files", []))
    processed.update(rel_paths)
    state["processed_files"] = sorted(processed)
    state["last_run"] = datetime.utcnow().isoformat()
    save_state(state)


def parse_ts(data: dict) -> datetime:
    try:
        return datetime.fromisoformat(data["timestamp"])
    except (KeyError, ValueError):
        return datetime.min


def scan_conversations(
    transcripts_dir: str | None = None,
    include_all: bool = False,
    since_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Scan transcripts and return list of conversation dicts.

    Each conversation dict has:
      date, time_range, languages, segments, duration_seconds,
      word_count, files, text
    """
    omi_dir = transcripts_dir or TRANSCRIPTS_DIR
    state = load_state()
    processed = set(state.get("processed_files", []))

    if not os.path.exists(omi_dir):
        return []

    # Load eligible transcript files
    transcripts = []
    for date_dir in sorted(os.listdir(omi_dir)):
        date_path = os.path.join(omi_dir, date_dir)
        if not os.path.isdir(date_path):
            continue
        if since_date and date_dir < since_date:
            continue
        if to_date and date_dir > to_date:
            continue

        for fname in sorted(os.listdir(date_path)):
            if not fname.endswith(".json"):
                continue
            rel_path = f"{date_dir}/{fname}"
            if not include_all and rel_path in processed:
                continue

            fpath = os.path.join(date_path, fname)
            try:
                with open(fpath) as f:
                    data = json.load(f)
                speech_dur = data.get("speech_duration_seconds", 0)
                text = data.get("text", "").strip()
                if speech_dur >= MIN_SPEECH_SECONDS and len(text.split()) >= 3:
                    data["_rel_path"] = rel_path
                    data["_date_dir"] = date_dir
                    transcripts.append(data)
            except (json.JSONDecodeError, IOError):
                continue

    transcripts.sort(key=parse_ts)
    if not transcripts:
        return []

    # Group into conversations (gap = new conversation)
    groups = []
    current = [transcripts[0]]
    for i in range(1, len(transcripts)):
        gap = (parse_ts(transcripts[i]) - parse_ts(transcripts[i - 1])).total_seconds()
        if gap > CONVERSATION_GAP_SECONDS:
            groups.append(current)
            current = [transcripts[i]]
        else:
            current.append(transcripts[i])
    groups.append(current)

    # Build conversation objects
    conversations = []
    for group in groups:
        total_words = sum(len(t.get("text", "").split()) for t in group)
        if total_words < MIN_WORDS:
            # Mark short conversations as processed to avoid re-checking
            short_paths = [t["_rel_path"] for t in group]
            mark_files_processed(short_paths)
            continue

        first_ts = group[0].get("timestamp", "")
        last_ts = group[-1].get("timestamp", "")
        languages = list(set(t.get("language", "unknown") for t in group))
        total_duration = sum(t.get("audio_duration_seconds", 0) for t in group)
        date = group[0].get("_date_dir", "unknown")
        time_range = f"{first_ts[11:16]}-{last_ts[11:16]}"
        file_paths = [t["_rel_path"] for t in group]

        segments = [f"[{t.get('timestamp', '')[11:16]}] {t.get('text', '').strip()}" for t in group]

        full_text = (
            f"Date: {date} | Time: {time_range} | "
            f"Languages: {', '.join(languages)} | "
            f"Segments: {len(group)} | Duration: {total_duration:.0f}s\n\n"
            + "\n\n".join(segments)
        )

        conversations.append({
            "date": date,
            "time_range": time_range,
            "languages": languages,
            "segments": len(group),
            "duration_seconds": round(total_duration),
            "word_count": total_words,
            "files": file_paths,
            "text": full_text,
        })

    return conversations
