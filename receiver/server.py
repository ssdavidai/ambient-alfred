"""FastAPI server for receiving audio from Omi wearable devices.

Receives raw PCM16 audio chunks via POST /audio, runs Silero VAD,
groups into segments via SmartChunker, transcribes via pluggable provider,
and saves transcripts as JSON files organized by date.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request, Response

from receiver.audio_utils import compute_duration_seconds
from receiver.chunker import SmartChunker
from receiver.config import (
    CHUNKER_CLEANUP_INTERVAL,
    CHUNKER_MAX_SEGMENT_DURATION,
    CHUNKER_MIN_SEGMENT_SPEECH,
    CHUNKER_SILENCE_THRESHOLD,
    CHUNKER_STALE_SESSION_TIMEOUT,
    QUEUE_DIR,
    RECEIVER_HOST,
    RECEIVER_PORT,
    TRANSCRIPTION_API_KEY,
    TRANSCRIPTION_LANGUAGE,
    TRANSCRIPTION_MODEL,
    TRANSCRIPTION_PROVIDER,
    TRANSCRIPTION_URL,
    TRANSCRIPTS_DIR,
)
from receiver.segment_queue import SegmentQueue
from receiver.storage import TranscriptStorage
from receiver.transcription import get_transcription_client

logger = logging.getLogger(__name__)

chunker: SmartChunker | None = None
transcription_client = None
storage: TranscriptStorage | None = None
segment_queue: SegmentQueue | None = None
_cleanup_task: asyncio.Task | None = None
_queue_worker_task: asyncio.Task | None = None


async def handle_segment_ready(uid: str, pcm_bytes: bytes, sample_rate: int, speech_seconds: float):
    """Called by chunker when a finalized audio segment is ready."""
    segment_queue.enqueue(uid, pcm_bytes, sample_rate, speech_seconds)


async def transcription_worker():
    """Background worker that processes the disk-backed segment queue."""
    while True:
        try:
            segment_id, pcm_bytes, meta = await segment_queue.get()
            uid = meta["uid"]
            sample_rate = meta["sample_rate"]
            speech_seconds = meta["speech_seconds"]
            audio_duration = meta["audio_duration"]

            logger.info(f"[{uid}] Transcribing {audio_duration:.1f}s segment ({segment_id})...")
            result = await transcription_client.transcribe(pcm_bytes, sample_rate)

            if result and result.get("text", "").strip():
                storage.save(
                    uid=uid,
                    text=result["text"],
                    language=result.get("language"),
                    audio_duration=audio_duration,
                    speech_duration=speech_seconds,
                )
                segment_queue.complete(segment_id)
            elif result:
                logger.info(f"[{uid}] Transcription returned empty text, skipping save")
                segment_queue.complete(segment_id)
            else:
                logger.warning(f"[{uid}] Transcription failed, re-enqueuing for retry in 30s")
                segment_queue.fail(segment_id, remove=False)
                await asyncio.sleep(30)
                segment_queue.requeue(segment_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error in transcription worker")
            await asyncio.sleep(5)


async def periodic_cleanup():
    """Periodically clean up stale chunker sessions."""
    while True:
        await asyncio.sleep(CHUNKER_CLEANUP_INTERVAL)
        try:
            stale = chunker.cleanup_stale_sessions()
            if stale:
                logger.info(f"Cleaned up {len(stale)} stale sessions: {stale}")
        except Exception:
            logger.exception("Error during session cleanup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global chunker, transcription_client, storage, segment_queue
    global _cleanup_task, _queue_worker_task

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    segment_queue = SegmentQueue(queue_dir=QUEUE_DIR)
    segment_queue.recover_pending()

    chunker = SmartChunker(
        silence_threshold=CHUNKER_SILENCE_THRESHOLD,
        max_segment_duration=CHUNKER_MAX_SEGMENT_DURATION,
        min_segment_speech=CHUNKER_MIN_SEGMENT_SPEECH,
        stale_session_timeout=CHUNKER_STALE_SESSION_TIMEOUT,
        on_segment_ready=handle_segment_ready,
    )

    transcription_client = get_transcription_client(
        provider=TRANSCRIPTION_PROVIDER,
        api_key=TRANSCRIPTION_API_KEY,
        url=TRANSCRIPTION_URL,
        model=TRANSCRIPTION_MODEL,
        language=TRANSCRIPTION_LANGUAGE,
    )
    storage = TranscriptStorage(base_dir=TRANSCRIPTS_DIR)

    await transcription_client.start()
    _cleanup_task = asyncio.create_task(periodic_cleanup())
    _queue_worker_task = asyncio.create_task(transcription_worker())

    recovered = segment_queue.stats["recovered"]
    logger.info(f"Receiver started on {RECEIVER_HOST}:{RECEIVER_PORT}")
    logger.info(f"Transcription provider: {TRANSCRIPTION_PROVIDER}")
    if recovered:
        logger.info(f"Resuming {recovered} segments from previous session")

    yield

    _cleanup_task.cancel()
    _queue_worker_task.cancel()
    try:
        await _cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await _queue_worker_task
    except asyncio.CancelledError:
        pass
    await transcription_client.close()
    logger.info("Receiver shut down")


app = FastAPI(title="Ambient Alfred — Omi Audio Receiver", lifespan=lifespan)


@app.post("/audio")
async def receive_audio(
    request: Request,
    uid: str = Query(default="default"),
    sample_rate: int = Query(default=16000),
):
    """Receive raw PCM16 audio chunk from Omi device."""
    try:
        pcm_bytes = await request.body()
        if not pcm_bytes:
            return Response(status_code=200, content="empty chunk")

        duration = compute_duration_seconds(len(pcm_bytes), sample_rate)
        logger.debug(f"[{uid}] Received {len(pcm_bytes)} bytes ({duration:.1f}s)")

        await chunker.process_chunk(uid, pcm_bytes, sample_rate)
    except Exception:
        logger.exception(f"[{uid}] Error processing chunk")

    # Always return 200 to Omi — never block the device
    return Response(status_code=200, content="ok")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/status")
async def status():
    """Debug endpoint showing chunker session states and transcription queue."""
    return {
        "sessions": chunker.get_session_status() if chunker else {},
        "transcription_queue": segment_queue.get_status() if segment_queue else {},
        "config": {
            "transcription_provider": TRANSCRIPTION_PROVIDER,
            "silence_threshold": CHUNKER_SILENCE_THRESHOLD,
            "max_segment_duration": CHUNKER_MAX_SEGMENT_DURATION,
            "transcripts_dir": TRANSCRIPTS_DIR,
        },
    }
