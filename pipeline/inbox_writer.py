"""Vault inbox writer: generates markdown files with YAML frontmatter."""

import logging
from pathlib import Path

from pipeline.config import VAULT_INBOX_DIR

logger = logging.getLogger(__name__)


def write_conversation_to_inbox(conversation: dict) -> str | None:
    """Write a conversation to the vault inbox as markdown with YAML frontmatter.

    Returns the filename on success, None on failure.
    """
    date = conversation.get("date", "unknown")
    time_range = conversation.get("time_range", "unknown")
    languages = conversation.get("languages", ["unknown"])
    languages_str = ", ".join(languages)
    segments = conversation.get("segments", 0)
    duration = conversation.get("duration_seconds", 0)
    word_count = conversation.get("word_count", 0)
    text = conversation.get("text", "")

    duration_str = f"{duration // 60}m{duration % 60}s" if duration else "unknown"
    time_slug = time_range.replace(":", "").replace("-", "-")
    filename = f"omi-{date}-{time_slug}.md"

    inbox_dir = Path(VAULT_INBOX_DIR)

    frontmatter = (
        f"---\n"
        f"type: omi-transcript\n"
        f"date: {date}\n"
        f"time: \"{time_range}\"\n"
        f"duration: \"{duration_str}\"\n"
        f"segments: {segments}\n"
        f"languages: \"{languages_str}\"\n"
        f"words: {word_count}\n"
        f"---\n"
    )

    body = (
        f"\n# Omi Conversation — {date} {time_range}\n\n"
        f"Duration: {duration_str} | Languages: {languages_str} "
        f"| Segments: {segments}\n\n"
        f"{text}\n"
    )

    try:
        inbox_dir.mkdir(parents=True, exist_ok=True)
        inbox_path = inbox_dir / filename
        inbox_path.write_text(frontmatter + body, encoding="utf-8")
        logger.info(f"Wrote to vault inbox: {filename}")
        return filename
    except Exception as e:
        logger.error(f"Failed to write to vault inbox: {e}")
        return None
