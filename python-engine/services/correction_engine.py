"""
Correction engine — grammar & expression analysis for English speaking practice.
Uses a separate LLM call (OpenAI-compatible) to produce structured JSON corrections.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

from openai import OpenAI

from config import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt — instructs the LLM to output ONLY JSON
# ---------------------------------------------------------------------------
CORRECTION_SYSTEM_PROMPT = """You are a professional English teacher and grammar expert.
Analyse the user's English sentence for grammar mistakes, unnatural expressions,
wrong word choices, preposition errors, tense errors, and missing/extra articles.

Rules:
1. Output ONLY valid JSON — no markdown, no extra text, no explanation outside the JSON.
2. If the sentence is perfectly natural and grammatically correct, return an empty errors array.
3. Do NOT flag conversational contractions (gonna, wanna, kinda, gotta) as errors — they are natural in spoken English.
4. Do NOT flag minor word order variations that are still natural.
5. For each error, identify the specific fragment (word or short phrase), not the whole sentence.
6. Each explanation_cn MUST be very short, at most 20 Chinese characters. Be specific and terse.
7. Order errors by severity: grammar/tense first, then expression/vocabulary.
8. CRITICAL: keep your ENTIRE response under 400 tokens. Use very short explanations.
9. Output ONLY raw JSON, no markdown wrapping, no backticks.

JSON schema:
{
  "corrected_text": "The fully corrected version of the whole sentence",
  "errors": [
    {
      "type": "grammar|tense|preposition|article|vocabulary|word_choice|expression",
      "original": "the incorrect fragment",
      "corrected": "the corrected fragment",
      "explanation_cn": "中文解释，简明扼要"
    }
  ]
}"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ErrorDetail:
    """A single correction detail."""
    type: str              # grammar, tense, preposition, article, vocabulary, word_choice, expression
    original: str          # the incorrect fragment
    corrected: str         # the corrected fragment
    explanation_cn: str    # Chinese explanation


@dataclass
class CorrectionResult:
    """Structured correction result for a user utterance."""
    original_text: str
    corrected_text: str
    errors: list[ErrorDetail] = field(default_factory=list)
    has_corrections: bool = False

    def to_dict(self) -> dict:
        return {
            "original_text": self.original_text,
            "corrected_text": self.corrected_text,
            "errors": [asdict(e) for e in self.errors],
            "has_corrections": self.has_corrections,
        }

    @classmethod
    def empty(cls, original_text: str) -> "CorrectionResult":
        """Return a no-correction result for perfect sentences or errors."""
        return cls(
            original_text=original_text,
            corrected_text=original_text,
            errors=[],
            has_corrections=False,
        )


# ---------------------------------------------------------------------------
# Truncated JSON repair
# ---------------------------------------------------------------------------
def _repair_truncated_json(raw: str) -> str | None:
    """Attempt to close truncated JSON: unterminated strings + open brackets."""
    closing = ''
    stack = []
    in_string = False
    escape = False

    for ch in raw:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in '{[':
            stack.append('}' if ch == '{' else ']')
        elif ch in '}]':
            if stack and stack[-1] == ch:
                stack.pop()
            else:
                return None

    # If still inside a string, close it first
    if in_string:
        closing += '"'

    # Close brackets in reverse order
    while stack:
        closing += stack.pop()

    return raw + closing


# ---------------------------------------------------------------------------
# Correction Engine
# ---------------------------------------------------------------------------
class CorrectionEngine:
    """Grammar & expression correction using a dedicated LLM call."""

    def __init__(self):
        self._llm = None          # lazy: imported when needed
        self._client: Optional[OpenAI] = None
        logger.info("Correction engine initialised")

    @property
    def client(self) -> Optional[OpenAI]:
        """Lazy-init OpenAI client (reuses LLM config)."""
        if self._client is None:
            if not config.LLM_API_KEY:
                logger.warning("LLM_API_KEY not set — correction unavailable")
                return None
            self._client = OpenAI(
                api_key=config.LLM_API_KEY,
                base_url=config.LLM_BASE_URL or "https://api.openai.com/v1",
            )
        return self._client

    @property
    def available(self) -> bool:
        return self.client is not None

    # ------------------------------------------------------------------
    def correct(self, text: str) -> CorrectionResult:
        """
        Analyse a user's English sentence and return structured corrections.

        Returns CorrectionResult.empty() on any failure — this method never raises.
        """
        if not text or not text.strip():
            return CorrectionResult.empty(text)

        original = text.strip()

        if not self.available:
            logger.debug("Correction skipped: LLM not available")
            return CorrectionResult.empty(original)

        try:
            response = self.client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[
                    {"role": "system", "content": CORRECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": original},
                ],
                max_tokens=800,
                temperature=0.1,
                stream=False,
            )
            raw = response.choices[0].message.content or ""
            return self._parse_response(original, raw)

        except Exception as exc:
            logger.warning(f"Correction LLM call failed (non-blocking): {exc}")
            return CorrectionResult.empty(original)

    # ------------------------------------------------------------------
    def _parse_response(self, original: str, raw: str) -> CorrectionResult:
        """Parse LLM JSON output into a structured CorrectionResult."""
        raw = raw.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            # Remove opening fence (```json or ```)
            if lines[0].startswith("```"):
                lines = lines[1:]
            # Remove closing fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to recover truncated JSON by closing open structures
            repaired = _repair_truncated_json(raw)
            if repaired:
                try:
                    data = json.loads(repaired)
                except json.JSONDecodeError as exc:
                    logger.warning(f"Correction JSON parse error: {exc} | raw={raw[:200]}")
                    return CorrectionResult.empty(original)
            else:
                logger.warning(f"Correction JSON unrecoverable | raw={raw[:200]}")
                return CorrectionResult.empty(original)

        # Validate and build result
        corrected_text = data.get("corrected_text", original) or original
        raw_errors = data.get("errors", [])
        if not isinstance(raw_errors, list):
            raw_errors = []

        errors: list[ErrorDetail] = []
        valid_types = {
            "grammar", "tense", "preposition", "article",
            "vocabulary", "word_choice", "expression",
        }

        for err in raw_errors:
            if not isinstance(err, dict):
                continue
            etype = err.get("type", "grammar")
            if etype not in valid_types:
                etype = "grammar"
            errors.append(ErrorDetail(
                type=etype,
                original=err.get("original", ""),
                corrected=err.get("corrected", ""),
                explanation_cn=err.get("explanation_cn", ""),
            ))

        has_corrections = len(errors) > 0 or corrected_text != original
        if has_corrections and errors:
            logger.info(
                f"Correction: {len(errors)} error(s) — "
                f"'{original[:40]}' -> '{corrected_text[:40]}'"
            )

        return CorrectionResult(
            original_text=original,
            corrected_text=corrected_text,
            errors=errors,
            has_corrections=has_corrections,
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_correction_engine: Optional[CorrectionEngine] = None


def get_correction_engine() -> CorrectionEngine:
    global _correction_engine
    if _correction_engine is None:
        _correction_engine = CorrectionEngine()
    return _correction_engine
