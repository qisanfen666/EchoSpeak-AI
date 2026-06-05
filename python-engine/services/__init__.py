"""
EchoSpeak AI — Python AI Engine Services

Available services:
  - ASR Engine (faster-whisper, pronunciation & fluency scoring)
  - TTS Engine (edge-tts, 47+ English voices)
  - LLM Engine (OpenAI-compatible, scene-based conversation)
  - Correction Engine (grammar & expression analysis, structured JSON output)
  - ASR gRPC Service (FunASR streaming placeholder)
"""

from .asr_engine import ASREngine, get_asr_engine
from .tts_engine import TTSEngine, get_tts_engine
from .llm_engine import LLMEngine, Conversation, get_llm, create_conversation
from .correction_engine import (
    CorrectionEngine, CorrectionResult, ErrorDetail, get_correction_engine
)
