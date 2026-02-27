"""SmartChunker: Silero VAD-based audio segmentation.

Groups incoming PCM16 audio chunks into speech segments using a
state machine: IDLE -> SPEECH -> TRAILING_SILENCE -> (finalize) -> IDLE.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable

import numpy as np
import torch

from receiver.audio_utils import compute_duration_seconds, pcm16_bytes_to_float32

logger = logging.getLogger(__name__)


class ChunkerState(Enum):
    IDLE = "idle"
    SPEECH = "speech"
    TRAILING_SILENCE = "trailing_silence"


@dataclass
class ChunkerSession:
    uid: str
    state: ChunkerState = ChunkerState.IDLE
    buffer: bytearray = field(default_factory=bytearray)
    total_speech_seconds: float = 0.0
    silence_start_time: float = 0.0
    last_chunk_time: float = field(default_factory=time.monotonic)
    sample_rate: int = 16000


class SmartChunker:
    def __init__(
        self,
        silence_threshold: float = 10.0,
        max_segment_duration: float = 300.0,
        min_segment_speech: float = 0.5,
        stale_session_timeout: float = 120.0,
        on_segment_ready: Callable[[str, bytes, int, float], Awaitable[None]] | None = None,
    ):
        self.silence_threshold = silence_threshold
        self.max_segment_duration = max_segment_duration
        self.min_segment_speech = min_segment_speech
        self.stale_session_timeout = stale_session_timeout
        self.on_segment_ready = on_segment_ready
        self.sessions: dict[str, ChunkerSession] = {}
        self._vad_model = None
        self._vad_utils = None

    def _load_vad(self):
        if self._vad_model is not None:
            return
        logger.info("Loading Silero VAD model...")
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        self._vad_model = model
        self._vad_utils = utils
        logger.info("Silero VAD model loaded")

    def _get_or_create_session(self, uid: str, sample_rate: int) -> ChunkerSession:
        if uid not in self.sessions:
            self.sessions[uid] = ChunkerSession(uid=uid, sample_rate=sample_rate)
        session = self.sessions[uid]
        session.last_chunk_time = time.monotonic()
        return session

    def _analyze_chunk_sync(self, audio_float32: np.ndarray, sample_rate: int) -> tuple[bool, float]:
        """Run Silero VAD on a chunk (synchronous). Returns (has_speech, speech_duration_seconds)."""
        self._load_vad()
        get_speech_timestamps = self._vad_utils[0]
        audio_tensor = torch.from_numpy(audio_float32)
        self._vad_model.reset_states()

        try:
            timestamps = get_speech_timestamps(
                audio_tensor,
                self._vad_model,
                sampling_rate=sample_rate,
                return_seconds=True,
            )
        except Exception:
            logger.exception("VAD analysis failed")
            return False, 0.0

        if not timestamps:
            return False, 0.0

        speech_duration = sum(ts["end"] - ts["start"] for ts in timestamps)
        return True, speech_duration

    async def _analyze_chunk(self, audio_float32: np.ndarray, sample_rate: int) -> tuple[bool, float]:
        """Run VAD in a thread pool to avoid blocking the event loop."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._analyze_chunk_sync, audio_float32, sample_rate)

    async def process_chunk(self, uid: str, pcm_bytes: bytes, sample_rate: int = 16000):
        """Main entry point: process one PCM16 chunk from a device."""
        session = self._get_or_create_session(uid, sample_rate)

        audio_float32 = pcm16_bytes_to_float32(pcm_bytes)
        has_speech, speech_duration = await self._analyze_chunk(audio_float32, sample_rate)

        now = time.monotonic()

        if session.state == ChunkerState.IDLE:
            if has_speech:
                session.state = ChunkerState.SPEECH
                session.buffer = bytearray(pcm_bytes)
                session.total_speech_seconds = speech_duration
                logger.info(f"[{uid}] IDLE -> SPEECH (speech={speech_duration:.1f}s)")

        elif session.state == ChunkerState.SPEECH:
            session.buffer.extend(pcm_bytes)
            if has_speech:
                session.total_speech_seconds += speech_duration
            else:
                session.state = ChunkerState.TRAILING_SILENCE
                session.silence_start_time = now
                logger.info(f"[{uid}] SPEECH -> TRAILING_SILENCE")

        elif session.state == ChunkerState.TRAILING_SILENCE:
            session.buffer.extend(pcm_bytes)
            if has_speech:
                session.state = ChunkerState.SPEECH
                session.total_speech_seconds += speech_duration
                logger.info(f"[{uid}] TRAILING_SILENCE -> SPEECH (speech={speech_duration:.1f}s)")
            else:
                silence_elapsed = now - session.silence_start_time
                if silence_elapsed >= self.silence_threshold:
                    logger.info(f"[{uid}] TRAILING_SILENCE -> IDLE (silence={silence_elapsed:.1f}s, finalizing)")
                    self._finalize_segment(session)

        # Max duration guard
        buffer_duration = compute_duration_seconds(len(session.buffer), sample_rate)
        if buffer_duration >= self.max_segment_duration and session.state != ChunkerState.IDLE:
            logger.warning(f"[{uid}] Max duration reached ({buffer_duration:.0f}s), force-finalizing")
            self._finalize_segment(session)

    def _finalize_segment(self, session: ChunkerSession):
        """Finalize a segment: validate, trim, and dispatch to background queue."""
        uid = session.uid
        pcm_bytes = bytes(session.buffer)
        speech_seconds = session.total_speech_seconds
        sample_rate = session.sample_rate

        # Reset session
        session.state = ChunkerState.IDLE
        session.buffer = bytearray()
        session.total_speech_seconds = 0.0
        session.silence_start_time = 0.0

        if speech_seconds < self.min_segment_speech:
            logger.info(f"[{uid}] Segment discarded: only {speech_seconds:.2f}s of speech")
            return

        # Trim trailing silence
        trim_bytes = int(self.silence_threshold * sample_rate * 2)
        if len(pcm_bytes) > trim_bytes:
            pcm_bytes = pcm_bytes[:-trim_bytes]

        duration = compute_duration_seconds(len(pcm_bytes), sample_rate)
        logger.info(f"[{uid}] Segment ready: {duration:.1f}s audio, {speech_seconds:.1f}s speech")

        if self.on_segment_ready:
            asyncio.create_task(self.on_segment_ready(uid, pcm_bytes, sample_rate, speech_seconds))

    def cleanup_stale_sessions(self) -> list[str]:
        """Remove sessions that haven't received data recently."""
        now = time.monotonic()
        stale_uids = [
            uid
            for uid, session in self.sessions.items()
            if now - session.last_chunk_time > self.stale_session_timeout
        ]
        for uid in stale_uids:
            logger.info(f"[{uid}] Cleaning up stale session (state={self.sessions[uid].state.value})")
            del self.sessions[uid]
        return stale_uids

    def get_session_status(self) -> dict:
        """Return status of all sessions for debugging."""
        now = time.monotonic()
        return {
            uid: {
                "state": session.state.value,
                "buffer_duration": compute_duration_seconds(len(session.buffer), session.sample_rate),
                "total_speech_seconds": session.total_speech_seconds,
                "idle_seconds": now - session.last_chunk_time,
            }
            for uid, session in self.sessions.items()
        }
