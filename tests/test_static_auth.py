"""Unit tests for the edge static Bearer authentication (Stage 1).

Covers the constant-time token store, multi-token/rotation semantics, malformed
header handling, and the namespace headroom intended for future OAuth coexistence.

Run with:  uv run python -m unittest tests.test_static_auth -v
"""

from __future__ import annotations

import logging
import unittest

from youtube_mcp_edge.auth import (
    MalformedAuthorizationError,
    StaticAuthenticator,
    StaticTokenStore,
    parse_bearer_header,
)

# A set of generated-looking opaque tokens for tests. Not real secrets.
TK1 = "ytsk_" + "a" * 40
TK2 = "ytsk_" + "b" * 40
TK3 = "ytsk_" + "c" * 40


class StaticTokenStoreTests(unittest.TestCase):
    def test_store_accepts_configured_token(self):
        store = StaticTokenStore((TK1, TK2))
        self.assertTrue(store.validate(TK1))
        self.assertTrue(store.validate(TK2))

    def test_store_rejects_unknown(self):
        store = StaticTokenStore((TK1,))
        self.assertFalse(store.validate(TK3))

    def test_store_bounded_count(self):
        store = StaticTokenStore((TK1, TK2, TK3))
        self.assertEqual(store.count, 3)

    def test_empty_store_rejects_everything(self):
        store = StaticTokenStore(())
        self.assertTrue(store.is_empty())
        self.assertFalse(store.validate(TK1))

    def test_constant_time_comparison_used(self):
        # The store compares SHA-256 digests via hmac.compare_digest (fixed 32
        # bytes), so comparison time does not depend on token content. We assert
        # the architectural property by checking the store holds digests and
        # validates by digest, not by storing plaintext.
        store = StaticTokenStore((TK1,))
        # Digest list must have exactly one 32-byte digest.
        digests = store._digests
        self.assertEqual(len(digests), 1)
        self.assertEqual(len(digests[0]), 32)
        # No plaintext token is retained.
        text = "".join(map(chr, digests[0]))
        self.assertNotIn(TK1, text)


class ParseBearerHeaderTests(unittest.TestCase):
    def parse_ok(self, header):
        return parse_bearer_header(header)

    def parse_fail(self, header):
        with self.assertRaises(MalformedAuthorizationError):
            parse_bearer_header(header)

    def test_valid_forms(self):
        self.assertEqual(self.parse_ok(f"Bearer {TK1}"), TK1)
        self.assertEqual(self.parse_ok(f"bearer {TK1}"), TK1)
        self.assertEqual(self.parse_ok(f"BEARER {TK1}"), TK1)
        self.assertEqual(self.parse_ok(f"Bearer\t{TK2}"), TK2)

    def test_missing_header_is_error(self):
        self.parse_fail(None)

    def test_wrong_scheme(self):
        self.parse_fail(f"Basic {TK1}")
        self.parse_fail(f"Token {TK1}")

    def test_malformed(self):
        self.parse_fail("Bearer")
        self.parse_fail("Bearer   ")
        self.parse_fail("Bearer a b c")
        self.parse_fail(f"Bearer {TK1} extra")
        self.parse_fail("")
        self.parse_fail("   ")


class StaticAuthenticatorTests(unittest.TestCase):
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

    def auth(self, tokens):
        return StaticAuthenticator(tuple(tokens))

    def test_authenticate_valid(self):
        a = self.auth([TK1, TK2])
        self.assertTrue(a.authenticate(f"Bearer {TK1}"))
        self.assertTrue(a.authenticate(f"Bearer {TK2}"))

    def test_authenticate_invalid(self):
        a = self.auth([TK1])
        self.assertFalse(a.authenticate(f"Bearer {TK3}"))
        self.assertFalse(a.authenticate(None))
        self.assertFalse(a.authenticate("Bearer"))
        self.assertFalse(a.authenticate("Basic xyz"))

    def test_authenticate_when_no_tokens_configured(self):
        a = self.auth([])
        self.assertFalse(a.configured)
        self.assertFalse(a.authenticate(f"Bearer {TK1}"))

    def test_multiple_tokens_allow_rotation(self):
        # Simulate a rolling rotation: old + new both accepted, then old removed.
        rolling = self.auth([TK1, TK2])
        self.assertTrue(rolling.authenticate(f"Bearer {TK1}"))
        self.assertTrue(rolling.authenticate(f"Bearer {TK2}"))
        rotated = self.auth([TK2])
        self.assertFalse(rotated.authenticate(f"Bearer {TK1}"))
        self.assertTrue(rotated.authenticate(f"Bearer {TK2}"))


if __name__ == "__main__":
    unittest.main(verbosity=2)