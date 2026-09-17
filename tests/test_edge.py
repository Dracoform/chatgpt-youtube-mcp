"""Tests for the edge component (Stage 0 scaffold + Stage 1 static Bearer auth).

Run with:  uv run python -m unittest tests.test_edge -v
or plainly: python -m unittest tests.test_edge -v  (needs starlette/httpx, which
come in transitively via the `mcp` runtime dependency / the `edge` extra).
"""

from __future__ import annotations

import logging
import unittest

import httpx
from starlette.testclient import TestClient

from youtube_mcp_edge.app import EdgeApp
from youtube_mcp_edge.settings import make_settings


class FakeUpstream:
    """A tiny faked MCP core: records requests and returns a scripted response."""

    def __init__(
        self,
        status: int = 200,
        body: bytes = b'{"ok":true}',
        content_type: str = "application/json",
    ):
        self.requests: list[dict] = []
        self.status = status
        self.body = body
        self.content_type = content_type

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "content": request.content,
            }
        )
        return httpx.Response(
            self.status,
            content=self.body,
            headers={"content-type": self.content_type},
            request=request,
        )


def make_client(upstream: FakeUpstream, tokens=None, **kw):
    """Build a TestClient for the edge with a fake upstream.

    The edge's upstream httpx client is injected and pointed at the FakeUpstream
    via MockTransport, so forwarding is tested deterministically without a live
    core.
    """
    settings = make_settings(static_tokens=tokens or ["tok-a"], **kw)
    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream.handler),
        timeout=httpx.Timeout(5.0),
    )
    app = EdgeApp(settings, upstream_client=upstream_client).asgi()
    return TestClient(app)


def make_client_for_refuse(tokens=("tok-a",)):
    """Build a client whose upstream always refuses connections (MockTransport raises)."""
    settings = make_settings(static_tokens=list(tokens))

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(refuse),
        timeout=httpx.Timeout(1.0),
    )
    app = EdgeApp(settings, upstream_client=upstream_client).asgi()
    return TestClient(app)


