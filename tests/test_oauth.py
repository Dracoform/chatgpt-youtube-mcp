"""Stage 3: OAuth Resource Server tests — JWT/JWKS validation, dispatch,
metadata/discovery, WWW-Authenticate, and coexistence modes.

Uses a fake Authorization Server (issuer metadata + JWKS) served over an httpx
MockTransport so no network is needed and tests are deterministic.
"""

from __future__ import annotations

import base64
import json
import time
import unittest

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from starlette.testclient import TestClient

from youtube_mcp_edge.app import EdgeApp
from youtube_mcp_edge.settings import EdgeSettings, make_settings

ISSUER = "https://as.example.org/realms/master"
RESOURCE = "https://mcp.example.org/mcp"
AUDIENCE = RESOURCE


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class FakeAS:
    """A tiny RSA-signed-JWT Authorization Server over httpx.MockTransport."""

    def __init__(self, *, key_override: rsa.RSAPrivateKey | None = None,
                 rotation: bool = False):
        self._key = key_override or rsa.generate_private_key(
            public_exponent=65537, key_size=2048)
        self._kid = "test-key-1"
        self._rotated_kid = "test-key-1-rotated"
        self._rotated: rsa.RSAPrivateKey | None = None
        self._use_rotation = rotation

    def signing_key(self) -> rsa.RSAPrivateKey:
        return self._key

    def _public_jwk(self, key: rsa.RSAPrivateKey, kid: str) -> dict:
        pub = key.public_key().public_numbers()
        return {
            "kty": "RSA",
            "kid": kid,
            "use": "sig",
            "alg": "RS256",
            "n": _b64(pub.n.to_bytes((pub.n.bit_length() + 7) // 8, "big")),
            "e": _b64(pub.e.to_bytes((pub.e.bit_length() + 7) // 8, "big")),
        }

    def jwks_doc(self) -> dict:
        keys = [self._public_jwk(self._key, self._kid)]
        if self._use_rotation:
            self._rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            keys.append(self._public_jwk(self._rotated, self._rotated_kid))
            keys[0], keys[-1] = keys[-1], keys[0]  # rotated first
        return {"keys": keys}

    def issuer_doc(self) -> dict:
        return {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
            "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
            "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
            "registration_endpoint": f"{ISSUER}/clients-registrations/openid-connect",
        }

    def make_token(self, *, issuer=ISSUER, audience=AUDIENCE, scope="youtube-mcp",
                   exp_delta=3600, nbf_delta=-30, kid=None, alg="RS256",
                   sign_key=None, claims_extra=None) -> str:
        key = sign_key or self._key
        kid = kid or self._kid
        now = time.time()
        header = {"alg": alg, "typ": "JWT", "kid": kid}
        payload = {
            "iss": issuer,
            "aud": audience,
            "exp": now + exp_delta,
            "nbf": now + nbf_delta,
            "iat": now,
            "scope": scope,
        }
        if claims_extra:
            payload.update(claims_extra)
        signing_input = _b64(json.dumps(header, separators=(",", ":")).encode()) + "." + \
            _b64(json.dumps(payload, separators=(",", ":")).encode())
        if alg.startswith("HS") or alg == "none":
            raise ValueError("MockTransport only signs asymmetric here")
        sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        return signing_input + "." + _b64(sig)

    def transport(self) -> httpx.MockTransport:
        jwks = self.jwks_doc()
        issuer_doc = self.issuer_doc()

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/.well-known/oauth-authorization-server") or \
               url.endswith("/.well-known/openid-configuration"):
                return httpx.Response(200, json=issuer_doc)
            if "certs" in url:
                return httpx.Response(200, json=jwks)
            return httpx.Response(404, json={"error": "not_found"})

        return httpx.MockTransport(handler)


def _oauth_settings(**overrides) -> EdgeSettings:
    from youtube_mcp_edge.settings import make_settings as _make

    base = dict(
        upstream_url="http://127.0.0.1:8765/mcp",
        static_auth_enabled=True,
        static_tokens=["ytsk_static_token"],
        oauth_enabled=True,
        oauth_issuer=ISSUER,
        oauth_jwks_url=f"{ISSUER}/protocol/openid-connect/certs",
        oauth_audience=AUDIENCE,
        oauth_required_scope="youtube-mcp",
        oauth_resource_identifier=RESOURCE,
        oauth_scopes_supported=["youtube-mcp"],
    )
    base.update(overrides)
    return _make(**base)  # type: ignore[arg-type]  # kwargs are valid; typing is lossy via dict


class FakeUpstream:
    def __init__(self):
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # Minimal MCP core echo.
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "result": {"ok": True}, "id": 1}
        )


def _client(as_: FakeAS, settings=None, upstream=None):
    upstream = upstream or FakeUpstream()
    settings = settings or _oauth_settings()
    app = EdgeApp(
        settings,
        upstream_client=httpx.AsyncClient(
            transport=httpx.MockTransport(upstream.handler),
            timeout=httpx.Timeout(5.0),
        ),
        oauth_client=httpx.AsyncClient(transport=as_.transport(), timeout=httpx.Timeout(5.0)),
    )
    return TestClient(app.asgi()), upstream


class OAuthDispatchTests(unittest.TestCase):
    """Deterministic auth-domain dispatch: no cross-domain fallback."""

    def setUp(self):
        self.as_ = FakeAS()
        self.settings = _oauth_settings()
        self.client, self.upstream = _client(self.as_, self.settings)

    def _post_mcp(self, token: str | None):
        headers = {"content-type": "application/json",
                   "accept": "application/json, text/event-stream"}
        if token is not None:
            headers["authorization"] = f"Bearer {token}"
        return self.client.post("/mcp", content=b"{}", headers=headers)

    def test_valid_static_token(self):
        resp = self._post_mcp("ytsk_static_token")
        self.assertEqual(resp.status_code, 200)

    def test_valid_oauth_token(self):
        resp = self._post_mcp(self.as_.make_token())
        self.assertEqual(resp.status_code, 200)
        # Credential not forwarded upstream.
        self.assertNotIn("authorization",
                         {k.lower() for k in self.upstream.requests[0].headers})

    def test_invalid_static_token_not_retried_as_oauth(self):
        # A static-namespace token that is NOT in the store must 401 and NEVER
        # reach the JWT path (an invalid static token must not be retried as
        # OAuth).
        resp = self._post_mcp("ytsk_wrong-static")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(self.upstream.requests), 0)  # never forwarded

    def test_invalid_oauth_token_not_retried_as_static(self):
        # A non-static-namespace (OAuth-looking) token that fails JWKS must 401
        # and NEVER be retried against the static store.
        wrong_issuer = self.as_.make_token(issuer="https://evil.example.org/issuer")
        bad_sig = self.as_.make_token(sign_key=rsa.generate_private_key(
            public_exponent=65537, key_size=2048))
        for token in (wrong_issuer, bad_sig):
            resp = self._post_mcp(token)
            self.assertEqual(resp.status_code, 401, token)
        self.assertEqual(len(self.upstream.requests), 0)

    def test_missing_header_advertises_oauth_discovery(self):
        resp = self._post_mcp(None)
        self.assertEqual(resp.status_code, 401)
        ww = resp.headers.get("www-authenticate", "")
        self.assertIn("Bearer", ww)
        self.assertIn("resource_metadata=", ww)
        self.assertIn("oauth-protected-resource", ww)


class OAuthJwtValidationTests(unittest.TestCase):
    """JWT claim/signature validation against the fake AS JWKS."""

    def setUp(self):
        self.as_ = FakeAS()
        self.client, self.upstream = _client(self.as_)

    def _post(self, token):
        return self.client.post(
            "/mcp", content=b"{}",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {token}"})

    def test_valid_token_ok(self):
        self.assertEqual(self._post(self.as_.make_token()).status_code, 200)

    def test_bad_signature_rejected(self):
        bad = self.as_.make_token(sign_key=rsa.generate_private_key(
            public_exponent=65537, key_size=2048))
        self.assertEqual(self._post(bad).status_code, 401)

    def test_wrong_issuer_rejected(self):
        self.assertEqual(
            self._post(self.as_.make_token(issuer="https://other/issuer")).status_code, 401)

    def test_wrong_audience_rejected(self):
        self.assertEqual(
            self._post(self.as_.make_token(audience="https://wrong/mcp")).status_code, 401)

    def test_expired_token_rejected(self):
        self.assertEqual(
            self._post(self.as_.make_token(exp_delta=-100)).status_code, 401)

    def test_not_yet_valid_rejected(self):
        self.assertEqual(
            self._post(self.as_.make_token(nbf_delta=+500)).status_code, 401)

    def test_missing_required_scope_rejected_403(self):
        resp = self._post(self.as_.make_token(scope="other-scope"))
        self.assertEqual(resp.status_code, 403)
        self.assertIn("insufficient_scope", resp.headers.get("www-authenticate", ""))
        self.assertIn("youtube-mcp", resp.headers.get("www-authenticate", ""))

    def test_required_scope_accepted(self):
        self.assertEqual(
            self._post(self.as_.make_token(scope="youtube-mcp other")).status_code, 200)

    def test_no_audience_configured_skips_aud_assert(self):
        settings = _oauth_settings(oauth_audience=None)
        client, upstream = _client(self.as_, settings)
        tok = self.as_.make_token(audience="anything")
        resp = client.post("/mcp", content=b"{}",
                           headers={"content-type": "application/json",
                                    "authorization": f"Bearer {tok}"})
        self.assertEqual(resp.status_code, 200)

    def test_no_audience_configured_still_validates_issuer(self):
        settings = _oauth_settings(oauth_audience=None)
        client, upstream = _client(self.as_, settings)
        tok = self.as_.make_token(issuer="https://evil/issuer", audience="anything")
        resp = client.post("/mcp", content=b"{}",
                           headers={"content-type": "application/json",
                                    "authorization": f"Bearer {tok}"})
        self.assertEqual(resp.status_code, 401)


class OAuthJwtSecurityTests(unittest.TestCase):
    """Algorithm / unsigned / malformed / key-confusion rejection."""

    def setUp(self):
        self.as_ = FakeAS()
        self.client, _ = _client(self.as_)

    def _post(self, token):
        return self.client.post(
            "/mcp", content=b"{}",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {token}"})

    def test_unsigned_alg_none_rejected(self):
        header = {"alg": "none", "typ": "JWT"}
        payload = {"iss": ISSUER, "aud": AUDIENCE, "exp": time.time() + 100,
                   "scope": "youtube-mcp"}
        token = _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(payload).encode()) + "."
        self.assertEqual(self._post(token).status_code, 401)

    def test_hmac_alg_rejected(self):
        # A token claiming HS256 must be rejected by our algorithm allow-list
        # (symmetric algorithm against a remote RSA JWKS = algorithm confusion).
        header = {"alg": "HS256", "typ": "JWT", "kid": self.as_._kid}
        payload = {"iss": ISSUER, "aud": AUDIENCE, "exp": time.time() + 100,
                   "scope": "youtube-mcp"}
        signing_input = _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(payload).encode())
        tok = signing_input + "." + _b64(b"fake-hmac-sig")
        self.assertEqual(self._post(tok).status_code, 401)

    def test_malformed_token_rejected(self):
        self.assertEqual(self._post("not.a.jwt").status_code, 401)
        self.assertEqual(self._post("eyJnotvalid").status_code, 401)

    def test_unknown_kid_refused_then_jwks_refresh(self):
        # kid not in the JWKS -> after a forced refresh still unknown -> 401.
        tok = self.as_.make_token(kid="missing-kid")
        self.assertEqual(self._post(tok).status_code, 401)

    def test_kid_missing_with_multiple_keys_refused(self):
        as_ = FakeAS(rotation=True)
        client, _ = _client(as_)
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {"iss": ISSUER, "aud": AUDIENCE, "exp": time.time() + 100,
                   "scope": "youtube-mcp"}
        tok = _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(payload).encode())
        # sign with the rotated key but omit kid
        sig = as_.signing_key().sign(tok.encode(), padding.PKCS1v15(), hashes.SHA256())
        tok = tok + "." + _b64(sig)
        resp = client.post("/mcp", content=b"{}",
                           headers={"content-type": "application/json",
                                    "authorization": f"Bearer {tok}"})
        self.assertEqual(resp.status_code, 401)

    def test_as_outage_jwks_unreachable_fails_closed(self):
        # Authorization-Server outage: the JWKS endpoint is unreachable. The
        # edge must reject with 401 (never fail-open), and never forward the
        # request upstream.
        as_ = FakeAS()

        def outage_handler(request: httpx.Request) -> httpx.Response:
            if "certs" in str(request.url):
                raise httpx.ConnectError("AS unreachable", request=request)
            # issuer metadata still reachable (or also down — either way 401)
            return httpx.Response(200, json=as_.issuer_doc())

        upstream = FakeUpstream()
        settings = _oauth_settings()
        app = EdgeApp(
            settings,
            upstream_client=httpx.AsyncClient(
                transport=httpx.MockTransport(upstream.handler),
                timeout=httpx.Timeout(5.0),
            ),
            oauth_client=httpx.AsyncClient(
                transport=httpx.MockTransport(outage_handler),
                timeout=httpx.Timeout(5.0),
            ),
        )
        client = TestClient(app.asgi())
        tok = as_.make_token()
        resp = client.post("/mcp", content=b"{}",
                           headers={"content-type": "application/json",
                                    "authorization": f"Bearer {tok}"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(upstream.requests), 0)  # never forwarded
        # fail-closed: a valid token must NOT be accepted during an AS outage
        self.assertNotEqual(resp.status_code, 200)


class OAuthMetadataTests(unittest.TestCase):
    def setUp(self):
        self.as_ = FakeAS()
        self.settings = _oauth_settings()
        self.client, _ = _client(self.as_, self.settings)

    def test_metadata_endpoint_served(self):
        resp = self.client.get("/.well-known/oauth-protected-resource")
        self.assertEqual(resp.status_code, 200)
        doc = resp.json()
        self.assertEqual(doc["resource"], RESOURCE)
        self.assertEqual(doc["authorization_servers"], [ISSUER])
        self.assertIn("youtube-mcp", doc.get("scopes_supported", []))
        self.assertEqual(doc.get("scopes_required"), ["youtube-mcp"])
        self.assertEqual(doc["bearer_methods_supported"], ["header"])

    def test_metadata_404_when_oauth_disabled(self):
        settings = _oauth_settings(oauth_enabled=False, oauth_issuer=None)
        client, _ = _client(self.as_, settings)
        resp = client.get("/.well-known/oauth-protected-resource")
        self.assertEqual(resp.status_code, 404)

    def test_metadata_url_derivation(self):
        # RFC 9728 metadata is served at the origin root (per Phase-1 §7.3,
        # Claude probes /.well-known/oauth-protected-resource on the MCP origin).
        from youtube_mcp_edge.app import EdgeApp as E

        app = E(self.settings, upstream_client=httpx.AsyncClient(
            transport=httpx.MockTransport(FakeUpstream().handler),
            timeout=httpx.Timeout(5.0)),
            oauth_client=httpx.AsyncClient(transport=self.as_.transport(), timeout=httpx.Timeout(5.0)))
        self.assertEqual(
            app._oauth.metadata_url,
            "https://mcp.example.org/.well-known/oauth-protected-resource",
        )


class OAuthCoexistenceTests(unittest.TestCase):
    """Coexistence modes A–G at the dispatch level."""

    def test_static_only_preserves_old_behavior(self):
        # OAuth off: any bearer token validated against static store only.
        as_ = FakeAS()
        s = make_settings(static_auth_enabled=True, static_tokens=["legacy-token"])
        client, upstream = _client(as_, s)
        for ok_tok in ("legacy-token", "ytsk_whatever"):
            # only legacy-token matches the store
            pass
        ok = client.post("/mcp", content=b"{}",
                         headers={"content-type": "application/json",
                                  "authorization": "Bearer legacy-token"})
        self.assertEqual(ok.status_code, 200)
        bad = client.post("/mcp", content=b"{}",
                          headers={"content-type": "application/json",
                                   "authorization": "Bearer wrong-token"})
        self.assertEqual(bad.status_code, 401)
        # no oauth discovery advertising in static-only mode
        self.assertNotIn("resource_metadata", bad.headers.get("www-authenticate", ""))

    def test_oauth_only_mode(self):
        as_ = FakeAS()
        s = _oauth_settings(static_auth_enabled=False, static_tokens=[])
        client, upstream = _client(as_, s)
        oauth = client.post("/mcp", content=b"{}",
                            headers={"content-type": "application/json",
                                     "authorization": f"Bearer {as_.make_token()}"})
        self.assertEqual(oauth.status_code, 200)
        # A static-namespace token in OAuth-only mode is rejected (never OAuth).
        statik = client.post("/mcp", content=b"{}",
                             headers={"content-type": "application/json",
                                      "authorization": "Bearer ytsk_whatever"})
        self.assertEqual(statik.status_code, 401)

    def test_local_noauth_mode(self):
        as_ = FakeAS()
        s = make_settings(static_auth_enabled=False, static_tokens=[],
                          oauth_enabled=False, oauth_issuer=None)
        client, _ = _client(as_, s)
        resp = client.post("/mcp", content=b"{}",
                           headers={"content-type": "application/json"})
        self.assertEqual(resp.status_code, 200)


class OAuthSettingsTests(unittest.TestCase):
    def test_oauth_fails_closed_without_issuer(self):
        from youtube_mcp_edge.settings import EdgeSettings
        import os

        saved = {k: os.environ.get(k) for k in ("EDGE_OAUTH_ENABLED",
                                                "EDGE_OAUTH_ISSUER",
                                                "EDGE_OAUTH_RESOURCE_IDENTIFIER")}
        os.environ["EDGE_OAUTH_ENABLED"] = "true"
        os.environ.pop("EDGE_OAUTH_ISSUER", None)
        os.environ["EDGE_OAUTH_RESOURCE_IDENTIFIER"] = RESOURCE
        try:
            with self.assertRaises(ValueError):
                EdgeSettings.from_env()
        finally:
            for k, prior in saved.items():
                if prior:
                    os.environ[k] = prior
                else:
                    os.environ.pop(k, None)

    def test_oauth_fails_closed_without_resource_identifier(self):
        from youtube_mcp_edge.settings import EdgeSettings
        import os

        saved = {k: os.environ.get(k) for k in ("EDGE_OAUTH_ENABLED",
                                                "EDGE_OAUTH_ISSUER",
                                                "EDGE_OAUTH_RESOURCE_IDENTIFIER")}
        os.environ["EDGE_OAUTH_ENABLED"] = "true"
        os.environ["EDGE_OAUTH_ISSUER"] = ISSUER
        os.environ.pop("EDGE_OAUTH_RESOURCE_IDENTIFIER", None)
        try:
            with self.assertRaises(ValueError):
                EdgeSettings.from_env()
        finally:
            for k, prior in saved.items():
                if prior:
                    os.environ[k] = prior
                else:
                    os.environ.pop(k, None)

    def test_static_token_prefix_configurable(self):
        s = make_settings(oauth_enabled=True, static_token_prefix="kc_")
        self.assertEqual(s.static_token_prefix, "kc_")


if __name__ == "__main__":
    unittest.main()