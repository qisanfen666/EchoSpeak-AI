"""
EchoSpeak AI — Python AI Engine Services

Available services:
  - ASR Engine (faster-whisper, pronunciation & fluency scoring)
  - TTS Engine (edge-tts, 47+ English voices)
  - ASR gRPC Service (FunASR streaming placeholder)
"""

from .asr_engine import ASREngine, get_asr_engine
from .tts_engine import TTSEngine, get_tts_engine
