"""Compose topology guard for the public MCP edge (Stage 0/1).

Because Docker is not always available in CI/dev, this file verifies the
networking contract of `compose.yaml` structurally:

- The edge process binds `0.0.0.0` INSIDE its container (so it is reachable
  through Docker NAT to the container's network interface), while Compose
  publishes the host-side port only on `127.0.0.1` (local testing).
- The MCP core is likewise published only on host loopback, never publicly.

Run with:  uv run python -m unittest tests.test_compose_topology -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
COMPOSE = REPO / "compose.yaml"


class ComposeTopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(COMPOSE.read_text())

    def test_core_is_published_only_on_host_loopback(self):
        ports = self.doc["services"]["youtube-mcp"]["ports"]
        self.assertEqual(ports, ["127.0.0.1:8765:8765"])

    def test_edge_publishes_only_on_host_loopback(self):
        ports = self.doc["services"]["youtube-mcp-edge"]["ports"]
        # container:8766 -> host 127.0.0.1:8766 (loopback only, not public).
        self.assertEqual(ports, ["127.0.0.1:8766:8766"])

    def test_edge_binds_all_interfaces_inside_container(self):
        # The process must listen on the container's network interface, not just
        # container-local loopback, or it would be unreachable through the
        # Compose-published host port (Docker NAT targets the container's eth0).
        env = self.doc["services"]["youtube-mcp-edge"]["environment"]
        self.assertEqual(env["EDGE_HOST"], "0.0.0.0")

    def test_edge_container_runs_the_edge_entrypoint(self):
        # The image's default ENTRYPOINT is the CORE (youtube-current-data-mcp).
        # The edge service must override it or the container would run the core,
        # not the edge. This is the single most important Compose guard.
        self.assertEqual(
            self.doc["services"]["youtube-mcp-edge"]["entrypoint"],
            ["youtube-mcp-edge"],
        )
        self.assertNotIn(
            "youtube-current-data-mcp",
            self.doc["services"]["youtube-mcp-edge"]["entrypoint"][0],
        )

    def test_edge_healthcheck_targets_edge_healthz(self):
        # The image HEALTHCHECK probes the CORE /healthz on 8765; the edge serves
        # its own on 8766 and must override the healthcheck so `depends_on`
        # service_healthy reflects the edge being ready, not the core.
        hc = self.doc["services"]["youtube-mcp-edge"]["healthcheck"]
        self.assertIn("/healthz", hc["test"][-1])
        self.assertIn("127.0.0.1:8766", hc["test"][-1])

    def test_edge_is_opt_in_via_profile(self):
        # The edge must not be part of the default `docker compose up`; existing
        # core-only/tunnel-only behavior is unchanged unless --profile edge is used.
        self.assertEqual(self.doc["services"]["youtube-mcp-edge"]["profiles"], ["edge"])

    def test_edge_upstream_points_at_private_core_service_name(self):
        env = self.doc["services"]["youtube-mcp-edge"]["environment"]
        self.assertEqual(env["EDGE_MCP_UPSTREAM_URL"], "http://youtube-mcp:8765/mcp")

    def test_core_not_published_on_all_interfaces(self):
        # Defence in depth: no `0.0.0.0:8765` publish anywhere in compose.
        for svc in ("youtube-mcp", "youtube-mcp-edge", "youtube-mcp-edge-public"):
            for port in self.doc["services"][svc]["ports"]:
                self.assertNotIn("0.0.0.0", port)


class StageTwoComposeTests(unittest.TestCase):
    """Public HTTPS static-auth edge (Stage 2): Compose topology guard."""

    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(COMPOSE.read_text())
        cls.pub = cls.doc["services"]["youtube-mcp-edge-public"]

    def test_public_edge_service_exists_and_opt_in(self):
        self.assertIn("youtube-mcp-edge-public", self.doc["services"])
        self.assertEqual(self.pub["profiles"], ["edge-public"])

    def test_public_edge_runs_the_edge_entrypoint(self):
        self.assertEqual(self.pub["entrypoint"], ["youtube-mcp-edge"])

    def test_public_edge_binds_all_interfaces_inside_container(self):
        self.assertEqual(self.pub["environment"]["EDGE_HOST"], "0.0.0.0")

    def test_public_edge_publishes_only_host_loopback(self):
        # Host publish must be loopback-gated; never a bare public port.
        for spec in self.pub["ports"]:
            self.assertRegex(spec, r"^127\.0\.0\.1:")

    def test_public_edge_https_port_configurable(self):
        # Port string must carry the configurable public HTTPS port placeholder.
        self.assertTrue(
            any("EDGE_PUBLIC_HTTPS_PORT" in p for p in self.pub["ports"]),
            self.pub["ports"],
        )

    def test_public_edge_tls_cert_key_in_env(self):
        env = self.pub["environment"]
        self.assertIn("EDGE_TLS_CERT_FILE", env)
        self.assertIn("EDGE_TLS_KEY_FILE", env)

    def test_public_edge_mounts_cert_dir_read_only(self):
        mounts = self.pub.get("volumes") or []
        self.assertTrue(any(":ro" in m and "/certs" in m for m in mounts), mounts)

    def test_public_edge_static_tokens_required_fail_closed(self):
        # The public profile must not pin a default token; it reads from env so
        # an operator who omits EDGE_STATIC_TOKENS gets a fail-closed startup.
        env = self.pub["environment"]
        self.assertEqual(env["EDGE_STATIC_TOKENS"], "${EDGE_STATIC_TOKENS:-}")
        self.assertEqual(env["EDGE_STATIC_AUTH_ENABLED"], "${EDGE_STATIC_AUTH_ENABLED:-true}")

    def test_public_edge_upstream_points_at_private_core(self):
        self.assertEqual(
            self.pub["environment"]["EDGE_MCP_UPSTREAM_URL"],
            "http://youtube-mcp:8765/mcp",
        )

    def test_public_edge_healthcheck_is_https_unverified(self):
        hc = self.pub["healthcheck"]
        probe = hc["test"][-1]
        self.assertIn("https://", probe)
        self.assertIn("_create_unverified_context", probe)

    def test_core_publish_not_public_in_public_profile(self):
        # Even when the public profile is enabled, the CORE publishes only
        # loopback; the public edge is the sole intended exposure point.
        core_ports = self.doc["services"]["youtube-mcp"]["ports"]
        self.assertEqual(core_ports, ["127.0.0.1:8765:8765"])

    def test_default_compose_still_core_only_plus_local_edge(self):
        # Profiles mean `docker compose up` (no profile) reproduces the original
        # core-only intent; the edge and public edge are opt-in.
        self.assertNotIn("profiles", self.doc["services"]["youtube-mcp"])
        for svc in ("youtube-mcp-edge", "youtube-mcp-edge-public"):
            self.assertTrue(self.doc["services"][svc].get("profiles"))


if __name__ == "__main__":
    unittest.main(verbosity=2)