import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

class Config:
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    AUDIO_DIR: Path = BASE_DIR / "audio"
    ASR_MODEL_DIR: Path = AUDIO_DIR / "whisper_models"
    DEV_PORT: int = int(os.getenv("DEV_PORT", "8000"))
    GRPC_LISTEN_ADDR: str = os.getenv("GRPC_LISTEN_ADDR", "0.0.0.0:50051")
    REDIS_ADDR: str = os.getenv("REDIS_ADDR", "localhost:6379")
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "tiny")
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cpu")
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    ASR_MODEL: str = os.getenv("ASR_MODEL", "paraformer-zh-streaming")
    TTS_VOICE: str = os.getenv("TTS_VOICE", "en-US-JennyNeural")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    EVAL_ENABLED: bool = os.getenv("EVAL_ENABLED", "true").lower() == "true"
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"
    def __init__(self):
        self.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        self.ASR_MODEL_DIR.mkdir(parents=True, exist_ok=True)

config = Config()
