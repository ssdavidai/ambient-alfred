"""Disk-backed queue for audio segments awaiting transcription.

Each segment is persisted as two files:
  - {id}.pcm  (raw audio bytes)
  - {id}.json (metadata: uid, sample_rate, speech_seconds, audio_duration, timestamp)

Segments survive process restarts.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from receiver.audio_utils import compute_duration_seconds

logger = logging.getLogger(__name__)


class SegmentQueue:
    def __init__(self, queue_dir: str = "queue"):
        self.queue_dir = Path(queue_dir)
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self._async_queue: asyncio.Queue = asyncio.Queue()
        self.stats = {
            "enqueued": 0,
            "completed": 0,
            "failed": 0,
            "in_flight": False,
            "in_flight_uid": None,
            "in_flight_since": None,
            "in_flight_id": None,
            "recovered": 0,
        }

    def enqueue(self, uid: str, pcm_bytes: bytes, sample_rate: int, speech_seconds: float):
        """Persist segment to disk and add to in-memory queue."""
        segment_id = f"{int(time.time() * 1000)}_{uid}"
        audio_duration = compute_duration_seconds(len(pcm_bytes), sample_rate)

        pcm_path = self.queue_dir / f"{segment_id}.pcm"
        pcm_path.write_bytes(pcm_bytes)

        meta = {
            "uid": uid,
            "sample_rate": sample_rate,
            "speech_seconds": speech_seconds,
            "audio_duration": audio_duration,
            "timestamp": time.time(),
        }
        meta_path = self.queue_dir / f"{segment_id}.json"
        meta_path.write_text(json.dumps(meta))

        self._async_queue.put_nowait(segment_id)
        self.stats["enqueued"] += 1

        depth = self._async_queue.qsize()
        logger.info(f"[{uid}] Queued segment {segment_id} ({audio_duration:.1f}s audio, queue depth: {depth})")

    def recover_pending(self):
        """On startup, re-enqueue any segments left on disk from previous runs."""
        meta_files = sorted(self.queue_dir.glob("*.json"))
        count = 0
        for meta_path in meta_files:
            segment_id = meta_path.stem
            pcm_path = self.queue_dir / f"{segment_id}.pcm"
            if pcm_path.exists():
                self._async_queue.put_nowait(segment_id)
                count += 1
            else:
                meta_path.unlink(missing_ok=True)
        if count:
            logger.info(f"Recovered {count} pending segments from disk")
        self.stats["recovered"] = count

    async def get(self) -> tuple[str, bytes, dict]:
        """Wait for next segment. Returns (segment_id, pcm_bytes, metadata)."""
        segment_id = await self._async_queue.get()

        pcm_path = self.queue_dir / f"{segment_id}.pcm"
        meta_path = self.queue_dir / f"{segment_id}.json"

        pcm_bytes = pcm_path.read_bytes()
        meta = json.loads(meta_path.read_text())

        self.stats["in_flight"] = True
        self.stats["in_flight_uid"] = meta["uid"]
        self.stats["in_flight_since"] = time.time()
        self.stats["in_flight_id"] = segment_id

        return segment_id, pcm_bytes, meta

    def complete(self, segment_id: str):
        """Mark segment as done: remove files from disk."""
        pcm_path = self.queue_dir / f"{segment_id}.pcm"
        meta_path = self.queue_dir / f"{segment_id}.json"
        pcm_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)

        self.stats["completed"] += 1
        self.stats["in_flight"] = False
        self.stats["in_flight_uid"] = None
        self.stats["in_flight_since"] = None
        self.stats["in_flight_id"] = None

    def fail(self, segment_id: str, remove: bool = False):
        """Mark segment as failed. If remove=True, delete from disk."""
        if remove:
            pcm_path = self.queue_dir / f"{segment_id}.pcm"
            meta_path = self.queue_dir / f"{segment_id}.json"
            pcm_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

        self.stats["failed"] += 1
        self.stats["in_flight"] = False
        self.stats["in_flight_uid"] = None
        self.stats["in_flight_since"] = None
        self.stats["in_flight_id"] = None

    def requeue(self, segment_id: str):
        """Re-add a failed segment to the in-memory queue for retry."""
        pcm_path = self.queue_dir / f"{segment_id}.pcm"
        meta_path = self.queue_dir / f"{segment_id}.json"
        if pcm_path.exists() and meta_path.exists():
            self._async_queue.put_nowait(segment_id)
            logger.info(f"Re-queued segment {segment_id} for retry (queue depth: {self._async_queue.qsize()})")

    def pending_count(self) -> int:
        return self._async_queue.qsize()

    def get_status(self) -> dict:
        info = {
            "pending": self._async_queue.qsize(),
            "on_disk": len(list(self.queue_dir.glob("*.json"))),
            "enqueued_total": self.stats["enqueued"],
            "completed_total": self.stats["completed"],
            "failed_total": self.stats["failed"],
            "recovered_on_startup": self.stats["recovered"],
            "in_flight": self.stats["in_flight"],
        }
        if self.stats["in_flight"] and self.stats["in_flight_since"]:
            info["in_flight_uid"] = self.stats["in_flight_uid"]
            info["in_flight_seconds"] = round(time.time() - self.stats["in_flight_since"], 1)
            info["in_flight_id"] = self.stats["in_flight_id"]
        return info
