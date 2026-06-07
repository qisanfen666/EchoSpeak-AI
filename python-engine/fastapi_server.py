"""
FastAPI development server for ASR (speech-to-text + scoring) & TTS (text-to-speech).

Provides REST and WebSocket endpoints for testing ASR/TTS functionality
independently of the Go gateway. In production, the Go gateway proxies
requests to the gRPC server (main.py) instead.

Usage:
    cd python-engine
    pip install -r requirements.txt
    python fastapi_server.py
    # Open http://localhost:8000
"""

import asyncio
import json
import time
import logging
import base64
import struct
import os
import ssl
from pathlib import Path
from contextlib import asynccontextmanager

# ── SSL workaround for HuggingFace model downloads on Windows ──
ssl._create_default_https_context = ssl._create_unverified_context
os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFY", "1")
os.environ.setdefault("CURL_CA_BUNDLE", "")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

# ── Pre-import torch to prevent circular import when ctranslate2 loads it later ──
try:
    import torch
    if torch.cuda.is_available():
        _ = torch.cuda.device_count()
except Exception:
    pass

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import edge_tts

from config import config
from services.asr_engine import get_asr_engine
from services.tts_engine import get_tts_engine
from services.llm_engine import get_llm, create_conversation
from services.correction_engine import get_correction_engine

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fastapi")


# ---------------------------------------------------------------------------
# Lifespan — preload ASR model on startup
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== EchoSpeak AI — FastAPI Dev Server Starting ===")
    asyncio.create_task(_preload_asr())
    yield
    logger.info("=== Server Shutdown ===")


async def _preload_asr():
    """Pre-load Whisper model + warm up CUDA kernels + preload LLM."""
    t_start = time.time()

    # ── 1. Load ASR model ──
    logger.info("Pre-loading ASR model ...")
    try:
        engine = get_asr_engine()
        model = engine.model
        load_s = time.time() - t_start
        logger.info(f"ASR model loaded in {load_s:.1f}s")
    except Exception as e:
        logger.warning(f"ASR preload failed (will lazy-load later): {e}")
        # Still try to preload LLM below, don't return yet
        model = None

    # ── 2. Warm up CUDA kernels (silent dummy inference) ──
    if model is not None:
        try:
            import io, wave, struct
            # 1 second of silent 16kHz 16-bit mono PCM
            dummy = struct.pack("<" + "h" * 16000, *([0] * 16000))
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(dummy)
            wav = buf.getvalue()
            t0 = time.time()
            engine._transcribe_wav_novad(wav, language="en")
            logger.info(f"ASR warm-up done in {time.time() - t0:.1f}s")
        except Exception as e:
            logger.warning(f"ASR warm-up failed (non-fatal): {e}")

    logger.info(f"ASR ready (total {time.time() - t_start:.1f}s)")

    # ── 3. Preload LLM engine ──
    try:
        llm = get_llm()
        if config.LLM_API_KEY:
            _ = llm.client
            logger.info("LLM engine preloaded")
    except Exception as e:
        logger.warning(f"LLM preload failed: {e}")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="EchoSpeak AI — ASR/TTS Dev Server",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (frontend test page)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")


# ===================================================================
# REST — ASR: Speech-to-Text with Scoring
# ===================================================================

@app.post("/api/asr/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Query("en", description="Language code"),
):
    """
    Upload an audio file (WAV, MP3, M4A) and get transcription with
    pronunciation (0-100) and fluency (0-100) scores.
    """
    t0 = time.time()
    audio_bytes = await file.read()
    logger.info(f"ASR request: {file.filename} ({len(audio_bytes)} bytes)")

    # Save temp file (faster-whisper reads from disk)
    tmp_path = config.AUDIO_DIR / f"upload_{int(t0 * 1000)}_{file.filename}"
    tmp_path.write_bytes(audio_bytes)

    try:
        engine = get_asr_engine()
        result = engine.transcribe_file(str(tmp_path), language=language)
        elapsed = time.time() - t0
        return {
            "success": True,
            "text": result["text"],
            "language": result["language"],
            "duration_s": round(result["duration_s"], 2),
            "processing_s": round(elapsed, 2),
            "segments": result["segments"],
            "pronunciation": result.get("pronunciation", 0),
            "fluency": result.get("fluency", 0),
        }
    except Exception as e:
        logger.exception("ASR transcription failed")
        return {"success": False, "error": str(e)}
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


