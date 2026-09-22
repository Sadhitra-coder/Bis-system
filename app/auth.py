"""Service-to-service authentication and correlation ID tracking for BIS RAG Engine.

Authority: PRD_v2.0 §22, §24; Master Engineering Directive.
"""

import hmac
import logging
from typing import Optional

from fastapi import HTTPException, Request, Response, Security, status
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger(__name__)

INTERNAL_KEY_HEADER = APIKeyHeader(name="X-Internal-Service-Key", auto_error=False)


def verify_internal_service_key(api_key: Optional[str] = Security(INTERNAL_KEY_HEADER)) -> Optional[str]:
    """Verify incoming X-Internal-Service-Key against configured secret.

    If INTERNAL_SERVICE_KEY is configured on settings, requests with missing
    or mismatched keys receive an immediate 401 Unauthorized.
    """
    expected_key = getattr(settings, "INTERNAL_SERVICE_KEY", None)
    if not expected_key:
        return None

    # Allow test environment bypass when explicitly set
    if getattr(settings, "APP_ENV", "") == "test":
        return api_key

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required X-Internal-Service-Key header.",
        )

    if not hmac.compare_digest(api_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Internal-Service-Key.",
        )

    return api_key


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate X-Correlation-ID headers across request lifecycle."""

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID")
        if not correlation_id:
            import uuid
            correlation_id = f"bis-{uuid.uuid4().hex[:12]}"

        request.state.correlation_id = correlation_id
        response: Response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response
