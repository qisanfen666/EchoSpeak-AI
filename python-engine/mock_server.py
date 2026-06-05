"""
gRPC Server with Real ASR + TTS
- StreamASR uses faster-whisper for real speech recognition
- Chat uses edge-tts for real audio response
"""

import asyncio
import time
import hashlib
import logging
from concurrent import futures

import grpc
from proto import aiservice_pb2 as pb2
from proto import aiservice_pb2_grpc as pb2_grpc

from services.asr_engine import get_asr_engine
from services.tts_engine import get_tts_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mock_server")

SCENE_REPLIES = {
    "ordering": [
        "Hello! What would you like to order today?",
        "Sure, one moment please. Would you like anything else with that?",
        "Your total is twelve dollars and fifty cents. Cash or card?",
    ],
    "interview": [
        "Tell me about your previous work experience.",
        "How do you handle tight deadlines?",
        "What are your long-term career goals?",
    ],
    "meeting": [
        "Let's go over the quarter two results first.",
        "Does anyone have feedback on the proposal?",
        "Great, let's set the next meeting for Friday.",
    ],
    "travel": [
        "Where are you headed today?",
        "Would you like a window seat or an aisle seat?",
        "Your boarding pass is ready. Gate B12, boarding at 3:30.",
    ],
}

MOCK_CORRECTIONS = [
    ("I go to store yesterday", "I went to the store yesterday", "grammar"),
    ("He don't like it", "He doesn't like it", "grammar"),
    ("Can I have a coffee", "Could I have a coffee, please?", "politeness"),
    ("I very like this", "I really like this", "vocabulary"),
]


def _pick_idx(key: str, length: int) -> int:
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % length


class MockAIService(pb2_grpc.AIServiceServicer):

    def Health(self, request, context):
        logger.info(f"[Mock] Health check from {context.peer()}")
        return pb2.HealthResponse(ok=True, message="Real ASR + TTS running")

    def StreamASR(self, request_iterator, context):
        """Real ASR: receive audio chunks, return recognized text."""
        audio_buf = bytearray()
        session_id = "unknown"

        for chunk in request_iterator:
            session_id = chunk.session_id
            audio_buf.extend(chunk.audio_data)
            if chunk.is_end:
                break

        logger.info(f"[ASR] Processing {len(audio_buf)} bytes for session {session_id}")

        try:
            engine = get_asr_engine()
            _ = engine.model  # ensure loaded
            result = engine.transcribe_pcm(bytes(audio_buf), 16000)
            text = result.get("text", "").strip()
            logger.info(f"[ASR] Recognized: \"{text}\"")
        except Exception as e:
            logger.error(f"[ASR] Failed: {e}")
            text = ""

        yield pb2.ASRResult(
            text=text or "I'd like to order a coffee please",
            is_final=True,
            session_id=session_id,
        )

    def Chat(self, request, context):
        scene = request.scene or "ordering"
        user_msg = request.user_message or "Hello"

        logger.info(f"[Mock] Chat: scene={scene} msg=\"{user_msg[:40]}\"")

        replies = SCENE_REPLIES.get(scene, SCENE_REPLIES["ordering"])
        reply = replies[_pick_idx(user_msg, len(replies))]

        # ---- Stream reply text word-by-word ----
        words = reply.split(" ")
        for i, word in enumerate(words):
            chunk_text = word + (" " if i < len(words) - 1 else "")
            yield pb2.ChatResponse(
                reply=pb2.ReplyChunk(text=chunk_text, is_first=(i == 0))
            )
            time.sleep(0.06)

        # ---- Correction ----
        corr = MOCK_CORRECTIONS[_pick_idx(user_msg, len(MOCK_CORRECTIONS))]
        yield pb2.ChatResponse(
            correction=pb2.Correction(
                original=corr[0], corrected=corr[1], error_type=corr[2],
            )
        )

        # ---- Real TTS Audio ----
        try:
            tts = get_tts_engine()
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            audio_bytes = loop.run_until_complete(tts.stream_speak(reply))
            logger.info(f"[Mock] TTS generated {len(audio_bytes)} bytes for reply")

            # Stream audio in 16KB chunks to avoid gRPC message limits
            chunk_size = 16384
            for offset in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[offset:offset + chunk_size]
                yield pb2.ChatResponse(tts_audio=chunk)
        except Exception as e:
            logger.error(f"[Mock] TTS failed: {e}")

        # ---- Done ----
        yield pb2.ChatResponse(done=True)
        logger.info(f"[Mock] Chat done: \"{reply[:40]}\" + TTS audio")


async def serve():
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_AIServiceServicer_to_server(MockAIService(), server)
    server.add_insecure_port("0.0.0.0:50052")
    await server.start()
    logger.info("[Mock] Listening on 0.0.0.0:50052 (with real TTS)")
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
