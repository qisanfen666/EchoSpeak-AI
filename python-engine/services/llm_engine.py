"""
LLM module — AI conversation engine for English speaking practice.
Uses OpenAI-compatible API (DeepSeek, OpenAI, Azure, Groq, etc.).
"""

import json
import time
import logging
from typing import AsyncGenerator, Optional

from openai import OpenAI

from config import config

logger = logging.getLogger(__name__)

# Scene system prompts
SCENE_SYSTEM_PROMPTS = {
    "ordering": (
        "You are a friendly waiter at an upscale restaurant. "
        "The user is a customer practicing English. "
        "Respond naturally to their order, ask follow-up questions, "
        "and help them practice restaurant conversation. "
        "Keep your responses concise (1-3 sentences). "
        "Use correct grammar in your own speech — never repeat or echo the user's sentence back to them."
    ),
    "interview": (
        "You are a hiring manager conducting a job interview in English. "
        "Ask the user questions about their experience, skills, and background. "
        "Respond to their answers naturally. "
        "Keep your responses concise (1-3 sentences). "
        "Use correct grammar in your own speech — never repeat or echo the user's sentence back to them."
    ),
    "meeting": (
        "You are a team lead in a business meeting. "
        "Discuss project progress, action items, and next steps. "
        "Keep your responses concise (1-3 sentences). "
        "Use correct grammar in your own speech — never repeat or echo the user's sentence back to them."
    ),
    "travel": (
        "You are a hotel receptionist helping a guest. "
        "Respond to their questions about check-in, room service, directions, etc. "
        "Keep your responses concise (1-3 sentences). "
        "Use correct grammar in your own speech — never repeat or echo the user's sentence back to them."
    ),
    "default": (
        "You are an AI English conversation partner. "
        "Help the user practice their spoken English by having a natural conversation. "
        "Keep your responses concise (1-3 sentences). "
        "Use correct grammar in your own speech — never repeat or echo the user's sentence back to them."
    ),
}


class Conversation:
    """Manages a multi-turn conversation with LLM context."""

    def __init__(self, scene: str = "default", max_history: int = 20):
        system = SCENE_SYSTEM_PROMPTS.get(scene, SCENE_SYSTEM_PROMPTS["default"])
        self.messages = [{"role": "system", "content": system}]
        self.max_history = max_history
        self.scene = scene

    def set_scene(self, scene: str):
        """Switch scene — resets conversation history."""
        self.scene = scene
        system = SCENE_SYSTEM_PROMPTS.get(scene, SCENE_SYSTEM_PROMPTS["default"])
        self.messages = [{"role": "system", "content": system}]

    def add_user_message(self, text: str):
        self.messages.append({"role": "user", "content": text})
        # Trim history if too long (keep system prompt)
        if len(self.messages) > self.max_history + 1:
            self.messages = [self.messages[0]] + self.messages[-(self.max_history):]

    def add_assistant_message(self, text: str):
        self.messages.append({"role": "assistant", "content": text})


class LLMEngine:
    """LLM engine using OpenAI-compatible API."""

    def __init__(self):
        self._client = None
        self._model = config.LLM_MODEL or "deepseek-chat"
        logger.info(f"LLM engine initialised (model={self._model})")

    @property
    def client(self) -> Optional[OpenAI]:
        if self._client is None:
            if not config.LLM_API_KEY:
                logger.warning("LLM_API_KEY not set — LLM unavailable")
                return None
            self._client = OpenAI(
                api_key=config.LLM_API_KEY,
                base_url=config.LLM_BASE_URL or "https://api.deepseek.com",
            )
        return self._client

    @property
    def available(self) -> bool:
        return self.client is not None

    def reply(self, conversation: Conversation) -> str:
        """
        Get a complete (non-streaming) reply from the LLM.
        Returns the response text, or a fallback message on error.
        """
        if not self.available:
            return self._fallback_reply(conversation.scene)

        t0 = time.time()
        try:
            client = self.client
            response = client.chat.completions.create(
                model=self._model,
                messages=conversation.messages,
                max_tokens=300,
                temperature=0.7,
                stream=False,
            )
            text = response.choices[0].message.content or ""
            elapsed = time.time() - t0
            logger.info(f"LLM reply ({elapsed:.1f}s): '{text[:60]}...'")
            conversation.add_assistant_message(text)
            return text
        except Exception as e:
            logger.exception(f"LLM request failed: {e}")
            return self._fallback_reply(conversation.scene)

    def reply_stream(self, conversation: Conversation) -> AsyncGenerator[str, None]:
        """
        Stream a reply from the LLM, yielding text chunks as they arrive.
        """
        if not self.available:
            yield self._fallback_reply(conversation.scene)
            return

        t0 = time.time()
        full_text = ""
        try:
            client = self.client
            stream = client.chat.completions.create(
                model=self._model,
                messages=conversation.messages,
                max_tokens=300,
                temperature=0.7,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    full_text += delta.content
                    yield delta.content

            elapsed = time.time() - t0
            logger.info(f"LLM stream reply ({elapsed:.1f}s): '{full_text[:60]}...'")
            conversation.add_assistant_message(full_text)

        except Exception as e:
            logger.exception(f"LLM stream failed: {e}")
            yield self._fallback_reply(conversation.scene)

    def _fallback_reply(self, scene: str = "default") -> str:
        """Fallback when LLM is unavailable."""
        fallbacks = {
            "ordering": "That sounds great! Would you like any sides with your meal?",
            "interview": "That's very interesting! Can you tell me more about your experience?",
            "meeting": "Good point. Let's discuss the next steps for this project.",
            "travel": "Welcome! Let me check you in. Do you have a reservation with us?",
        }
        return fallbacks.get(scene, fallbacks["ordering"])


# Singleton
_engine: LLMEngine | None = None


def get_llm() -> LLMEngine:
    global _engine
    if _engine is None:
        _engine = LLMEngine()
    return _engine


def create_conversation(scene: str = "default") -> Conversation:
    return Conversation(scene=scene)