# ===================================================================
# REST — TTS: Text-to-Speech
# ===================================================================

@app.get("/api/tts/speak")
async def speak_text(
    text: str = Query(..., description="Text to synthesise"),
    voice: str = Query("en-US-JennyNeural", description="TTS voice name"),
    rate: str = Query("+0%", description="Speaking rate"),
):
    """Synthesise text to MP3 speech audio."""
    t0 = time.time()
    try:
        tts = get_tts_engine()
        tts.voice = voice
        audio_bytes = await tts.stream_speak(text)
        elapsed = time.time() - t0
        logger.info(f"TTS: {len(text)} chars -> {len(audio_bytes)} bytes in {elapsed:.2f}s")
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "X-Processing-Time": str(round(elapsed, 2)),
                "X-Text-Length": str(len(text)),
            },
        )
    except Exception as e:
        logger.exception("TTS failed")
        return Response(
            content=json.dumps({"success": False, "error": str(e)}),
            media_type="application/json",
            status_code=500,
        )


@app.get("/api/tts/voices")
async def list_voices():
    """List all available English TTS voices."""
    tts = get_tts_engine()
    voices = await tts.list_voices()
    return {"voices": voices}


# ===================================================================
# WebSocket — Streaming ASR (real-time voice-to-text)
# ===================================================================

@app.websocket("/ws/stream/{client_id}")
async def websocket_stream(websocket: WebSocket, client_id: str):
    """
    Real-time streaming ASR over WebSocket.

    Client sends:    {"type":"audio", "data":"<base64 PCM16 16kHz>"}
    Server replies:
        {"type":"partial","text":"...","stable":true,"pronunciation":N,"fluency":N}
        {"type":"final","text":"...","pronunciation":72,"fluency":68,"processing_s":0.5}
        {"type":"reset"}
        {"type":"end"}
    """
    await websocket.accept()
    logger.info(f"[{client_id}] Stream connected")

    recognizer = get_asr_engine()
    _ = recognizer.model  # ensure loaded

    buf = bytearray()
    last_time = time.time()
    last_text = ""
    has_sent_partial = False

    # Constants
    SR = 16000
    TRANS_INTERVAL = 1.2
    MIN_BYTES = 48000          # ~1.5s at 16kHz
    MAX_SECONDS = 8.0
    VAD_SILENCE = 1.2
    VAD_THRESHOLD = 0.015

    is_speaking = False
    silence_start = None
    speech_start = None

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg.get("type") == "audio":
                pcm = base64.b64decode(msg["data"])
                buf.extend(pcm)
                now = time.time()

                # VAD on this chunk
                eng = 0.0
                cnt = 0
                for i in range(0, len(pcm), 2):
                    if i + 1 < len(pcm):
                        v = abs(struct.unpack_from("<h", pcm, i)[0]) / 32768.0
                        eng += v
                        cnt += 1
                energy = eng / cnt if cnt else 0

                if energy > VAD_THRESHOLD:
                    if not is_speaking:
                        is_speaking = True
                        speech_start = now
                    silence_start = None
                else:
                    if is_speaking and silence_start is None:
                        silence_start = now

                # --- Partial transcription ---
                min_ok = MIN_BYTES * 2 if not has_sent_partial else MIN_BYTES
                if is_speaking and len(buf) >= min_ok and (now - last_time) >= TRANS_INTERVAL:
                    last_time = now
                    has_sent_partial = True
                    try:
                        result = recognizer.transcribe_pcm(bytes(buf), SR)
                        text = result.get("text", "").strip()
                        logger.info(
                            f"[{client_id}] partial: {len(buf)}B "
                            f"({len(buf)/SR/2:.1f}s) text='{text[:60]}'"
                        )
                        if text and text != last_text:
                            last_text = text
                            # Trim buffer to last 3s
                            trim = int(3.0 * SR * 2)
                            if len(buf) > trim:
                                buf = buf[-trim:]
                            await websocket.send_text(json.dumps({
                                "type": "partial", "text": text, "stable": True,
                                "pronunciation": result.get("pronunciation", 0),
                                "fluency": result.get("fluency", 0),
                            }))
                    except Exception as e:
                        logger.warning(f"[{client_id}] partial err: {e}")

                # --- Finalize on silence ---
                if is_speaking and silence_start is not None:
                    if (now - silence_start) >= VAD_SILENCE:
                        dur = (now - speech_start) if speech_start else 0
                        if dur > 0.5 and len(buf) >= MIN_BYTES:
                            try:
                                t0 = time.time()
                                result = recognizer.transcribe_pcm(bytes(buf), SR)
                                el = time.time() - t0
                                txt = result.get("text", "").strip()
                                logger.info(
                                    f"[{client_id}] final: {len(buf)}B "
                                    f"({len(buf)/SR/2:.1f}s) text='{txt[:60]}'"
                                )
                                if txt:
                                    await websocket.send_text(json.dumps({
                                        "type": "final", "text": txt,
                                        "pronunciation": result.get("pronunciation", 0),
                                        "fluency": result.get("fluency", 0),
                                        "processing_s": round(el, 2),
                                        "duration_s": round(dur, 1),
                                    }))
                                    logger.info(
                                        f"[{client_id}] final '{txt[:40]}' "
                                        f"P{result.get('pronunciation',0)} "
                                        f"F{result.get('fluency',0)} ({el:.1f}s)"
                                    )
                            except Exception as e:
                                logger.warning(f"[{client_id}] final err: {e}")

                        buf.clear()
                        last_text = ""
                        is_speaking = False
                        silence_start = None
                        speech_start = None
                        await websocket.send_text(json.dumps({"type": "reset"}))

                # Trim to max
                maxb = int(MAX_SECONDS * SR * 2)
                if len(buf) > maxb:
                    buf = buf[-maxb:]

            elif msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif msg.get("type") == "stop":
                if len(buf) >= MIN_BYTES:
                    try:
                        result = recognizer.transcribe_pcm(bytes(buf), SR)
                        txt = result.get("text", "").strip()
                        if txt:
                            await websocket.send_text(json.dumps({
                                "type": "final", "text": txt,
                                "pronunciation": result.get("pronunciation", 0),
                                "fluency": result.get("fluency", 0),
                                "processing_s": 0,
                            }))
                    except Exception:
                        pass
                await websocket.send_text(json.dumps({"type": "end"}))
                break

    except WebSocketDisconnect:
        logger.info(f"[{client_id}] Stream disconnected")
    except Exception as e:
        logger.exception(f"[{client_id}] Stream error")
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass


