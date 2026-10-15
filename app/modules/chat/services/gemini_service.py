"""Google Gemini streaming client.

Thin async wrapper over the `google-genai` SDK: it owns the client instance,
prompt/config assembly, and error translation. All RAG logic (retrieval,
context budgeting, history replay, persistence) lives in ChatService — this
service only turns (system instruction, message list) into text deltas.
"""

import logging
from collections.abc import AsyncIterator, Sequence

from google import genai
from google.genai import types

from app.core.config import settings

logger = logging.getLogger(__name__)


class GeminiError(Exception):
    """Gemini is unreachable, misconfigured, or returned no usable content."""


def _to_contents(messages: Sequence[tuple[str, str]]) -> list[types.Content]:
    """Map (role, text) pairs — roles are 'user' | 'assistant' — to Gemini turns."""
    contents: list[types.Content] = []
    for role, text in messages:
        gemini_role = "model" if role == "assistant" else "user"
        contents.append(
            types.Content(
                role=gemini_role,
                parts=[types.Part.from_text(text=text)],
            )
        )
    return contents


class GeminiService:
    def __init__(self) -> None:
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        """Lazily build the SDK client so the app boots without an API key
        (development); the first chat request then fails fast with GeminiError."""
        if self._client is None:
            if not settings.GEMINI_API_KEY:
                raise GeminiError("GEMINI_API_KEY is not configured")
            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        return self._client

    async def stream(
        self,
        *,
        system_instruction: str,
        messages: Sequence[tuple[str, str]],
    ) -> AsyncIterator[str]:
        """Yield the answer incrementally as text deltas.

        Raises GeminiError on API failures and when the model returns no text
        at all (e.g. a safety block) — callers translate it into an SSE `error`
        event or an HTTP error.
        """
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=settings.GEMINI_MAX_OUTPUT_TOKENS,
            temperature=0.2,
        )
        try:
            response_stream = await self.client.aio.models.generate_content_stream(
                model=settings.GEMINI_MODEL,
                contents=_to_contents(messages),
                config=config,
            )
        except GeminiError:
            raise
        except Exception as exc:
            logger.exception("Gemini request failed (%s)", settings.GEMINI_MODEL)
            raise GeminiError("The language model could not be reached") from exc

        emitted = False
        try:
            async for chunk in response_stream:
                # chunk.text raises ValueError when a candidate carries no text
                # (safety filters, empty deltas) — skip those chunks.
                try:
                    text = chunk.text
                except ValueError:
                    continue
                if text:
                    emitted = True
                    yield text
        except GeminiError:
            raise
        except Exception as exc:
            logger.exception("Gemini stream interrupted (%s)", settings.GEMINI_MODEL)
            raise GeminiError("The answer stream was interrupted") from exc

        if not emitted:
            raise GeminiError("The model returned no content")


# Module-level singleton, mirroring StorageService (no per-request state).
gemini_service = GeminiService()
