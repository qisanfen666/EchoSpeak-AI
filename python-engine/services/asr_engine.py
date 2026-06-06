"""
Speech Recognition engine using faster-whisper.
Transcribes user voice input to text with pronunciation & fluency scoring.

Integrated from Xengineer ASR module into EchoSpeak-AI as a service.
"""

import time
import logging
import io
import wave

from config import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def calculate_pronunciation_score(segments: list) -> int:
    """
    Score pronunciation (0-100) based on Whisper's avg_logprob.

    avg_logprob range: typically -2.5 (poor/unclear) to 0 (crystal clear).
    Mapping: score = clamp( (avg_logprob + 2.5) / 2.5 * 100, 0, 100 )
    """
    if not segments:
        return 0
    probs = [s.get("avg_logprob", -1.0) for s in segments]
    avg = sum(probs) / len(probs)
    raw = (avg + 2.5) / 2.5 * 100
    return max(0, min(100, int(raw)))


def calculate_fluency_score(segments: list, duration_s: float) -> int:
    """
    Score fluency (0-100) based on speaking rate and flow.

    Factors:
      - Words per minute (wpm): ideal 120-160 for spoken English
      - Number of segments (more segments = less fluency = more pauses)
    """
    if not segments or duration_s <= 0:
        return 0

    words = sum(len(s.get("text", "").split()) for s in segments)
    wpm = words / (duration_s / 60.0) if duration_s > 0 else 0

    # WPM scoring
    if wpm < 40:
        wpm_score = max(0, wpm / 40 * 40)
    elif wpm < 120:
        wpm_score = 40 + (wpm - 40) / 80 * 30   # 40 → 70
    elif wpm < 160:
        wpm_score = 70 + (wpm - 120) / 40 * 20  # 70 → 90
    elif wpm < 200:
        wpm_score = 90 - (wpm - 160) / 40 * 15  # 90 → 75
    else:
        wpm_score = max(40, 75 - (wpm - 200) / 50 * 25)  # 75 → 50

    # Segment count penalty
    seg_count = len(segments)
    if seg_count <= 1:
        seg_score = 100
    elif seg_count <= 3:
        seg_score = 90
    elif seg_count <= 6:
        seg_score = 75 - (seg_count - 3) * 5
    else:
        seg_score = max(40, 60 - (seg_count - 6) * 3)

    score = int(wpm_score * 0.7 + seg_score * 0.3)
    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# Repetition dedup — Whisper occasionally hallucinates repeated sentences
# ---------------------------------------------------------------------------

def _dedupe_repetition(text: str) -> str:
    """
    Detect and remove repeated phrases from Whisper output.

    Common pattern: "You give me water, you give me water."
    Handles case-insensitive repetition at comma and word level.
    """
    if not text:
        return text

    # Normalise: lowercase, strip trailing punctuation for comparison
    def _norm(s: str) -> str:
        return s.strip().lower().rstrip(".,;:!?")

    # ── Fuzzy comparison ──
    def _fuzzy_eq(a: str, b: str) -> bool:
        """Return True if strings are identical or differ by a short prefix/suffix."""
        if a == b:
            return True
        # One is a substring of the other (e.g. "ou" vs "you" or "water" vs "water")
        if len(a) >= 3 and len(b) >= 3:
            if a in b or b in a:
                return True
            # Levenshtein distance <= 2 for short truncations
            if abs(len(a) - len(b)) <= 2:
                shorter = a if len(a) <= len(b) else b
                longer = b if len(a) <= len(b) else a
                if longer.startswith(shorter) or longer.endswith(shorter):
                    return True
        return False

    # ── Comma-split dedup ──
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 2:
        mid = len(parts) // 2
        if all(
            _fuzzy_eq(_norm(parts[i]), _norm(parts[mid + i]))
            for i in range(mid)
        ):
            return ", ".join(parts[:mid])

    # ── Word-level dedup — half ──
    words = text.split()
    n = len(words)
    if n >= 4 and n % 2 == 0:
        mid = n // 2
        if all(
            _fuzzy_eq(_norm(words[i]), _norm(words[mid + i]))
            for i in range(mid)
        ):
            return " ".join(words[:mid])

    # ── Word-level dedup — thirds ──
    if n >= 6 and n % 3 == 0:
        third = n // 3
        if (
            all(_fuzzy_eq(_norm(words[i]), _norm(words[third + i])) for i in range(third))
            and all(_fuzzy_eq(_norm(words[i]), _norm(words[2 * third + i])) for i in range(third))
        ):
            return " ".join(words[:third])

    return text