# ===================================================================
# WebSocket — Streaming TTS (text-to-speech playback)
# ===================================================================

@app.websocket("/ws/tts/{client_id}")
async def websocket_tts(websocket: WebSocket, client_id: str):
    """
    WebSocket for text-to-speech.

    Client sends: {"text": "...", "voice": "en-US-JennyNeural"}
    Server replies: binary MP3 chunks, then {"type":"end"}
    """
    await websocket.accept()
    logger.info(f"[{client_id}] TTS WebSocket connected")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            text = data.get("text", "")
            if not text:
                continue

            logger.info(f"[{client_id}] TTS request: '{text[:50]}...'")

            communicate = edge_tts.Communicate(text, data.get("voice", config.TTS_VOICE))
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    await websocket.send_bytes(chunk["data"])

            await websocket.send_text(json.dumps({"type": "end"}))

    except WebSocketDisconnect:
        logger.info(f"[{client_id}] TTS WebSocket disconnected")
    except Exception as e:
        logger.exception(f"[{client_id}] TTS WebSocket error")


# ===================================================================
# EchoSpeak AI — Full-duplex WebSocket (ASR + LLM + TTS + scoring)
# ===================================================================

VALID_SCENES = {"ordering", "interview", "meeting", "travel", "daily", "business", "custom", "default"}

# Fallback suggestion templates (used when LLM is unavailable)
_SUGGESTION_FALLBACKS = {
    "article": "注意冠词（a/an/the）的使用，尤其是定冠词和不定冠词的区分",
    "tense": "加强时态表达练习，注意过去时和完成时的正确使用",
    "preposition": "多练习介词的搭配，注意 in/on/at 等介词的准确用法",
    "grammar": "巩固基础语法知识，注意句子结构的完整性",
    "vocabulary": "扩充词汇量，尝试使用更丰富的表达方式",
    "word_choice": "注意用词准确性，选择更地道的英语表达",
    "expression": "提高英语表达连贯性，多练习地道口语表达",
}


