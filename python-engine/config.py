"""
EchoSpeak AI — Python AI Engine 配置
3天限时赛：保持简洁，用环境变量 + .env
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # gRPC Server
    GRPC_LISTEN_ADDR: str = os.getenv("GRPC_LISTEN_ADDR", "0.0.0.0:50051")

    # Redis
    REDIS_ADDR: str = os.getenv("REDIS_ADDR", "localhost:6379")

    # ASR — 使用 FunASR (Paraformer) 流式模型
    ASR_MODEL: str = os.getenv("ASR_MODEL", "paraformer-zh-streaming")
    # 或 "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"

    # LLM — OpenAI 兼容接口
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    # 国内可用 DeepSeek: base_url=https://api.deepseek.com/v1 model=deepseek-chat

    # TTS — 语音合成
    TTS_ENGINE: str = os.getenv("TTS_ENGINE", "edge-tts")  # edge-tts / cosyvoice

    # 发音评测
    EVAL_ENABLED: bool = os.getenv("EVAL_ENABLED", "true").lower() == "true"

    # 服务
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"


config = Config()
