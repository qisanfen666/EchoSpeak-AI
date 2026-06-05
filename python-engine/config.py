"""
EchoSpeak AI — Python AI Engine 配置

支持：
  - gRPC Server (Go ↔ Python 通信)
  - FastAPI Dev Server (ASR / TTS 开发测试)
  - ASR (faster-whisper), TTS (edge-tts), LLM (OpenAI)
  - Redis 会话存储
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    """全局配置 —— 通过环境变量覆盖"""

    # ---- Paths ----
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    AUDIO_DIR: Path = BASE_DIR / "audio"
    ASR_MODEL_DIR: Path = AUDIO_DIR / "whisper_models"

    # ---- Dev Server (FastAPI) ----
    DEV_PORT: int = int(os.getenv("DEV_PORT", "8000"))

    # ---- gRPC Server ----
    GRPC_LISTEN_ADDR: str = os.getenv("GRPC_LISTEN_ADDR", "0.0.0.0:50051")

    # ---- Redis ----
    REDIS_ADDR: str = os.getenv("REDIS_ADDR", "localhost:6379")

    # ---- ASR — faster-whisper (integrated engine) ----
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "tiny")
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cpu")
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

    # ---- ASR — FunASR / Paraformer (original placeholder) ----
    ASR_MODEL: str = os.getenv("ASR_MODEL", "paraformer-zh-streaming")

    # ---- TTS — edge-tts (integrated engine) ----
    TTS_VOICE: str = os.getenv("TTS_VOICE", "en-US-JennyNeural")
    # 常用备选:
    # en-US-GuyNeural  (male, US)
    # en-GB-SoniaNeural (female, UK)
    # en-GB-RyanNeural  (male, UK)
    # en-AU-NatashaNeural (female, AU)

    # ---- LLM — OpenAI 兼容接口 ----
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")

    # ---- 发音评测 ----
    EVAL_ENABLED: bool = os.getenv("EVAL_ENABLED", "true").lower() == "true"

    # ---- Service ----
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    def __init__(self):
        # Ensure directories exist
        self.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        self.ASR_MODEL_DIR.mkdir(parents=True, exist_ok=True)


config = Config()
