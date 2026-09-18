"""Gemini LLM provider for operator-note interpretation."""

import json
import logging
import asyncio
from typing import List

from google import genai
from google.genai import types

from app.llm.base import LLMProvider, LLMError
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.config import get_settings

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """Concrete LLM provider using Google Gemini API."""

    def __init__(self):
        settings = get_settings()
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY environment variable is not set")
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.llm_model
        self._max_retries = settings.llm_max_retries
        self._timeout = settings.llm_timeout_seconds

    async def interpret_notes(
        self,
        operator_notes: List[str],
        battery_capacity_kwh: float,
    ) -> List[dict]:
        """Interpret operator notes using Gemini with structured JSON output."""
        user_prompt = build_user_prompt(operator_notes, battery_capacity_kwh)

        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._call_gemini(user_prompt, len(operator_notes))
            except Exception as e:
                last_error = e
                logger.warning(
                    f"LLM attempt {attempt + 1}/{self._max_retries + 1} failed: {e}"
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))

        raise LLMError(f"All LLM attempts failed. Last error: {last_error}")

    async def _call_gemini(
        self, user_prompt: str, num_notes: int
    ) -> List[dict]:
        """Make a single Gemini API call and parse the JSON response."""
        # Use JSON response mode for structured output
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )

        if not response.text:
            raise LLMError("Empty response from Gemini")

        # Parse the JSON response
        try:
            raw_text = response.text.strip()
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise LLMError(f"Gemini returned invalid JSON: {e}\nRaw: {raw_text[:500]}")

        # Ensure we got a list
        if isinstance(parsed, dict):
            # Sometimes the model wraps in an object
            for key in ("interpretations", "directives", "results", "data"):
                if key in parsed and isinstance(parsed[key], list):
                    parsed = parsed[key]
                    break
            else:
                raise LLMError(f"Expected JSON array, got object: {list(parsed.keys())}")

        if not isinstance(parsed, list):
            raise LLMError(f"Expected JSON array, got {type(parsed).__name__}")

        if len(parsed) != num_notes:
            logger.warning(
                f"LLM returned {len(parsed)} interpretations for {num_notes} notes"
            )

        return parsed
