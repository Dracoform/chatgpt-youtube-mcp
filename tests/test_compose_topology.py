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
        for svc in ("youtube-mcp", "youtube-mcp-edge"):
            for port in self.doc["services"][svc]["ports"]:
                self.assertNotIn("0.0.0.0", port)


if __name__ == "__main__":
    unittest.main(verbosity=2)