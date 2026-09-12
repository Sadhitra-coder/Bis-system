"""
Shared Groq client with retry / exponential backoff.

Both the ingestion structuring stage (app/steps/structure.py)
and answer generation (app/rag/generator.py) go through here so
that retry behaviour, error typing, and configuration live in
exactly one place.

Design rules:

    1. Transient failures (rate limits, timeouts, 5xx) are
       retried with exponential backoff plus jitter.

    2. Permanent failures (bad API key, bad request, unknown
       model) are raised immediately - retrying cannot help.

    3. Exhausted retries raise LLMError. Callers decide what to
       do; this module never invents a successful result.
"""

import logging
import random
import time
from typing import Any, Dict, List, Optional

from app.config import settings


logger = logging.getLogger(__name__)


# ============================================================
# ERRORS
# ============================================================

class LLMError(Exception):
    """Base class for all LLM failures raised by this module."""


class LLMUnavailableError(LLMError):
    """
    The LLM cannot be used at all.

    Raised when the API key is missing or the client library
    cannot be constructed.
    """


class LLMCallError(LLMError):
    """
    A completion request failed and could not be recovered
    by retrying.
    """


# ============================================================
# TRANSIENT ERROR DETECTION
# ============================================================

# Substrings that indicate a retryable condition. Matched
# against the exception type name and message so that this
# module does not have to import Groq's exception classes
# (which move between library versions).

_TRANSIENT_MARKERS = (
    "ratelimit",
    "rate limit",
    "rate_limit",
    "timeout",
    "timed out",
    "connection",
    "temporarily unavailable",
    "service unavailable",
    "internalserver",
    "internal server",
    "bad gateway",
    "overloaded",
    "apiconnection",
    "too many requests",
    "429",
    "500",
    "502",
    "503",
    "504",
)

# Substrings that indicate retrying is pointless.
_PERMANENT_MARKERS = (
    "authentication",
    "invalid api key",
    "unauthorized",
    "401",
    "403",
    "permission",
    "model_not_found",
    "does not exist",
    "invalid_request",
    "badrequest",
    "400",
    "404",
)


def is_transient_error(error: BaseException) -> bool:
    """
    Decide whether an exception is worth retrying.

    Permanent markers win over transient markers so that, for
    example, a 401 is never retried even if the message also
    happens to mention "connection".
    """

    text = f"{type(error).__name__} {error}".lower()

    for marker in _PERMANENT_MARKERS:
        if marker in text:
            return False

    for marker in _TRANSIENT_MARKERS:
        if marker in text:
            return True

    # Unknown failures are not retried. Retrying an error we do
    # not understand risks multiplying a real bug by N.
    return False


# ============================================================
# CLIENT
# ============================================================

class GroqClient:
    """
    Thin wrapper over groq.Groq adding retry and backoff.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        max_retries: Optional[int] = None,
        base_delay: Optional[float] = None,
        max_delay: Optional[float] = None
    ):
        """
        Parameters
        ----------
        api_key:
            Groq API key. Falls back to settings.GROQ_API_KEY.

        max_retries:
            Number of retries AFTER the first attempt.
            Falls back to settings.GROQ_MAX_RETRIES.

        base_delay:
            First backoff delay in seconds.

        max_delay:
            Upper bound on any single backoff delay.

        Raises
        ------
        LLMUnavailableError
            If no API key is available, or the Groq client
            cannot be constructed.
        """

        resolved_key = api_key or settings.GROQ_API_KEY

        if not resolved_key:
            raise LLMUnavailableError(
                "GROQ_API_KEY is not set. "
                "Add it to .env (see .env.example)."
            )

        self.max_retries = (
            settings.GROQ_MAX_RETRIES
            if max_retries is None
            else max_retries
        )

        self.base_delay = (
            settings.GROQ_RETRY_BASE_DELAY
            if base_delay is None
            else base_delay
        )

        self.max_delay = (
            settings.GROQ_RETRY_MAX_DELAY
            if max_delay is None
            else max_delay
        )

        try:
            from groq import Groq
        except Exception as error:
            raise LLMUnavailableError(
                f"The 'groq' package is not importable: {error}"
            ) from error

        try:
            self._client = Groq(api_key=resolved_key)
        except Exception as error:
            raise LLMUnavailableError(
                f"Could not construct the Groq client: {error}"
            ) from error

    # --------------------------------------------------------
    # BACKOFF
    # --------------------------------------------------------

    def _delay_for_attempt(self, attempt: int) -> float:
        """
        Exponential backoff with full jitter.

        attempt is 1-based: the delay after the first failure.
        """

        raw = self.base_delay * (2 ** (attempt - 1))

        capped = min(raw, self.max_delay)

        # Full jitter avoids synchronised retries when several
        # documents are ingested concurrently.
        return random.uniform(0.0, capped)

    # --------------------------------------------------------
    # COMPLETION
    # --------------------------------------------------------

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.0,
        max_completion_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        description: str = "completion"
    ) -> str:
        """
        Run one chat completion, retrying transient failures.

        Parameters
        ----------
        messages:
            Chat messages in Groq/OpenAI format.

        model:
            Model identifier.

        temperature:
            Sampling temperature.

        max_completion_tokens:
            Output token ceiling.

        response_format:
            e.g. {"type": "json_object"} to force JSON.

        description:
            Short label used in log messages so failures can be
            traced to a specific call site.

        Returns
        -------
        str
            The assistant message content. Never empty - empty
            content is treated as a transient failure and
            retried.

        Raises
        ------
        LLMCallError
            The call failed permanently, or every retry was
            exhausted.
        """

        request: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        if max_completion_tokens is not None:
            request["max_completion_tokens"] = max_completion_tokens

        if response_format is not None:
            request["response_format"] = response_format

        total_attempts = self.max_retries + 1

        last_error: Optional[BaseException] = None

        for attempt in range(1, total_attempts + 1):

            try:

                completion = (
                    self._client.chat.completions.create(**request)
                )

                content = completion.choices[0].message.content

                if not content or not content.strip():
                    # Treated as transient: the request was
                    # accepted but produced nothing usable.
                    raise TimeoutError(
                        "Groq returned empty content."
                    )

                if attempt > 1:
                    logger.info(
                        "Groq %s succeeded on attempt %d/%d.",
                        description,
                        attempt,
                        total_attempts
                    )

                return content

            except Exception as error:

                last_error = error

                permanent = not is_transient_error(error)

                exhausted = attempt >= total_attempts

                if permanent:
                    logger.error(
                        "Groq %s failed permanently on attempt "
                        "%d/%d: %s",
                        description,
                        attempt,
                        total_attempts,
                        error
                    )

                    raise LLMCallError(
                        f"Groq {description} failed permanently: "
                        f"{error}"
                    ) from error

                if exhausted:
                    logger.error(
                        "Groq %s failed after %d attempts: %s",
                        description,
                        total_attempts,
                        error
                    )
                    break

                delay = self._delay_for_attempt(attempt)

                logger.warning(
                    "Groq %s failed on attempt %d/%d (%s). "
                    "Retrying in %.2fs.",
                    description,
                    attempt,
                    total_attempts,
                    error,
                    delay
                )

                time.sleep(delay)

        raise LLMCallError(
            f"Groq {description} failed after "
            f"{total_attempts} attempts: {last_error}"
        ) from last_error