async def _generate_llm_suggestions(
    llm,
    scene_display: str,
    utterance_count: int,
    duration_sec: int,
    grammar_score: int,
    vocab_score: int,
    avg_pron: int,
    avg_flu: int,
    error_counts: dict,
    all_errors: list,
    label_map: dict,
) -> list:
    """Call LLM to generate personalised learning suggestions."""
    # Build error summary text
    error_lines = []
    for etype, count in sorted(error_counts.items(), key=lambda x: -x[1]):
        if count > 0:
            label = label_map.get(etype, etype)
            error_lines.append(f"  - {label} ({etype}): {count}次")
    error_summary = "\n".join(error_lines) if error_lines else "  无错误"

    # Pick up to 5 example errors
    examples = all_errors[:5]
    example_lines = []
    for i, err in enumerate(examples, 1):
        example_lines.append(
            f"  {i}. {err['original']} → {err['corrected']}"
            f" ({label_map.get(err['type'], err['type'])})"
        )
    error_examples = "\n".join(example_lines) if example_lines else "  无具体错误"

    prompt = f"""You are an expert English teacher reviewing a student's practice session.
Generate 3-5 specific, actionable learning suggestions in Chinese (中文).

Session data:
- Scenario: {scene_display}
- Turns: {utterance_count}
- Duration: {duration_sec}s
- Scores: Grammar {grammar_score}/100, Vocabulary {vocab_score}/100, Pronunciation {avg_pron}/100, Fluency {avg_flu}/100

Error statistics:
{error_summary}

Example mistakes the student made:
{error_examples}

Requirements for each suggestion:
1. Reference the specific mistakes above — be concrete, not generic
2. Give actionable practice methods (e.g. "复习过去式变化规则，特别是..." not just "加强时态练习")
3. Be encouraging and constructive in tone
4. Keep each to 1-2 sentences

Return ONLY the suggestions, one per line. No numbering, no bullet points, no other text."""

    try:
        from services.llm_engine import create_conversation

        conv = create_conversation("default")
        # Replace system prompt with our specialised one
        conv.messages[0] = {
            "role": "system",
            "content": "You are an expert English teacher. You give specific, actionable learning advice in Chinese. Always reference concrete mistakes. Never give vague generic suggestions.",
        }
        conv.add_user_message(prompt)

        import asyncio
        reply = await asyncio.to_thread(llm.reply, conv)
        logger.info(f"LLM suggestions generated: '{reply[:120]}...'")

        # Parse: split by newlines, filter empty, clean up numbering/bullets
        import re
        suggestions = []
        for line in reply.strip().split("\n"):
            line = line.strip()
            # Remove leading numbers, bullets, dashes
            line = re.sub(r'^[\d]+[\.\)、]\s*', '', line)
            line = re.sub(r'^[-\•\*\–]\s*', '', line)
            line = line.strip()
            if line and len(line) > 6:  # filter short/noise lines
                suggestions.append(line)
        if suggestions:
            return suggestions[:5]
    except Exception as e:
        logger.warning(f"LLM suggestions failed, using fallback: {e}")

    # Fallback: template-based suggestions
    suggestions = []
    for etype, _ in sorted(error_counts.items(), key=lambda x: -x[1])[:3]:
        if etype in _SUGGESTION_FALLBACKS:
            suggestions.append(_SUGGESTION_FALLBACKS[etype])
    if not suggestions:
        suggestions.append("继续保持练习，尝试更多不同场景的对话")
        suggestions.append("可以挑战更复杂的表达，提高语言丰富度")
    return suggestions


