"""Audio utility functions for PCM16 processing."""

import io
import wave

import numpy as np


def pcm16_bytes_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """Convert raw PCM16 (16-bit signed LE) bytes to float32 array in [-1.0, 1.0]."""
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    return samples.astype(np.float32) / 32768.0


def pcm16_bytes_to_wav_bytes(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Wrap raw PCM16 bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def compute_duration_seconds(
    pcm_bytes_length: int,
    sample_rate: int = 16000,
    sample_width: int = 2,
    channels: int = 1,
) -> float:
    """Calculate audio duration in seconds from PCM byte length."""
    bytes_per_second = sample_rate * sample_width * channels
    if bytes_per_second == 0:
        return 0.0
    return pcm_bytes_length / bytes_per_second
