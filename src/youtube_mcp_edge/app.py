"""Edge ASGI application (Stage 0 scaffold + Stage 1 static Bearer auth).

Responsibilities:
- `GET /healthz` — liveness endpoint for the edge itself.
- `/mcp` (any method) — transparent Streamable HTTP proxy to the private MCP
  core, gated by static Bearer auth.

The edge does NOT contain MCP tool/business logic; it only forwards to the core
after authentication. The credential is validated and then stripped before the
request is forwarded, so the private core never sees it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .auth import StaticAuthenticator
from .settings import EdgeSettings

logger = logging.getLogger("youtube_mcp_edge")

# Hop-by-hop headers that must not be forwarded upstream (RFC 9110 §7.6.1).
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
# Headers the edge manages itself and must never pass through.
_MANAGED = {"host", "content-length", "authorization"}


class RequestTooLarge(Exception):
    """Internal marker: request body exceeded the configured cap."""


class EdgeApp:
    def __init__(
        self,
        settings: EdgeSettings,
        *,
        upstream_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._authenticator = StaticAuthenticator(settings.static_tokens)
        if settings.static_auth_enabled and self._authenticator.configured is False:
            # Fail closed: an edge that is supposed to enforce auth but has no
            # configured credential is a misconfiguration, not a wide-open proxy.
            raise ValueError(
                "EDGE_STATIC_AUTH_ENABLED is set but no EDGE_STATIC_TOKENS are "
                "configured; refusing to start an auth-gated edge with no credential."
            )
        self._owns_client = upstream_client is None
        self._client: httpx.AsyncClient | None = upstream_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.upstream_timeout_seconds),
            follow_redirects=False,
        )

    # ---- lifecycle --------------------------------------------------------

    @asynccontextmanager
    async def _lifespan(self, app: Starlette) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {}
        finally:
            if self._owns_client and self._client is not None:
                await self._client.aclose()
                self._client = None

    def asgi(self) -> Starlette:
        routes = [
            Route("/healthz", self._handle_healthz, methods=["GET"]),
            Route(
                "/mcp",
                self._handle_mcp,
                methods=[
                    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS",
                ],
            ),
        ]
        return Starlette(routes=routes, lifespan=self._lifespan)

    # ---- handlers ---------------------------------------------------------

    async def _handle_healthz(self, request: Request) -> Response:
        # Liveness of the edge, not of the upstream core. Bounded, no secrets.
        return JSONResponse(
            {"status": "ok", "service": "youtube-mcp-edge"}, status_code=200
        )

    async def _handle_mcp(self, request: Request) -> Response:
        started = time.monotonic()
        # 1) Static Bearer auth (Stage 1) happens BEFORE any body buffering, so
        #    an unauthenticated caller cannot force request-body allocation.
        if self.settings.static_auth_enabled:
            authz = request.headers.get("authorization")
            if not self._authenticator.authenticate(authz):
                self._log(request, 401, started, "auth_denied")
                # Do not leak whether the header or the token was the problem.
                return JSONResponse(
                    {
                        "ok": False,
                        "error": {
                            "code": "unauthorized",
                            "message": "Missing or invalid credentials.",
                        },
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer realm="youtube-mcp"'},
                )
        # NOTE: the credential is INTENTIONALLY not forwarded upstream.

        # 2) Request-size protection before any tool work.
        try:
            body = await self._read_bounded(request)
        except RequestTooLarge:
            self._log(request, 413, started, "request_too_large")
            return JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "request_too_large",
                        "message": "Request body exceeds the configured size limit.",
                    },
                },
                status_code=413,
            )

        # 3) Forward transparently to the private core, streaming the response
        #    back (MCP Streamable HTTP may return JSON or text/event-stream).
        try:
            return await self._proxy(request, body, started)
        except httpx.HTTPError as exc:
            self._log(request, 502, started, "upstream_error")
            logger.exception("Upstream MCP request failed: %s", type(exc).__name__)
            return JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "upstream_unreachable",
                        "message": "The MCP upstream could not be reached.",
                    },
                },
                status_code=502,
            )

    # ---- helpers ----------------------------------------------------------

    async def _read_bounded(self, request: Request) -> bytes:
        """Read the request body, refusing bodies over the size cap."""
        limit = self.settings.max_request_bytes
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise RequestTooLarge()
            chunks.append(chunk)
        return b"".join(chunks)

    def _proxy_headers(self, request: Request, body: bytes) -> dict[str, str]:
        """Copy request headers, dropping hop-by-hop + managed + credential.

        Returns a dict (single value per header). MCP Streamable HTTP tool calls
        do not depend on duplicate request headers, so a dict is sufficient and
        avoids multi-value proxy complexity.
        """
        headers: dict[str, str] = {}
        for key, value in request.headers.items():
            name = key.lower()
            if name in _HOP_BY_HOP or name in _MANAGED:
                continue
            headers[key] = value
        headers["Content-Length"] = str(len(body))
        return headers

    async def _proxy(self, request: Request, body: bytes, started: float) -> Response:
        if self._client is None:
            raise RuntimeError("edge client not initialised (lifespan not run)")
        url = self.settings.upstream_url
        headers = self._proxy_headers(request, body)

        # Stream the upstream response so large JSON and SSE (text/event-stream)
        # bodies are not fully buffered in memory and SSE events are delivered as
        # they arrive. `send(stream=True)` keeps the connection open so the
        # StreamingResponse can consume it lazily; the iterator closes it.
        upstream_request = self._client.build_request(
            request.method, url, content=body, headers=headers
        )
        upstream = await self._client.send(upstream_request, stream=True)
        content_type = upstream.headers.get("content-type")

        async def _iter() -> AsyncIterator[bytes]:
            try:
                # aiter_bytes() yields the decoded body. We drop content-encoding
                # (and content-length) from the forwarded headers so the client
                # decodes exactly once.
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        response_headers: dict[str, str] = {}
        for key, value in upstream.headers.items():
            name = key.lower()
            if name in _HOP_BY_HOP or name in {"content-length", "content-encoding"}:
                # content-length is recomputed by StreamingResponse;
                # content-encoding is dropped because aiter_bytes() is decoded.
                continue
            response_headers[key] = value

        self._log(request, upstream.status_code, started, "forwarded")

        return StreamingResponse(
            _iter(),
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=content_type,
        )

    def _log(self, request: Request, status: int, started: float, outcome: str) -> None:
        # Safe logging: NEVER log the credential, authorization header, or body.
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "edge %s %s status=%d outcome=%s client=%s dt_ms=%d",
            request.method,
            request.url.path,
            status,
            outcome,
            request.client.host if request.client else "-",
            elapsed_ms,
        )


def create_app(
    settings: EdgeSettings | None = None,
    *,
    upstream_client: httpx.AsyncClient | None = None,
) -> Starlette:
    """Build the edge ASGI application from settings."""
    settings = settings or EdgeSettings.from_env()
    return EdgeApp(settings, upstream_client=upstream_client).asgi()


def main() -> None:
    """Entrypoint: run the edge under uvicorn.

    Validates configuration up front (fail fast) WITHOUT constructing a
    throwaway EdgeApp, so no second httpx client is created or leaked. The ASGI
    app is built fresh by uvicorn via factory=True.
    """
    import uvicorn

    settings = EdgeSettings.from_env()
    logging.basicConfig(level=logging.INFO)
    # Fail fast on auth-gated-with-no-tokens; from_env already validates the
    # upstream URL scheme and the auth-enable boolean strictly (fail closed).
    if settings.static_auth_enabled and not settings.static_tokens:
        raise SystemExit(
            "EDGE_STATIC_AUTH_ENABLED is set but no EDGE_STATIC_TOKENS are "
            "configured; refusing to start an auth-gated edge with no credential."
        )
    uvicorn.run(
        "youtube_mcp_edge.app:create_app",
        host=settings.host,
        port=settings.port,
        factory=True,
        log_level="info",
    )


__all__ = ["EdgeApp", "create_app", "EdgeSettings", "main"]