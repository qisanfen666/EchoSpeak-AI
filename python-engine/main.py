"""
EchoSpeak AI — Python AI Engine 入口 (gRPC Server)

Go 网关通过 gRPC 调用这里的 ASR / Chat / TTS 服务。
"""

import logging
import asyncio
import time
from concurrent import futures

import grpc

# 动态导入 proto（运行时生成）
try:
    import proto.aiservice_pb2 as pb2
    import proto.aiservice_pb2_grpc as pb2_grpc
    PROTO_AVAILABLE = True
except ImportError:
    PROTO_AVAILABLE = False
    print("[WARN] Proto not generated yet, gRPC server will use placeholder")

from config import config
from services.asr_engine import get_asr_engine
from services.llm_engine import get_llm, create_conversation
from services.correction_engine import get_correction_engine
from services.tts_engine import get_tts_engine

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class AIServiceServicer(pb2_grpc.AIServiceServicer if PROTO_AVAILABLE else object):
    """AI 服务 gRPC 实现"""

    # ------------------------------------------------------------------
    # Health — 健康检查
    # ------------------------------------------------------------------
    def Health(self, request, context):
        logger.info(f"[gRPC] Health check from peer: {context.peer()}")
        if PROTO_AVAILABLE:
            return pb2.HealthResponse(ok=True, message="Python AI Engine is running")
        return None

    # ------------------------------------------------------------------
    # StreamASR — 流式语音识别
    # Go 推送音频流 → Python 返回识别结果
    # ------------------------------------------------------------------
    async def StreamASR(self, request_iterator, context):
        logger.info("[gRPC:StreamASR] Stream started")
        asr = get_asr_engine()
        buf = bytearray()
        session_id = "unknown"

        try:
            async for chunk in request_iterator:
                session_id = chunk.session_id
                buf.extend(chunk.audio_data)

                if chunk.is_end and len(buf) > 0:
                    # Finalize: transcribe accumulated audio
                    logger.info(
                        f"[gRPC:StreamASR] Finalizing: session={session_id} "
                        f"bytes={len(buf)} ({len(buf)/32000:.1f}s)"
                    )
                    t0 = time.time()
                    try:
                        result = await asyncio.to_thread(
                            asr.transcribe_pcm, bytes(buf), 16000
                        )
                        text = result.get("text", "").strip()
                        elapsed = time.time() - t0
                        logger.info(
                            f"[gRPC:StreamASR] Result: \"{text[:60]}\" "
                            f"({elapsed:.1f}s)"
                        )
                        yield pb2.ASRResult(
                            text=text,
                            is_final=True,
                            session_id=session_id,
                            pronunciation=result.get("pronunciation", 0),
                            fluency=result.get("fluency", 0),
                        )
                    except Exception as e:
                        logger.exception(f"[gRPC:StreamASR] Transcription error")
                        # Return error as empty text so the pipeline can fallback
                        yield pb2.ASRResult(
                            text="",
                            is_final=True,
                            session_id=session_id,
                        )

                    buf.clear()
        except Exception as e:
            logger.exception(f"[gRPC:StreamASR] Stream error: {e}")

    # ------------------------------------------------------------------
    # Chat — LLM 对话 + 纠错 + TTS
    # Go 发送 ChatRequest → Python 流式返回 reply / correction / tts_audio
    # ------------------------------------------------------------------
    async def Chat(self, request, context):
        session_id = request.session_id
        scene = request.scene or "ordering"
        user_message = request.user_message.strip()

        logger.info(
            f"[gRPC:Chat] session={session_id} scene={scene} "
            f"msg=\"{user_message[:50]}\""
        )

        llm = get_llm()
        tts = get_tts_engine()
        corr_engine = get_correction_engine()
        conversation = create_conversation(scene)

        # Inject difficulty instructions into system prompt
        difficulty = request.difficulty or "medium"
        _diff_instructions = {
            "easy": "Use simple vocabulary and short sentences. Speak slowly and clearly. Avoid idioms and complex grammar.",
            "medium": "Use natural, everyday English. Mix simple and moderate sentences.",
            "hard": "Use advanced vocabulary, complex sentences, idioms, and natural native-speaker speed. Challenge the user.",
        }
        _diff_hint = _diff_instructions.get(difficulty, _diff_instructions["medium"])
        conversation.messages[0]["content"] += f"\n\nDifficulty level: {difficulty.upper()}. {_diff_hint}"

        # TTS voice from accent (default US female)
        _tts_voice = request.accent or "en-US-JennyNeural"

        # TTS rate by difficulty
        _tts_rate = {"easy": "-25%", "medium": "+0%", "hard": "+15%"}.get(difficulty, "+0%")

        logger.info(
            f"[gRPC:Chat] session={session_id} scene={scene} "
            f"difficulty={difficulty} msg=\"{user_message[:50]}\""
        )

        # Restore history from request (if any)
        for msg in request.history:
            if msg.role == "user":
                conversation.add_user_message(msg.content)
            elif msg.role == "assistant":
                conversation.add_assistant_message(msg.content)

        # Add the current user message
        conversation.add_user_message(user_message)

        # ── Start correction as a background Task (runs in parallel with LLM) ──
        correction_result = None  # will hold CorrectionResult when ready

        async def _run_correction_bg():
            nonlocal correction_result
            try:
                correction_result = await asyncio.to_thread(
                    corr_engine.correct, user_message
                )
                if correction_result and correction_result.has_corrections:
                    logger.info(
                        f"[gRPC:Chat] Correction ready: "
                        f"{len(correction_result.errors)} error(s)"
                    )
            except Exception as e:
                logger.warning(f"[gRPC:Chat] Correction failed: {e}")

        correction_task = asyncio.create_task(_run_correction_bg())

        # ── Phase 1: Stream LLM reply tokens ──
        full_reply = ""
        try:
            queue: asyncio.Queue = asyncio.Queue()

            def _run_llm():
                try:
                    for token in llm.reply_stream(conversation):
                        queue.put_nowait(("token", token))
                    queue.put_nowait(("done", None))
                except Exception as exc:
                    queue.put_nowait(("error", exc))

            loop = asyncio.get_event_loop()
            llm_future = loop.run_in_executor(None, _run_llm)

            while True:
                try:
                    kind, value = await asyncio.wait_for(
                        queue.get(), timeout=60
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"[gRPC:Chat] LLM timeout")
                    break

                if kind == "error":
                    logger.error(f"[gRPC:Chat] LLM error: {value}")
                    break
                if kind == "done":
                    break

                full_reply += value
                yield pb2.ChatResponse(
                    reply=pb2.ReplyChunk(
                        text=value,
                        is_first=(len(full_reply) == len(value)),
                    )
                )

            # Wait for LLM thread to finish
            await llm_future

        except asyncio.CancelledError:
            logger.info(f"[gRPC:Chat] Cancelled during LLM stream")
            if full_reply.strip():
                try:
                    yield pb2.ChatResponse(done=True)
                except Exception:
                    pass
            return
        except Exception as e:
            logger.exception(f"[gRPC:Chat] LLM pipeline error: {e}")
            full_reply = full_reply or "I'm sorry, I didn't catch that. Could you say it again?"

        if not full_reply.strip():
            full_reply = llm._fallback_reply(scene)

        # ── Phase 2: TTS audio (while correction may still be running) ──
        try:
            t0 = time.time()
            audio_bytes = await tts.stream_speak(full_reply.strip(), rate=_tts_rate, voice=_tts_voice)
            elapsed = time.time() - t0
            logger.info(
                f"[gRPC:Chat] TTS: {len(full_reply)} chars → "
                f"{len(audio_bytes)} bytes ({elapsed:.1f}s)"
            )
            yield pb2.ChatResponse(tts_audio=audio_bytes)
        except asyncio.CancelledError:
            logger.info(f"[gRPC:Chat] Cancelled during TTS")
            return
        except Exception as e:
            logger.warning(f"[gRPC:Chat] TTS error: {e}")

        # ── Phase 3: Wait for correction (with timeout) and yield ──
        try:
            await asyncio.wait_for(correction_task, timeout=5)
        except asyncio.TimeoutError:
            logger.info(f"[gRPC:Chat] Correction still running after 5s, skipping")
        except Exception:
            pass

        if correction_result and correction_result.has_corrections:
            try:
                highlights = []
                for err_item in correction_result.errors:
                    orig = err_item.original
                    start = user_message.find(orig)
                    if start >= 0:
                        highlights.append(pb2.WordFix(
                            start_idx=start,
                            end_idx=start + len(orig),
                            suggestion=err_item.corrected,
                            type=err_item.type,
                            explanation_cn=err_item.explanation_cn,
                        ))
                # ── Expression tip: suggest a more idiomatic way ──
                expression_tip = ""
                try:
                    _tip_conv = create_conversation("default")
                    _tip_conv.messages[0] = {
                        "role": "system",
                        "content": (
                            "You are an English coach. Given the user's sentence, suggest ONE more natural, "
                            "idiomatic way to express the same meaning. Use conversational native-speaker English. "
                            "Keep it under 30 words. Return ONLY the suggestion, no explanation."
                        )
                    }
                    _tip_conv.add_user_message(user_message)
                    expression_tip = await asyncio.wait_for(
                        asyncio.to_thread(llm.reply, _tip_conv), timeout=8
                    )
                    expression_tip = (expression_tip or "").strip()
                except Exception:
                    expression_tip = ""

                yield pb2.ChatResponse(
                    correction=pb2.Correction(
                        original=correction_result.original_text,
                        corrected=correction_result.corrected_text,
                        error_type=correction_result.errors[0].type if correction_result.errors else "grammar",
                        highlights=highlights,
                        expression_tip=expression_tip,
                    )
                )
                logger.info(
                    f"[gRPC:Chat] Correction sent: "
                    f"{len(correction_result.errors)} error(s), tip={bool(expression_tip)}"
                )
            except Exception as e:
                logger.warning(f"[gRPC:Chat] Failed to yield correction: {e}")

        # ── Phase 4: Translation (Chinese) ──
        if full_reply.strip():
            try:
                trans_conv = create_conversation("default")
                trans_conv.messages[0] = {
                    "role": "system",
                    "content": "Translate the following English to natural Chinese (中文). Return ONLY the translation, no explanation."
                }
                trans_conv.add_user_message(full_reply.strip())
                translation = await asyncio.to_thread(llm.reply, trans_conv)
                if translation and translation.strip():
                    yield pb2.ChatResponse(translation=translation.strip())
                    logger.info(f"[gRPC:Chat] Translation: {translation.strip()[:40]}...")
            except Exception:
                pass  # Translation is optional

        # ── Done ──
        try:
            yield pb2.ChatResponse(done=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Synthesize — 独立 TTS（暂未使用，预留）
    # ------------------------------------------------------------------
    async def Synthesize(self, request, context):
        tts = get_tts_engine()
        try:
            audio = await tts.stream_speak(request.text)
            yield pb2.AudioChunk(
                audio_data=audio,
                session_id=request.session_id,
                is_end=True,
            )
        except Exception as e:
            logger.exception(f"[gRPC:Synthesize] Error: {e}")

    # ------------------------------------------------------------------
    # Evaluate — 发音评测（异步，占位）
    # ------------------------------------------------------------------
    async def Evaluate(self, request, context):
        # TODO: implement pronunciation evaluation
        return pb2.EvaluateResponse(
            overall_score=0,
            accuracy=0,
            fluency=0,
            completeness=0,
        )

    # ------------------------------------------------------------------
    # GenerateReport — 课后报告（占位）
    # ------------------------------------------------------------------
    async def GenerateReport(self, request, context):
        # TODO: implement report generation
        return pb2.ReportResponse(
            overall_score=0,
            summary="Report not yet implemented.",
        )


async def serve():
    """启动 gRPC Server"""
    # ── 启动时预加载 ASR 模型（确保下载/加载完成后再接受请求）──
    logger.info("[gRPC] Preloading ASR model (first startup may download)...")
    import time as _time
    _t0 = _time.time()
    asr = get_asr_engine()
    # 触发 lazy-load，模型不在本地时会自动下载
    await asyncio.to_thread(lambda: asr.model)
    logger.info(f"[gRPC] ASR model ready ({_time.time() - _t0:.1f}s)")

    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))

    if PROTO_AVAILABLE:
        pb2_grpc.add_AIServiceServicer_to_server(AIServiceServicer(), server)

    server.add_insecure_port(config.GRPC_LISTEN_ADDR)

    await server.start()
    logger.info(f"[gRPC] Listening on {config.GRPC_LISTEN_ADDR}")
    logger.info("[gRPC] Ready — waiting for Go gateway connections...")
    logger.info("[gRPC] Endpoints: Health, StreamASR, Chat, Synthesize")

    await server.wait_for_termination()


if __name__ == "__main__":
    if not PROTO_AVAILABLE:
        print("=" * 50)
        print("  Proto 文件未生成！请先运行:")
        print("  cd python-engine")
        print("  python gen_proto.py")
        print("=" * 50)
    asyncio.run(serve())