class EdgeScaffoldTests(unittest.TestCase):
    def setUp(self):
        self._logs: list[str] = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self._logs.append(record.getMessage())
        logger = logging.getLogger("youtube_mcp_edge")
        logger.handlers = []
        logger.addHandler(self._handler)
        logger.setLevel(logging.INFO)

    def tearDown(self):
        logging.getLogger("youtube_mcp_edge").handlers = []

    def _logs_joined(self) -> str:
        return "\n".join(self._logs)

    # ---- health -----------------------------------------------------------

    def test_healthz(self):
        upstream = FakeUpstream()
        client = make_client(upstream)
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok", "service": "youtube-mcp-edge"})
        # Health must not be consumed by auth / upstream.
        self.assertEqual(upstream.requests, [])

    def test_healthz_does_not_need_auth(self):
        upstream = FakeUpstream()
        client = make_client(upstream)
        # No Authorization header; still 200.
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)

    # ---- static auth on /mcp ---------------------------------------------

    def test_missing_token_401(self):
        upstream = FakeUpstream()
        client = make_client(upstream)
        resp = client.post(
            "/mcp", content=b"{}", headers={"content-type": "application/json"}
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn("WWW-Authenticate", resp.headers)
        self.assertEqual(upstream.requests, [])  # never forwarded

    def test_invalid_token_401(self):
        upstream = FakeUpstream()
        client = make_client(upstream)
        resp = client.post(
            "/mcp",
            content=b"{}",
            headers={
                "content-type": "application/json",
                "authorization": "Bearer not-the-token",
            },
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(upstream.requests, [])

    def test_valid_token_forwarded(self):
        upstream = FakeUpstream()
        client = make_client(upstream)
        resp = client.post(
            "/mcp",
            content=b'{"jsonrpc":"2.0","method":"tools/list","id":1}',
            headers={
                "content-type": "application/json",
                "authorization": "Bearer tok-a",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(upstream.requests), 1)
        req = upstream.requests[0]
        self.assertEqual(req["method"], "POST")
        self.assertIn("tools/list", req["content"].decode())

    def test_valid_token_not_forwarded_upstream(self):
        # The credential must NOT be forwarded to the private core.
        upstream = FakeUpstream()
        client = make_client(upstream)
        client.post(
            "/mcp",
            content=b"{}",
            headers={"content-type": "application/json", "authorization": "Bearer tok-a"},
        )
        self.assertEqual(len(upstream.requests), 1)
        forwarded_auth = upstream.requests[0]["headers"].get("authorization")
        self.assertIsNone(forwarded_auth)

    def test_prefers_exact_token_match_case_sensitive(self):
        upstream = FakeUpstream()
        client = make_client(upstream)
        # "tok-a" vs "TOK-A" differ.
        resp = client.post(
            "/mcp",
            content=b"{}",
            headers={
                "content-type": "application/json",
                "authorization": "Bearer TOK-A",
            },
        )
        self.assertEqual(resp.status_code, 401)

    def test_multiple_tokens_any_valid(self):
        upstream = FakeUpstream()
        client = make_client(upstream, tokens=["t1", "t2", "t3"])
        for token in ("t1", "t2", "t3"):
            resp = client.post(
                "/mcp",
                content=b"{}",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                },
            )
            self.assertEqual(resp.status_code, 200, token)
        # An out-of-set token is rejected.
        resp = client.post(
            "/mcp",
            content=b"{}",
            headers={
                "content-type": "application/json",
                "authorization": "Bearer t4",
            },
        )
        self.assertEqual(resp.status_code, 401)

    def test_rotation_scenario(self):
        # Old token works, then config rotates: add new, remove old.
        up = FakeUpstream()
        c1 = make_client(up, tokens=["legacy-token"])
        r1 = c1.post(
            "/mcp", content=b"{}", headers={"authorization": "Bearer legacy-token"}
        )
        self.assertEqual(r1.status_code, 200)
        c2 = make_client(up, tokens=["new-token"])
        r_old = c2.post(
            "/mcp", content=b"{}", headers={"authorization": "Bearer legacy-token"}
        )
        self.assertEqual(r_old.status_code, 401)
        r_new = c2.post(
            "/mcp", content=b"{}", headers={"authorization": "Bearer new-token"}
        )
        self.assertEqual(r_new.status_code, 200)

    def test_malformed_authorization_401(self):
        upstream = FakeUpstream()
        client = make_client(upstream)
        for bad in (
            "Bearer",                 # no space/token
            "Bearer   ",              # token only whitespace
            "Basic abc",              # wrong scheme
            "tok-a",                  # no scheme
            "Bearer a b c",           # multipart token
            "Bearer\t",               # tab then nothing
            "Bearer tok-a extra",     # extra after token
        ):
            resp = client.post(
                "/mcp",
                content=b"{}",
                headers={
                    "content-type": "application/json",
                    "authorization": bad,
                },
            )
            self.assertEqual(
                resp.status_code, 401,
                msg=f"header {bad!r} should be rejected as 401",
            )
            self.assertEqual(upstream.requests, [])

    # ---- forwarding / upstream behavior -----------------------------------

    def test_upstream_unreachable_502(self):
        client = make_client_for_refuse()
        resp = client.post(
            "/mcp", content=b"{}", headers={"authorization": "Bearer tok-a"}
        )
        self.assertEqual(resp.status_code, 502)

    def test_upstream_error_status_passthrough(self):
        upstream = FakeUpstream(status=422, body=b'{"ok":false}')
        client = make_client(upstream)
        resp = client.post(
            "/mcp", content=b"{}", headers={"authorization": "Bearer tok-a"}
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.content, b'{"ok":false}')

    def test_streaming_content_type_preserved(self):
        # SSE responses must keep their content type.
        upstream = FakeUpstream(content_type="text/event-stream", body=b"data: x\n\n")
        client = make_client(upstream)
        resp = client.post(
            "/mcp", content=b"{}", headers={"authorization": "Bearer tok-a"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers["content-type"].split(";")[0], "text/event-stream"
        )
        self.assertIn("data: x", resp.text)

    # ---- request-size protection ------------------------------------------

    def test_request_size_rejected(self):
        upstream = FakeUpstream()
        client = make_client(upstream, max_request_bytes=100)
        big = b"x" * 200
        resp = client.post(
            "/mcp", content=big, headers={"authorization": "Bearer tok-a"}
        )
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(upstream.requests, [])

    def test_request_size_under_limit_ok(self):
        upstream = FakeUpstream()
        client = make_client(upstream, max_request_bytes=10_000)
        body = b'{"hello":"world"}'
        resp = client.post(
            "/mcp", content=body, headers={"authorization": "Bearer tok-a"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(upstream.requests[0]["content"], body)

    # ---- credential never logged / leaked ---------------------------------

    def test_credentials_never_in_logs(self):
        upstream = FakeUpstream()
        client = make_client(upstream, tokens=["super-secret-abc"])
        client.post(
            "/mcp",
            content=b"{}",
            headers={"authorization": "Bearer super-secret-abc"},
        )
        client.post(
            "/mcp",
            content=b"{}",
            headers={"authorization": "Bearer wrong-secret-xyz"},
        )
        joined = self._logs_joined()
        self.assertNotIn("super-secret-abc", joined)
        self.assertNotIn("wrong-secret-xyz", joined)
        self.assertNotIn("authorization", joined.lower())


class EdgeCliSmokeTests(unittest.TestCase):
    """Small settings-level checks (no server started)."""

    def test_auth_enabled_with_no_tokens_fails_closed(self):
        settings = make_settings(static_auth_enabled=True, static_tokens=[])
        with self.assertRaises(ValueError):
            EdgeApp(settings)

    def test_unrecognized_auth_env_value_fails_closed(self):
        # A typo/garbage value for EDGE_STATIC_AUTH_ENABLED must raise, not
        # silently disable authentication (security review MEDIUM-2).
        import os
        import youtube_mcp_edge.settings as settings_mod

        old = os.environ.get("EDGE_STATIC_AUTH_ENABLED")
        os.environ["EDGE_STATIC_AUTH_ENABLED"] = "tru"
        try:
            with self.assertRaises(ValueError):
                settings_mod.EdgeSettings.from_env()
        finally:
            if old is None:
                os.environ.pop("EDGE_STATIC_AUTH_ENABLED", None)
            else:
                os.environ["EDGE_STATIC_AUTH_ENABLED"] = old

    def test_bad_upstream_url_fails_closed(self):
        # Non-http(s) or relative upstream URLs must be rejected at settings
        # load (security review LOW-2).
        import os
        import youtube_mcp_edge.settings as settings_mod

        old = os.environ.get("EDGE_MCP_UPSTREAM_URL")
        for bad in ("ftp://youtube-mcp:8765/mcp", "not-a-url", ""):
            os.environ["EDGE_MCP_UPSTREAM_URL"] = bad
            try:
                with self.assertRaises(ValueError):
                    settings_mod.EdgeSettings.from_env()
            finally:
                if old is None:
                    os.environ.pop("EDGE_MCP_UPSTREAM_URL", None)
                else:
                    os.environ["EDGE_MCP_UPSTREAM_URL"] = old

    def test_settings_from_env_parses_edge_vars(self):
        import os
        import youtube_mcp_edge.settings as settings_mod

        env = {
            "EDGE_HOST": "0.0.0.0",
            "EDGE_PORT": "9999",
            "EDGE_MCP_UPSTREAM_URL": "http://youtube-mcp:8765/mcp",
            "EDGE_STATIC_AUTH_ENABLED": "true",
            "EDGE_STATIC_TOKENS": "tok1\ntok2",
            "EDGE_MAX_REQUEST_BYTES": "4096",
            "EDGE_UPSTREAM_TIMEOUT_SECONDS": "30",
        }
        saved = {k: os.environ.get(k) for k in env}
        for k, v in env.items():
            os.environ[k] = v
        try:
            s = settings_mod.EdgeSettings.from_env()
            self.assertEqual(s.host, "0.0.0.0")
            self.assertEqual(s.port, 9999)
            self.assertEqual(s.upstream_url, "http://youtube-mcp:8765/mcp")
            self.assertTrue(s.static_auth_enabled)
            self.assertEqual(s.static_tokens, ("tok1", "tok2"))
            self.assertEqual(s.max_request_bytes, 4096)
            self.assertEqual(s.upstream_timeout_seconds, 30.0)
        finally:
            for k in env:
                prior = saved.get(k) or ""
                if prior:
                    os.environ[k] = prior
                else:
                    os.environ.pop(k, None)

    def test_auth_disabled_allows(self):
        # With static auth disabled and no tokens, the edge should forward
        # without a credential (not fail closed).
        upstream = FakeUpstream()
        settings = make_settings(static_auth_enabled=False, static_tokens=[])
        upstream_client = httpx.AsyncClient(
            transport=httpx.MockTransport(upstream.handler),
            timeout=httpx.Timeout(5.0),
        )
        app = EdgeApp(settings, upstream_client=upstream_client).asgi()
        client = TestClient(app)
        resp = client.post(
            "/mcp", content=b"{}", headers={"content-type": "application/json"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(upstream.requests), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)