"""
Text-to-Speech engine using edge-tts (Microsoft neural voices).
Converts AI response text to natural speech audio.

Integrated from Xengineer TTS module into EchoSpeak-AI as a service.
"""

import asyncio
import time
import logging
from pathlib import Path

import edge_tts

from config import config

logger = logging.getLogger(__name__)


class TTSEngine:
    """TTS engine powered by Microsoft Edge-TTS (free, high-quality, 47+ English voices)."""

    def __init__(self, voice: str | None = None):
        self.voice = voice or config.TTS_VOICE
        logger.info(f"TTS engine initialised (voice={self.voice})")

    async def speak(
        self,
        text: str,
        output_path: str | Path | None = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> bytes:
        """
        Synthesise text to speech and save to file.

        Args:
            text:        The text to speak.
            output_path: Optional file path. Auto-generated if None.
            rate:        Speaking rate ("+0%" normal, "+20%" faster).
            pitch:       Voice pitch ("+0Hz" normal).

        Returns:
            Raw MP3 audio bytes.
        """
        if output_path is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
            output_path = config.AUDIO_DIR / f"tts_{ts}.mp3"

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        t0 = time.time()
        communicate = edge_tts.Communicate(text, self.voice, rate=rate, pitch=pitch)
        await communicate.save(str(output_path))

        elapsed = time.time() - t0
        logger.info(f"TTS: {len(text)} chars -> {output_path.name} in {elapsed:.2f}s")

        return output_path.read_bytes()

    async def stream_speak(self, text: str, rate: str = "+0%") -> bytes:
        """
        Synthesise and return MP3 bytes without saving to disk.
        Suitable for WebSocket / HTTP streaming responses.
        """
        t0 = time.time()
        communicate = edge_tts.Communicate(text, self.voice, rate=rate)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])

        audio_bytes = b"".join(chunks)
        elapsed = time.time() - t0
        logger.info(f"TTS streamed: {len(text)} chars -> {len(audio_bytes)} bytes in {elapsed:.2f}s")
        return audio_bytes

    async def list_voices(self) -> list[dict]:
        """List all available English TTS voices."""
        voices = await edge_tts.list_voices()
        english = [
            {
                "name": v["ShortName"],
                "gender": v["Gender"],
                "locale": v["Locale"],
                "friendly": v["FriendlyName"],
            }
            for v in voices
            if v["Locale"].startswith("en")
        ]
        return english


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_tts: TTSEngine | None = None


def get_tts_engine() -> TTSEngine:
    global _tts
    if _tts is None:
        _tts = TTSEngine()
    return _tts
