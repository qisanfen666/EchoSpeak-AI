"""
流式 ASR 服务 — 使用 FunASR (Paraformer) 实现低延迟语音识别

关键设计：
- 支持 streaming 模式，逐 chunk 送入模型
- 输出 partial result（中间结果）和 final result（最终结果）
- partial 用于实时字幕显示，final 用于触发 LLM 对话
"""

import asyncio
import logging
from typing import AsyncGenerator, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ASRResult:
    """ASR 识别结果"""
    text: str
    is_final: bool
    session_id: str


class ASRService:
    """
    ASR 服务封装
    Day 1 重点实现：先跑通 basic 版本，再优化 streaming
    """

    def __init__(self, model_name: str = "paraformer-zh-streaming"):
        self.model_name = model_name
        self.model = None
        logger.info(f"[ASR] Initializing with model: {model_name}")

    async def initialize(self):
        """
        加载模型（耗时操作，启动时执行一次）
        Day 1: FunASR 的 AutoModel 支持 online 模式
        """
        try:
            from funasr import AutoModel
            self.model = AutoModel(
                model=self.model_name,
                # 流式模式参数
                chunk_size=[0, 10, 5],  # [0, 10, 5] 600ms 延迟
                encoder_chunk_look_back=4,
                decoder_chunk_look_back=1,
                device="cuda:0",  # 有 GPU 用 cuda:0，否则用 cpu
                disable_pbar=True,
            )
            logger.info(f"[ASR] Model loaded: {self.model_name}")
        except ImportError:
            logger.error("[ASR] funasr not installed! Using placeholder ASR.")
            self.model = None
        except Exception as e:
            logger.error(f"[ASR] Model load failed: {e}, using placeholder")
            self.model = None

    async def transcribe_stream(
        self,
        audio_chunks: AsyncGenerator[bytes, None],
        session_id: str,
    ) -> AsyncGenerator[ASRResult, None]:
        """
        流式转写：逐 chunk 送入模型，yield 识别结果

        Args:
            audio_chunks: 音频字节流（PCM 16kHz 16bit mono）
            session_id: 会话标识

        Yields:
            ASRResult: text + is_final
        """
        if self.model is None:
            # 降级方案：Mock 返回
            logger.warning("[ASR] Running in mock mode")
            async for chunk in audio_chunks:
                pass  # 消费所有音频
            yield ASRResult(
                text="[Mock ASR] I'd like to order a coffee please.",
                is_final=True,
                session_id=session_id,
            )
            return

        # 实际 FunASR streaming 调用
        # FunASR 的 generate 支持输入音频流
        cache = {}
        async for chunk in audio_chunks:
            try:
                results = self.model.generate(
                    input=chunk,
                    cache=cache,
                    is_final=False,
                    chunk_size=[0, 10, 5],
                )
                if results and len(results) > 0:
                    text = results[0].get("text", "")
                    if text:
                        yield ASRResult(
                            text=text,
                            is_final=False,  # partial
                            session_id=session_id,
                        )
            except Exception as e:
                logger.error(f"[ASR] Streaming error: {e}")
                continue

        # 结束时发送 final 结果
        try:
            final_results = self.model.generate(
                input=None,  # 表示流结束
                cache=cache,
                is_final=True,
            )
            if final_results and len(final_results) > 0:
                text = final_results[0].get("text", "")
                if text:
                    yield ASRResult(
                        text=text,
                        is_final=True,
                        session_id=session_id,
                    )
        except Exception as e:
            logger.error(f"[ASR] Finalize error: {e}")

    async def transcribe_file(self, audio_path: str, session_id: str) -> str:
        """非流式转写（用于发音评测等异步场景）"""
        if self.model is None:
            return "[Mock ASR] I'd like to order a coffee please."

        # FunASR 文件模式
        try:
            results = self.model.generate(input=audio_path)
            if results and len(results) > 0:
                return results[0].get("text", "")
        except Exception as e:
            logger.error(f"[ASR] File transcribe error: {e}")
            return ""

        return ""


# 全局单例
asr_service = ASRService()