# ---------------------------------------------------------------------------
# ASR Engine
# ---------------------------------------------------------------------------

class ASREngine:
    """Speech-to-text engine powered by faster-whisper."""

    def __init__(self):
        self._model = None
        logger.info(
            f"ASR engine initialised (model={config.WHISPER_MODEL_SIZE}, "
            f"device={config.WHISPER_DEVICE}, compute={config.WHISPER_COMPUTE_TYPE})"
        )

    @property
    def model(self):
        """Lazy-load the Whisper model on first use."""
        if self._model is None:
            # Workaround: Windows SSL cert issues when downloading from HF
            import ssl
            ssl._create_default_https_context = ssl._create_unverified_context
            import os as _os
            _os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFY", "1")
            # Use mirror for users behind restricted networks (e.g. China)
            if not _os.environ.get("HF_ENDPOINT"):
                _os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

            # Patch httpcore (httpx's low-level HTTP lib) to disable SSL verification,
            # since httpx ignores the global ssl._create_default_https_context.
            try:
                import httpcore._backends.sync as _sync_backend
                _orig_sync_start_tls = _sync_backend.SyncStream.start_tls
                def _patched_start_tls(self, ssl_context, server_hostname, timeout=None):
                    if ssl_context is None:
                        ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                    return _orig_sync_start_tls(self, ssl_context, server_hostname, timeout)
                _sync_backend.SyncStream.start_tls = _patched_start_tls
                logger.info("Patched httpcore SSL for HuggingFace downloads")
            except Exception:
                pass

            model_path = str(config.WHISPER_MODEL_SIZE)
            # Resolve local path (if it looks like a filesystem path)
            from pathlib import Path
            p = Path(model_path)
            if p.exists():
                model_path = str(p.resolve())
                download_root = None
                logger.info(f"Loading Whisper model from local: {model_path}")
            else:
                download_root = str(config.ASR_MODEL_DIR)
                logger.info(f"Loading Whisper model '{model_path}' ...")

            t0 = time.time()
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                model_path,
                device=config.WHISPER_DEVICE,
                compute_type=config.WHISPER_COMPUTE_TYPE,
                download_root=download_root,
            )
            logger.info(f"Whisper model loaded in {time.time() - t0:.1f}s")
        return self._model

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    # ---- File-based transcription (with VAD) ----

    def transcribe_file(self, audio_path: str, language: str = "en") -> dict:
        """
        Transcribe an audio file with pronunciation & fluency scores.

        Returns:
            { "text", "segments", "duration_s", "language",
              "pronunciation": int 0-100, "fluency": int 0-100 }
        """
        from pathlib import Path
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        t0 = time.time()
        segments, info = self.model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500, threshold=0.35),
        )
        segments = list(segments)
        full_text = " ".join(seg.text.strip() for seg in segments).strip()
        duration_s = info.duration

        logger.info(
            f"Transcribed {audio_path.name} ({duration_s:.1f}s audio) "
            f"-> '{full_text[:60]}' in {time.time() - t0:.2f}s"
        )

        seg_data = [
            {
                "start": seg.start, "end": seg.end,
                "text": seg.text.strip(),
                "avg_logprob": round(seg.avg_logprob, 4),
                "no_speech_prob": round(seg.no_speech_prob, 4),
                "compression_ratio": round(seg.compression_ratio, 4),
            }
            for seg in segments
        ]

        return {
            "text": full_text,
            "segments": seg_data,
            "duration_s": round(duration_s, 2),
            "language": info.language,
            "pronunciation": calculate_pronunciation_score(seg_data),
            "fluency": calculate_fluency_score(seg_data, duration_s),
        }

    # ---- PCM streaming transcription (NO VAD — client already handles VAD) ----

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> dict:
        """
        Transcribe raw PCM 16-bit mono audio WITHOUT server-side VAD.
        The client already applies its own VAD; running VAD again on the
        server clips soft speech onsets (especially consonants like /f/, /h/, /th/).
        """
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm_bytes)
        return self._transcribe_wav_novad(buf.getvalue(), language="en")

    def transcribe_bytes(self, audio_bytes: bytes, language: str = "en") -> dict:
        """Transcribe any audio format bytes (MP3/WAV/etc) with scores."""
        buf = io.BytesIO(audio_bytes)
        buf.seek(0)
        segments, info = self.model.transcribe(
            buf,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500, threshold=0.35),
        )
        segments = list(segments)
        full_text = " ".join(seg.text.strip() for seg in segments).strip()
        full_text = _dedupe_repetition(full_text)
        seg_data = [
            {
                "start": seg.start, "end": seg.end,
                "text": seg.text.strip(),
                "avg_logprob": round(seg.avg_logprob, 4),
                "no_speech_prob": round(seg.no_speech_prob, 4),
            }
            for seg in segments
        ]
        return {
            "text": full_text,
            "segments": seg_data,
            "duration_s": round(info.duration, 2),
            "language": info.language,
            "pronunciation": calculate_pronunciation_score(seg_data),
            "fluency": calculate_fluency_score(seg_data, info.duration),
        }

    def _transcribe_wav(self, wav_bytes: bytes, language: str = "en") -> dict:
        """Transcribe WAV bytes with VAD filtering for clean speech extraction."""
        buf = io.BytesIO(wav_bytes)
        buf.seek(0)
        segments, info = self.model.transcribe(
            buf,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=400,
                threshold=0.35,
                speech_pad_ms=500,
            ),
        )
        segments = list(segments)
        full_text = " ".join(seg.text.strip() for seg in segments).strip()
        full_text = _dedupe_repetition(full_text)
        seg_data = [
            {
                "start": seg.start, "end": seg.end,
                "text": seg.text.strip(),
                "avg_logprob": round(seg.avg_logprob, 4),
                "no_speech_prob": round(seg.no_speech_prob, 4),
            }
            for seg in segments
        ]
        return {
            "text": full_text,
            "segments": seg_data,
            "duration_s": round(info.duration, 2),
            "language": info.language,
            "pronunciation": calculate_pronunciation_score(seg_data),
            "fluency": calculate_fluency_score(seg_data, info.duration),
        }

    def _transcribe_wav_novad(self, wav_bytes: bytes, language: str = "en") -> dict:
        """
        Transcribe WAV bytes WITHOUT VAD filtering.

        Use this when the client already performs its own VAD —
        running VAD twice clips soft speech onsets.
        """
        buf = io.BytesIO(wav_bytes)
        buf.seek(0)
        segments, info = self.model.transcribe(
            buf,
            language=language,
            beam_size=5,
            best_of=5,
            temperature=0.0,
            vad_filter=False,
            condition_on_previous_text=True,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            repetition_penalty=1.15,
            initial_prompt="English conversation practice, dialogue, spoken English.",
        )
        segments = list(segments)
        full_text = " ".join(seg.text.strip() for seg in segments).strip()
        full_text = _dedupe_repetition(full_text)
        seg_data = [
            {
                "start": seg.start, "end": seg.end,
                "text": seg.text.strip(),
                "avg_logprob": round(seg.avg_logprob, 4),
                "no_speech_prob": round(seg.no_speech_prob, 4),
            }
            for seg in segments
        ]
        return {
            "text": full_text,
            "segments": seg_data,
            "duration_s": round(info.duration, 2),
            "language": info.language,
            "pronunciation": calculate_pronunciation_score(seg_data),
            "fluency": calculate_fluency_score(seg_data, info.duration),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: ASREngine | None = None


def get_asr_engine() -> ASREngine:
    global _engine
    if _engine is None:
        _engine = ASREngine()
    return _engine
