"""Filesystem watcher for real-time Omi transcript processing.

Watches the transcripts directory for new .json segment files.
Groups segments into conversations (configurable gap = new conversation).
Debounces: waits for conversation to be "complete" before processing.
Detects commands directed at the agent and spawns subagents.
Non-command conversations are written to the vault inbox.
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Thread, Timer

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from pipeline.command_detector import classify_command, has_agent_mention
from pipeline.config import (
    AGENT_NAME,
    COMMAND_DETECTION_ENABLED,
    CONVERSATION_GAP_SECONDS,
    DEBOUNCE_SECONDS,
    MIN_WORDS,
    STATE_FILE,
    SUBAGENT_ID,
    TRANSCRIPTS_DIR,
)
from pipeline.inbox_writer import write_conversation_to_inbox
from pipeline.notifier import notify, spawn_subagent
from pipeline.scanner import load_state, mark_files_processed

logger = logging.getLogger("ambient-alfred.watcher")

MIN_SPEECH_SECONDS = 0.5
MIN_COMMAND_WORDS = 3

_work_queue: Queue = Queue()
_instant_queue: Queue = Queue()
_debounce_timer = None
_segments_buffer: list = []


# =============================================================================
# SEGMENT PARSING
# =============================================================================

def parse_segment(path: Path) -> dict | None:
    """Parse an Omi transcript JSON file. Returns dict with metadata or None."""
    try:
        data = json.loads(path.read_text())
        text = data.get("text", "").strip()
        speech_dur = data.get("speech_duration_seconds", 0)

        if speech_dur < MIN_SPEECH_SECONDS and len(text.split()) < 5:
            return None

        date_dir = path.parent.name
        rel_path = f"{date_dir}/{path.name}"

        ts_str = data.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            ts = datetime.min

        return {
            "text": text,
            "timestamp": ts,
            "timestamp_str": ts_str,
            "language": data.get("language", "unknown"),
            "audio_duration_seconds": data.get("audio_duration_seconds", 0),
            "speech_duration_seconds": speech_dur,
            "rel_path": rel_path,
            "abs_path": str(path),
            "date_dir": date_dir,
        }
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to parse {path}: {e}")
        return None


# =============================================================================
# CONVERSATION GROUPING
# =============================================================================

def group_segments_into_conversations(segments: list) -> list:
    """Group segments by time proximity. Returns list of conversation groups."""
    if not segments:
        return []

    sorted_segs = sorted(segments, key=lambda s: s["timestamp"])
    groups = []
    current = [sorted_segs[0]]

    for i in range(1, len(sorted_segs)):
        gap = (sorted_segs[i]["timestamp"] - sorted_segs[i - 1]["timestamp"]).total_seconds()
        if gap > CONVERSATION_GAP_SECONDS:
            groups.append(current)
            current = [sorted_segs[i]]
        else:
            current.append(sorted_segs[i])
    groups.append(current)

    return groups


def build_conversation(group: list) -> dict | None:
    """Build a conversation object from a group of segments. Returns None if too short."""
    total_words = sum(len(s["text"].split()) for s in group)
    if total_words < MIN_COMMAND_WORDS:
        return None

    first_ts = group[0]["timestamp_str"]
    last_ts = group[-1]["timestamp_str"]
    date = group[0]["date_dir"]
    time_range = f"{first_ts[11:16]}-{last_ts[11:16]}"
    languages = list(set(s["language"] for s in group))
    total_duration = sum(s["audio_duration_seconds"] for s in group)
    file_paths = [s["rel_path"] for s in group]

    segment_lines = [f"[{s['timestamp_str'][11:16]}] {s['text']}" for s in group]

    full_text = (
        f"Date: {date} | Time: {time_range} | "
        f"Languages: {', '.join(languages)} | "
        f"Segments: {len(group)} | Duration: {total_duration:.0f}s\n\n"
        + "\n\n".join(segment_lines)
    )

    return {
        "date": date,
        "time_range": time_range,
        "languages": languages,
        "segments": len(group),
        "duration_seconds": round(total_duration),
        "word_count": total_words,
        "files": file_paths,
        "text": full_text,
    }


# =============================================================================
# CONVERSATION PROCESSING
# =============================================================================

def process_conversation(conversation: dict) -> None:
    """Process a finalized conversation: detect commands, write to inbox, notify."""
    date = conversation["date"]
    time_range = conversation["time_range"]
    word_count = conversation["word_count"]
    duration = conversation.get("duration_seconds", 0)
    text = conversation["text"]
    files = conversation["files"]
    is_instant = conversation.get("instant", False)

    duration_str = f"{duration // 60}m{duration % 60}s" if duration else "unknown"
    logger.info(f"Processing conversation: {date} {time_range} ({word_count} words)")

    # 1. Write to vault inbox
    inbox_file = write_conversation_to_inbox(conversation)

    # 2. Notify
    if inbox_file:
        notify(
            f"Omi conversation ({date} {time_range}, {duration_str}) "
            f"| {word_count} words | -> inbox/{inbox_file}"
        )

    # 3. Command detection
    if COMMAND_DETECTION_ENABLED and has_agent_mention(text):
        is_command = is_instant or classify_command(text)
        if is_command:
            logger.info(f"Command detected in conversation {date} {time_range}")
            task = (
                f"Omi ambient transcript from {date} {time_range} ({duration_str}).\n"
                f"This was flagged because it contains a command directed at {AGENT_NAME}.\n\n"
                f"TRANSCRIPT:\n{text}\n\n"
                f"Execute any commands or action items directed at {AGENT_NAME}. "
                f"If nothing actionable, reply NO_REPLY."
            )
            result = spawn_subagent(task, SUBAGENT_ID)
            logger.info(f"SubAgent dispatch: {result}")

    # 4. Mark files as processed
    mark_files_processed(files)
    logger.info(f"Marked {len(files)} files as processed")


# =============================================================================
# WORKER THREAD
# =============================================================================

def _worker_loop():
    """Process work queues in a dedicated thread."""
    while True:
        try:
            # Instant queue has priority
            try:
                conversation = _instant_queue.get_nowait()
                logger.info("Processing instant command")
                process_conversation(conversation)
                continue
            except Empty:
                pass

            conversation = _work_queue.get(timeout=0.5)
            process_conversation(conversation)
        except Empty:
            continue
        except Exception as e:
            logger.error(f"Worker loop error: {e}")


# =============================================================================
# DEBOUNCE + FINALIZATION
# =============================================================================

def finalize_conversations():
    """Called after debounce period. Group buffered segments and process."""
    global _segments_buffer

    if not _segments_buffer:
        return

    state = load_state()
    processed = set(state.get("processed_files", []))

    pending = [s for s in _segments_buffer if s["rel_path"] not in processed]
    if not pending:
        _segments_buffer.clear()
        return

    logger.info(f"Finalizing: {len(pending)} pending segments")

    groups = group_segments_into_conversations(pending)

    for group in groups:
        conv = build_conversation(group)
        if conv is None:
            short_paths = [s["rel_path"] for s in group]
            total_words = sum(len(s["text"].split()) for s in group)
            if total_words < MIN_WORDS:
                mark_files_processed(short_paths)
                logger.info(f"Skipping short conversation ({total_words} words)")
            continue
        if conv["word_count"] < MIN_WORDS:
            mark_files_processed(conv["files"])
            logger.info(f"Skipping low-content conversation ({conv['word_count']} words)")
            continue
        _work_queue.put(conv)

    _segments_buffer.clear()


def reset_debounce():
    """Reset the debounce timer. Called every time a new segment arrives."""
    global _debounce_timer

    if _debounce_timer is not None:
        _debounce_timer.cancel()

    _debounce_timer = Timer(DEBOUNCE_SECONDS, finalize_conversations)
    _debounce_timer.daemon = True
    _debounce_timer.start()


# =============================================================================
# FILESYSTEM HANDLER
# =============================================================================

class TranscriptHandler(FileSystemEventHandler):
    _seen: set = set()

    def on_created(self, event):
        if event.is_directory:
            return
        path = event.src_path
        if not path.endswith(".json"):
            return
        if Path(path).name.startswith("."):
            return
        if path in self._seen:
            return
        self._seen.add(path)

        segment = parse_segment(Path(path))
        if segment is None:
            return

        state = load_state()
        if segment["rel_path"] in set(state.get("processed_files", [])):
            return

        word_count = len(segment["text"].split())
        logger.info(f"New segment: {segment['rel_path']} ({word_count} words)")

        # Instant command detection — classify before debounce
        if (
            COMMAND_DETECTION_ENABLED
            and word_count >= MIN_COMMAND_WORDS
            and has_agent_mention(segment["text"])
            and classify_command(segment["text"])
        ):
            logger.info(f"INSTANT COMMAND detected: {segment['text'][:80]}")
            instant_conv = build_conversation([segment])
            if instant_conv:
                instant_conv["instant"] = True
                _instant_queue.put(instant_conv)
                mark_files_processed([segment["rel_path"]])
                return

        _segments_buffer.append(segment)
        reset_debounce()


# =============================================================================
# STARTUP RECOVERY
# =============================================================================

def scan_existing_unprocessed():
    """On startup, scan for any unprocessed segments and add to buffer."""
    omi_dir = Path(TRANSCRIPTS_DIR)
    if not omi_dir.exists():
        return

    state = load_state()
    processed = set(state.get("processed_files", []))
    count = 0

    for date_dir in sorted(omi_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        for json_file in sorted(date_dir.glob("*.json")):
            rel_path = f"{date_dir.name}/{json_file.name}"
            if rel_path in processed:
                continue
            segment = parse_segment(json_file)
            if segment is not None:
                _segments_buffer.append(segment)
                count += 1

    if count > 0:
        logger.info(f"Found {count} unprocessed segments on startup, starting debounce")
        reset_debounce()
    else:
        logger.info("No unprocessed segments found on startup")


# =============================================================================
# MAIN
# =============================================================================

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    omi_dir = Path(TRANSCRIPTS_DIR)
    omi_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Ambient Alfred pipeline watcher starting...")
    logger.info(f"Watching: {omi_dir}")
    logger.info(f"State file: {STATE_FILE}")
    logger.info(f"Debounce: {DEBOUNCE_SECONDS}s | Gap: {CONVERSATION_GAP_SECONDS}s | Min words: {MIN_WORDS}")
    logger.info(f"Command detection: {'enabled' if COMMAND_DETECTION_ENABLED else 'disabled'}")
    if COMMAND_DETECTION_ENABLED:
        logger.info(f"Agent name: {AGENT_NAME} | SubAgent: {SUBAGENT_ID}")

    # Start worker thread
    worker = Thread(target=_worker_loop, daemon=True)
    worker.start()

    # Scan existing unprocessed segments
    scan_existing_unprocessed()

    # Start filesystem watcher
    handler = TranscriptHandler()
    observer = Observer()
    observer.schedule(handler, str(omi_dir), recursive=True)
    observer.start()

    logger.info("Watcher running. Waiting for new transcript segments...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        if _debounce_timer is not None:
            _debounce_timer.cancel()
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
