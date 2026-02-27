"""Pluggable transcription clients.

Supported providers:
  - assemblyai: AssemblyAI Universal-2 with language detection
  - whisper_compatible: Any OpenAI Whisper-compatible API endpoint
  - openai: Official OpenAI Whisper API
  - passthrough: No-op (for pre-transcribed audio or testing)
"""

import asyncio
import logging
import os

import httpx

from receiver.audio_utils import pcm16_bytes_to_wav_bytes

logger = logging.getLogger(__name__)


class BaseTranscriptionClient:
    async def start(self):
        pass

    async def close(self):
        pass

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int = 16000) -> dict | None:
        raise NotImplementedError


class PassthroughClient(BaseTranscriptionClient):
    """No-op client — returns empty result. Useful when Omi sends pre-transcribed text."""

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int = 16000) -> dict | None:
        return {"text": "", "language": "unknown", "duration": 0}


class WhisperCompatibleClient(BaseTranscriptionClient):
    """Client for any Whisper-compatible API (LocalAI, faster-whisper-server, etc.)."""

    def __init__(self, url: str, model: str = "large-v3", timeout: float = 60.0):
        self.url = url
        self.model = model
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def start(self):
        self._client = httpx.AsyncClient(timeout=self.timeout)

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int = 16000) -> dict | None:
        wav_bytes = pcm16_bytes_to_wav_bytes(pcm_bytes, sample_rate=sample_rate)
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"model": self.model}

        for attempt in range(2):
            try:
                resp = await self._client.post(self.url, files=files, data=data)
                resp.raise_for_status()
                result = resp.json()
                logger.info(f"Transcription received: {result.get('text', '')[:80]}...")
                return result
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                if attempt == 0:
                    logger.warning(f"Whisper server error ({type(e).__name__}), retrying in 5s")
                    await asyncio.sleep(5)
                else:
                    logger.error(f"Whisper server error after retry: {e}")
                    return None
            except httpx.HTTPStatusError as e:
                logger.error(f"Whisper server returned error: {e.response.status_code} {e.response.text}")
                return None
            except Exception:
                logger.exception("Unexpected error during transcription")
                return None


class OpenAIClient(BaseTranscriptionClient):
    """Official OpenAI Whisper API client."""

    def __init__(self, api_key: str, model: str = "whisper-1", language: str = ""):
        self.api_key = api_key
        self.model = model
        self.language = language
        self._client: httpx.AsyncClient | None = None

    async def start(self):
        self._client = httpx.AsyncClient(
            timeout=60.0,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int = 16000) -> dict | None:
        wav_bytes = pcm16_bytes_to_wav_bytes(pcm_bytes, sample_rate=sample_rate)
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"model": self.model}
        if self.language:
            data["language"] = self.language

        try:
            resp = await self._client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                files=files,
                data=data,
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"OpenAI transcription: {result.get('text', '')[:80]}...")
            return {"text": result.get("text", ""), "language": self.language or "en", "duration": 0}
        except Exception:
            logger.exception("OpenAI transcription failed")
            return None


class AssemblyAIClient(BaseTranscriptionClient):
    """AssemblyAI transcription client with Universal-2 and language detection."""

    UPLOAD_URL = "https://api.assemblyai.com/v2/upload"
    TRANSCRIPT_URL = "https://api.assemblyai.com/v2/transcript"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client: httpx.AsyncClient | None = None

    async def start(self):
        self._client = httpx.AsyncClient(
            timeout=60.0,
            headers={"Authorization": self.api_key},
        )

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int = 16000) -> dict | None:
        wav_bytes = pcm16_bytes_to_wav_bytes(pcm_bytes, sample_rate=sample_rate)
        headers = {"Authorization": self.api_key, "Content-Type": "application/octet-stream"}

        try:
            # Step 1: Upload audio
            upload_resp = await self._client.post(self.UPLOAD_URL, content=wav_bytes, headers=headers)
            upload_resp.raise_for_status()
            audio_url = upload_resp.json()["upload_url"]
            logger.info("Audio uploaded to AssemblyAI")

            # Step 2: Request transcription
            transcript_resp = await self._client.post(
                self.TRANSCRIPT_URL,
                json={
                    "audio_url": audio_url,
                    "speech_model": "universal",
                    "language_detection": True,
                },
                headers={"Authorization": self.api_key},
            )
            transcript_resp.raise_for_status()
            transcript_id = transcript_resp.json()["id"]
            logger.info(f"Transcription requested: {transcript_id}")

            # Step 3: Poll for completion
            poll_url = f"{self.TRANSCRIPT_URL}/{transcript_id}"
            for _ in range(60):
                await asyncio.sleep(1)
                poll_resp = await self._client.get(poll_url, headers={"Authorization": self.api_key})
                poll_resp.raise_for_status()
                result = poll_resp.json()
                status = result.get("status")

                if status == "completed":
                    text = result.get("text", "").strip()
                    language = result.get("language_code", "en")
                    duration = result.get("audio_duration", 0)
                    logger.info(f"Transcription completed: {text[:80]}...")
                    return {"text": text, "language": language, "duration": duration}
                elif status == "error":
                    logger.error(f"AssemblyAI error: {result.get('error', 'Unknown')}")
                    return None

            logger.error("AssemblyAI transcription timed out after 60s")
            return None

        except httpx.HTTPStatusError as e:
            logger.error(f"AssemblyAI API error: {e.response.status_code} {e.response.text}")
            return None
        except Exception:
            logger.exception("Unexpected error during AssemblyAI transcription")
            return None


def get_transcription_client(
    provider: str = "assemblyai",
    api_key: str = "",
    url: str = "",
    model: str = "",
    language: str = "",
) -> BaseTranscriptionClient:
    """Factory function: create the right transcription client based on provider."""
    if provider == "passthrough":
        return PassthroughClient()
    elif provider == "whisper_compatible":
        return WhisperCompatibleClient(
            url=url or "http://localhost:8090/v1/audio/transcriptions",
            model=model or "large-v3",
        )
    elif provider == "openai":
        return OpenAIClient(api_key=api_key, model=model or "whisper-1", language=language)
    elif provider == "assemblyai":
        if not api_key:
            raise ValueError("AssemblyAI requires ALFRED_TRANSCRIPTION_API_KEY to be set")
        return AssemblyAIClient(api_key=api_key)
    else:
        raise ValueError(f"Unknown transcription provider: {provider}")