@app.websocket("/ws")
async def echo_speak_ws(websocket: WebSocket):
    """
    EchoSpeak AI full-duplex WebSocket endpoint.
    Full pipeline: ASR -> LLM -> TTS

    Protocol (JSON messages):
      Client -> Server:
        {"type":"audio_chunk", "data":{"data":"<base64_audio>","is_end":bool,"chunk_id":N}}
        {"type":"text_message", "data":{"text":"..."}}
        {"type":"interrupt", "seq":N}
        {"type":"custom_scene", "data":{"description":"..."}}  // create custom scene
        {"type":"scene_select", "data":{"scene":"ordering|interview|meeting|travel|daily|business|custom"}}
        {"type":"end_session", "data":{}}

      Server -> Client:
        {"type":"transcript", "data":{"text":"...","is_final":true,"is_user":true,"pronunciation":N,"fluency":N}}
        {"type":"reply_start"}
        {"type":"reply_chunk", "data":{"text":"..."}}
        {"type":"reply_end", "data":{"interrupted":bool}}
        {"type":"correction", "data":{"original_text":"...","corrected_text":"...","errors":[...],"has_corrections":bool}}
        {"type":"context_usage", "data":{"used":N,"max":20}}  // conversation context usage
        {"type":"score_update", "data":{"score":N}}
        {"type":"session_report", "data":{"overall_score":N,...}}
        {"type":"custom_scene_ready", "data":{"scene":"custom","description":"..."}}  // custom scene acknowledged
        {"type":"error", "data":{"message":"..."}}
        <binary MP3 chunks>  <- TTS audio
    """
    session_id = websocket.query_params.get("session_id", "unknown")
    scene = websocket.query_params.get("scene", "ordering")
    if scene not in VALID_SCENES:
        scene = "ordering"

    await websocket.accept()
    logger.info(f"[WS:{session_id}] EchoSpeak connected, scene={scene}")

    # Session state
    llm = get_llm()
    conversation = create_conversation(scene)
    tts = get_tts_engine()
    buf = bytearray()
    utterance_count = 0
    session_start = time.time()
    interrupted = False
    current_turn_task: asyncio.Task | None = None

    # Session tracking for report
    pron_scores = []       # pronunciation scores per utterance
    flu_scores = []        # fluency scores per utterance
    all_corrections = []   # correction results

    async def cancel_current_turn():
        """Cancel the running LLM/TTS task if any."""
        nonlocal current_turn_task, interrupted
        interrupted = True
        if current_turn_task and not current_turn_task.done():
            current_turn_task.cancel()
            try:
                await current_turn_task
            except asyncio.CancelledError:
                pass
        current_turn_task = None

    async def start_turn(user_text: str):
        """Launch process_and_reply as a cancellable background task."""
        nonlocal current_turn_task
        await cancel_current_turn()
        current_turn_task = asyncio.create_task(process_and_reply(user_text))
        # Don't await — let the main loop keep receiving messages

    async def send_json(msg: dict):
        try:
            await websocket.send_text(json.dumps(msg))
        except RuntimeError:
            pass  # client disconnected, ignore

    async def process_and_reply(user_text: str):
        """Streaming LLM → TTS pipeline.

        LLM tokens are streamed word-by-word to the frontend for real-time
        display.  Once the full reply is ready, TTS is streamed as MP3 chunks
        so the browser can start playback before the entire audio is generated.
        """
        nonlocal interrupted
        interrupted = False

        if not user_text.strip():
            return

        conversation.add_user_message(user_text.strip())
        logger.info(f"[WS:{session_id}] LLM streaming start")

        # Launch correction in parallel (non-blocking, results sent when ready)
        async def run_correction():
            try:
                corr_engine = get_correction_engine()
                correction = await asyncio.to_thread(corr_engine.correct, user_text.strip())
                if correction.has_corrections:
                    all_corrections.append(correction)
                    await send_json({"type": "correction", "data": correction.to_dict()})
                    logger.info(f"[WS:{session_id}] Correction: {len(correction.errors)} error(s)")
            except Exception as e:
                logger.warning(f"[WS:{session_id}] Correction failed: {e}")
        correction_task = asyncio.create_task(run_correction())

        await send_json({"type": "reply_start"})

        # ── Phase 1: stream LLM tokens word-by-word ──
        full_reply: str = ""
        try:
            # reply_stream() is sync → run in thread, bridge via Queue
            queue: asyncio.Queue = asyncio.Queue()

            def _run_llm():
                try:
                    for token in llm.reply_stream(conversation):
                        queue.put_nowait(("token", token))
                    queue.put_nowait(("done", None))
                except Exception as exc:
                    queue.put_nowait(("error", exc))

            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, _run_llm)

            while True:
                try:
                    kind, value = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    logger.warning(f"[WS:{session_id}] LLM stream timeout")
                    break

                if kind == "error":
                    logger.error(f"[WS:{session_id}] LLM stream error: {value}")
                    break
                if kind == "done":
                    break

                # kind == "token"
                full_reply += value
                await send_json({
                    "type": "reply_chunk",
                    "data": {"text": value}
                })
                if interrupted:
                    break

        except asyncio.CancelledError:
            logger.info(f"[WS:{session_id}] Turn cancelled")
            interrupted = True
            raise  # re-raise so the task is properly cancelled
        except Exception as e:
            logger.exception(f"[WS:{session_id}] LLM pipeline error: {e}")

        if not interrupted:
            await send_json({
                "type": "reply_end",
                "data": {"interrupted": interrupted}
            })

        # ── Translation (Chinese) ──
        if full_reply.strip():
            try:
                _trans_conv = create_conversation("default")
                _trans_conv.messages[0] = {
                    "role": "system",
                    "content": "Translate the following English to natural Chinese (中文). Return ONLY the translation, no explanation."
                }
                _trans_conv.add_user_message(full_reply.strip())
                _translation = await asyncio.to_thread(llm.reply, _trans_conv)
                if _translation and _translation.strip():
                    await send_json({
                        "type": "translation",
                        "data": {"text": _translation.strip()}
                    })
            except Exception:
                pass  # Translation is optional

        # ── Context usage update ──
        msg_count = len(conversation.messages) - 1  # exclude system prompt
        await send_json({
            "type": "context_usage",
            "data": {"used": msg_count, "max": conversation.max_history}
        })

        if not full_reply.strip():
            return

        # ── Phase 2: score ──
        await send_json({
            "type": "score_update",
            "data": {"score": min(95, 60 + utterance_count * 5 + 30)}
        })

        # ── Phase 3: streaming TTS ──
        if not interrupted:
            t0 = time.time()
            try:
                communicate = edge_tts.Communicate(full_reply.strip(), config.TTS_VOICE)
                async for chunk in communicate.stream():
                    if interrupted:
                        break
                    if chunk["type"] == "audio":
                        try:
                            await websocket.send_bytes(chunk["data"])
                        except RuntimeError:
                            break  # client disconnected
                elapsed = time.time() - t0
                logger.info(f"[WS:{session_id}] TTS done: {len(full_reply)} chars, {elapsed:.1f}s")
            except asyncio.CancelledError:
                logger.info(f"[WS:{session_id}] TTS cancelled")
                raise
            except Exception as e:
                logger.warning(f"[WS:{session_id}] TTS error: {e}")

    # ── Scene greeting: warm up LLM connection + welcome the user ──
    _greeting_prompts = {
        "ordering": "You are a waiter. Greet the customer as they sit down. Ask what they'd like to order. 1-2 sentences only.",
        "interview": "You are a hiring manager. Greet the candidate and ask them to introduce themselves. 1-2 sentences only.",
        "meeting": "You are a team lead. Open the meeting and ask for a project update. 1-2 sentences only.",
        "travel": "You are a hotel receptionist. Greet the guest and ask how you can help. 1-2 sentences only.",
        "daily": "You are a friendly chat partner. Greet the user and ask how their day is going. 1-2 sentences only.",
        "business": "You are a business partner. Greet your colleague and ask about their progress. 1-2 sentences only.",
        "default": "You are a friendly English conversation partner. Greet the user warmly and ask what they'd like to practice today. 1-2 sentences only.",
    }

    async def _send_greeting(custom_prompt: str = None):
        try:
            if custom_prompt:
                system_msg = f"You are an AI English conversation partner. Scenario: {custom_prompt}. Greet the user and start a conversation about this topic. 1-2 sentences only."
            else:
                system_msg = _greeting_prompts.get(scene, _greeting_prompts["default"])

            greet_conv = create_conversation(scene)
            greet_conv.messages[0] = {"role": "system", "content": system_msg}
            greet_conv.add_user_message("Greet me to start the conversation.")

            await send_json({"type": "reply_start"})
            full_text = ""
            queue: asyncio.Queue = asyncio.Queue()

            def _run():
                try:
                    for token in llm.reply_stream(greet_conv):
                        queue.put_nowait(("token", token))
                    queue.put_nowait(("done", None))
                except Exception as exc:
                    queue.put_nowait(("error", exc))

            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, _run)

            while True:
                try:
                    kind, value = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    break
                if kind == "error" or kind == "done":
                    break
                full_text += value
                await send_json({"type": "reply_chunk", "data": {"text": value}})

            await send_json({"type": "reply_end", "data": {"interrupted": False}})

            if full_text.strip():
                communicate = edge_tts.Communicate(full_text.strip(), config.TTS_VOICE)
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        await websocket.send_bytes(chunk["data"])
                logger.info(f"[WS:{session_id}] Greeting TTS done")

            logger.info(f"[WS:{session_id}] Greeting sent: '{full_text[:50]}'")
        except Exception as e:
            logger.warning(f"[WS:{session_id}] Greeting failed (non-fatal): {e}")

    # Fire greeting immediately for preset scenes (custom defers to after topic arrives)
    if scene != "custom":
        asyncio.create_task(_send_greeting())

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except RuntimeError:
                # WebSocket already closed (e.g., TTS error)
                break
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            # -- Audio chunk (ASR pipeline) --
            if msg_type == "audio_chunk":
                chunk_data = msg.get("data", {}).get("data", "")
                is_end = msg.get("data", {}).get("is_end", False)

                try:
                    raw_bytes = base64.b64decode(chunk_data)
                    buf.extend(raw_bytes)
                except Exception as e:
                    logger.warning(f"[WS:{session_id}] base64 decode error: {e}")

                if is_end and len(buf) > 1024:
                    utterance_count += 1
                    recognizer = get_asr_engine()
                    _ = recognizer.model

                    # Tell frontend we're processing
                    await send_json({"type": "processing", "data": {"message": "识别中..."}})

                    text = ""
                    is_webm = len(buf) > 4 and bytes(buf[:4]) == b'\x1a\x45\xdf\xa3'

                    if not is_webm and len(buf) < 500000:
                        try:
                            result = recognizer.transcribe_pcm(bytes(buf), 16000)
                            text = result.get("text", "").strip()
                            logger.info(f"[WS:{session_id}] ASR(PCM): '{text[:80]}'")
                        except Exception as e:
                            logger.info(f"[WS:{session_id}] PCM failed: {e}, trying file...")
                            text = ""

                    if not text:
                        try:
                            ext = "webm" if is_webm else "audio"
                            tmp = config.AUDIO_DIR / f"upload_{int(time.time()*1000)}.{ext}"
                            tmp.write_bytes(bytes(buf))
                            result = recognizer.transcribe_file(str(tmp))
                            text = result.get("text", "").strip()
                            logger.info(f"[WS:{session_id}] ASR(file): '{text[:80]}'")
                            tmp.unlink(missing_ok=True)
                        except Exception as e2:
                            logger.warning(f"[WS:{session_id}] ASR fallback error: {e2}")

                    if text:
                        # Track scores for report
                        pron = result.get("pronunciation", 0)
                        flu = result.get("fluency", 0)
                        if pron > 0:
                            pron_scores.append(pron)
                        if flu > 0:
                            flu_scores.append(flu)

                        # Show user what was recognized (with pronunciation & fluency scores)
                        await send_json({
                            "type": "transcript",
                            "data": {
                                "text": text,
                                "is_final": True,
                                "is_user": True,
                                "pronunciation": pron,
                                "fluency": flu,
                            }
                        })
                        await start_turn(text)
                    else:
                        await send_json({
                            "type": "error",
                            "data": {"message": "No speech detected"}
                        })
                    buf.clear()

            # -- Text message --
            elif msg_type == "text_message":
                text = msg.get("data", {}).get("text", "")
                if text.strip():
                    utterance_count += 1
                    logger.info(f"[WS:{session_id}] Text: '{text[:80]}'")
                    await start_turn(text)

            # -- Interrupt --
            elif msg_type == "interrupt":
                logger.info(f"[WS:{session_id}] Interrupted")
                buf.clear()
                await cancel_current_turn()
                await send_json({
                    "type": "reply_end",
                    "data": {"interrupted": True}
                })

            # -- Custom scene --
            elif msg_type == "custom_scene":
                description = msg.get("data", {}).get("description", "")
                if description.strip():
                    conversation.set_custom_scene(description.strip())
                    scene = "custom"
                    logger.info(f"[WS:{session_id}] Custom scene set: '{description[:80]}'")
                    await send_json({
                        "type": "custom_scene_ready",
                        "data": {"scene": "custom", "description": description.strip()}
                    })
                    # Fire greeting now that we know the topic
                    asyncio.create_task(_send_greeting(custom_prompt=description.strip()))
                else:
                    await send_json({
                        "type": "error",
                        "data": {"message": "Scene description cannot be empty"}
                    })

            # -- Scene switch --
            elif msg_type == "scene_select":
                new_scene = msg.get("data", {}).get("scene", "ordering")
                if new_scene in VALID_SCENES:
                    scene = new_scene
                    conversation.set_scene(scene)
                    logger.info(f"[WS:{session_id}] Scene changed to {scene}")
                    await send_json({
                        "type": "reply_chunk",
                        "data": {"text": f"[Switched to {scene} scene]"}
                    })
                else:
                    await send_json({
                        "type": "error",
                        "data": {"message": f"Unknown scene: {new_scene}"}
                    })

            # -- End session --
            elif msg_type == "end_session":
                duration = time.time() - session_start
                duration_sec = int(duration)

                # Calculate average pronunciation & fluency
                avg_pron = sum(pron_scores) // len(pron_scores) if pron_scores else 75
                avg_flu = sum(flu_scores) // len(flu_scores) if flu_scores else 75

                # Aggregate error stats
                error_counts = {}
                for corr in all_corrections:
                    for err in corr.errors:
                        etype = err.type
                        error_counts[etype] = error_counts.get(etype, 0) + 1

                # Grammar score (grammar + tense + preposition + article)
                grammar_errors = sum(error_counts.get(k, 0) for k in ["grammar", "tense", "preposition", "article"])
                grammar_score = max(0, 100 - grammar_errors * 10)

                # Vocabulary score (vocabulary + word_choice + expression)
                vocab_errors = sum(error_counts.get(k, 0) for k in ["vocabulary", "word_choice", "expression"])
                vocab_score = max(0, 100 - vocab_errors * 10)

                # Error stats with labels
                LABEL_MAP = {
                    "grammar": "语法错误", "tense": "时态错误",
                    "preposition": "介词错误", "article": "冠词遗漏/误用",
                    "vocabulary": "词汇使用", "word_choice": "用词选择",
                    "expression": "表达问题",
                }
                error_stats = []
                for etype, count in sorted(error_counts.items(), key=lambda x: -x[1]):
                    if count > 0:
                        error_stats.append({
                            "type": etype,
                            "label": LABEL_MAP.get(etype, etype),
                            "count": count,
                        })

                # Scene name
                SCENE_NAMES = {
                    "ordering": "餐厅点餐", "interview": "工作面试",
                    "meeting": "商务会议", "travel": "旅行出行",
                    "custom": "自定义对话",
                }
                scene_display = f"{SCENE_NAMES.get(scene, scene)} ({scene})"

                logger.info(f"[WS:{session_id}] Session ended: {utterance_count} utterances, {duration_sec}s, "
                           f"grammar={grammar_score} vocab={vocab_score} pron={avg_pron} flu={avg_flu}")

                # Collect all individual errors (before suggestions so LLM can reference them)
                all_errors = []
                for corr in all_corrections:
                    for err in corr.errors:
                        all_errors.append({
                            "type": err.type,
                            "type_label": LABEL_MAP.get(err.type, err.type),
                            "original": err.original,
                            "corrected": err.corrected,
                            "explanation_cn": err.explanation_cn,
                            "sentence": corr.original_text,
                            "corrected_sentence": corr.corrected_text,
                        })

                # Generate personalised suggestions via LLM (with template fallback)
                suggestions = await _generate_llm_suggestions(
                    llm, scene_display, utterance_count, duration_sec,
                    grammar_score, vocab_score, avg_pron, avg_flu,
                    error_counts, all_errors, LABEL_MAP,
                )

                await send_json({
                    "type": "session_report",
                    "data": {
                        "scene": scene_display,
                        "duration_sec": duration_sec,
                        "turns": utterance_count,
                        "grammar": grammar_score,
                        "vocabulary": vocab_score,
                        "pronunciation": avg_pron,
                        "fluency": avg_flu,
                        "error_stats": error_stats,
                        "all_errors": all_errors,
                        "suggestions": suggestions,
                    }
                })
                break

            elif msg_type == "ping":
                await send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"[WS:{session_id}] Disconnected ({utterance_count} utterances)")
    except Exception as e:
        logger.exception(f"[WS:{session_id}] Error")
        try:
            await send_json({"type": "error", "data": {"message": str(e)}})
        except Exception:
            pass


# ===================================================================
# Static frontend
# ===================================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    """EchoSpeak-AI conversation page."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return HTMLResponse("<h1>EchoSpeak AI — English Speaking Practice</h1>")


@app.get("/test", response_class=HTMLResponse)
async def test_page():
    """ASR/TTS integrated test page with waveform, scoring, etc."""
    test_path = FRONTEND_DIR / "test.html"
    if test_path.exists():
        return test_path.read_text(encoding="utf-8")
    return HTMLResponse("<h1>Test page not found</h1>")


# ===================================================================
# Entry point
# ===================================================================

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting EchoSpeak AI dev server on http://127.0.0.1:{config.DEV_PORT}")
    uvicorn.run(
        "fastapi_server:app",
        host="127.0.0.1",
        port=config.DEV_PORT,
        reload=False,
        log_level="info",
    )
