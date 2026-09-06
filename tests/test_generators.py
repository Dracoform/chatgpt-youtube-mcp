"""Static tests for the Portainer stack generators and the tunnel wrapper.

No Docker, no PowerShell, no network, no real credentials required.
Executable PowerShell equivalence tests run in CI (ubuntu runner has pwsh).
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SH_GENERATOR = REPO / "generators" / "generate_docker-compose_for_ChatGPT_MCP.sh"
PS1_GENERATOR = REPO / "generators" / "generate_docker-compose_for_ChatGPT_MCP.ps1"
ENTRYPOINT = REPO / "docker" / "openai-mcp-tunnel" / "entrypoint.sh"
DOCKERFILE = REPO / "docker" / "openai-mcp-tunnel" / "Dockerfile"
VERSIONS = REPO / "docker" / "openai-mcp-tunnel" / "versions.env"

VALID_ID = "tunnel_0123456789abcdef0123456789abcdef"


def run_sh_generator(answers, out_path):
    inp = "\n".join(answers) + "\n"
    return subprocess.run(
        [str(SH_GENERATOR), "--output", str(out_path)],
        input=inp, capture_output=True, text=True,
    )


def parse_yaml(path):
    import yaml
    return yaml.safe_load(Path(path).read_text())


class TestBashGenerator(unittest.TestCase):
    """Executable tests against the Bash generator (bash is available)."""

    def generate(self, proxy_answer=""):
        out = tempfile.mktemp(suffix=".yml")
        answers = [
            "",            # mcp_tag -> default
            "",            # tunnel_tag -> default
            "",            # youtube api key
            "ja",          # enable yt-dlp
            "JA",          # consent
            "",            # languages
            "",            # max chars
            VALID_ID,      # tunnel id
            "sk-test-key-12345",  # runtime key (hidden)
            proxy_answer,  # proxy
        ]
        r = run_sh_generator(answers, out)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, r

    def test_fixed_image_names(self):
        out, _ = self.generate()
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["youtube-mcp"]["image"],
            "ghcr.io/dracoform/chatgpt-youtube-mcp:latest",
        )
        self.assertEqual(
            d["services"]["openai-tunnel"]["image"],
            "ghcr.io/dracoform/openai-mcp-tunnel:0.1.0",
        )

    def test_official_tunnel_id_variable_only(self):
        out, _ = self.generate()
        env = parse_yaml(out)["services"]["openai-tunnel"]["environment"]
        self.assertIn("CONTROL_PLANE_TUNNEL_ID", env)
        self.assertNotIn("OPENAI_TUNNEL_ID", env)
        self.assertEqual(env["CONTROL_PLANE_TUNNEL_ID"], VALID_ID)

    def test_no_tunnel_config_volume(self):
        out, _ = self.generate()
        d = parse_yaml(out)
        self.assertNotIn("volumes", d)
        self.assertNotIn("volumes", d["services"]["openai-tunnel"])
        self.assertNotIn("tunnel-config", Path(out).read_text())

    def test_no_host_published_ports(self):
        out, _ = self.generate()
        d = parse_yaml(out)
        self.assertNotIn("ports", d["services"]["openai-tunnel"])
        self.assertNotIn("ports", d["services"]["youtube-mcp"])

    def test_proxy_behavior(self):
        out, _ = self.generate(proxy_answer="http://proxy:3128")
        env = parse_yaml(out)["services"]["openai-tunnel"]["environment"]
        self.assertEqual(env["HTTPS_PROXY"], "http://proxy:3128")
        self.assertEqual(env["NO_PROXY"], "youtube-mcp,localhost,127.0.0.1")

    def test_no_proxy_keys_when_empty(self):
        out, _ = self.generate(proxy_answer="")
        env = parse_yaml(out)["services"]["openai-tunnel"]["environment"]
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("NO_PROXY", env)

    def test_secrets_not_printed_to_stdout(self):
        out, r = self.generate()
        self.assertNotIn("sk-test-key-12345", r.stdout)
        self.assertNotIn("sk-test-key-12345", r.stderr)

    def test_yaml_quoting_handles_single_quotes(self):
        out = tempfile.mktemp(suffix=".yml")
        answers = ["", "", "", "nein", "", "", VALID_ID, "sk-it's-quoted", ""]
        r = run_sh_generator(answers, out)
        self.assertEqual(r.returncode, 0, r.stderr)
        env = parse_yaml(out)["services"]["openai-tunnel"]["environment"]
        self.assertEqual(env["CONTROL_PLANE_API_KEY"], "sk-it's-quoted")

    def test_expected_security_options(self):
        out, _ = self.generate()
        tun = parse_yaml(out)["services"]["openai-tunnel"]
        self.assertTrue(tun["read_only"])
        self.assertEqual(tun["tmpfs"], ["/tmp:size=16m"])
        self.assertEqual(tun["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", tun["security_opt"])
        self.assertEqual(str(tun["stop_grace_period"]), "30s")
        self.assertEqual(tun["depends_on"], ["youtube-mcp"])

    def test_ascii_only_output(self):
        _out, r = self.generate()
        self.assertTrue(all(ord(c) < 128 for c in r.stdout),
                        "generator output must stay ASCII-only (mojibake guard)")

    def test_generated_output_parses_as_yaml(self):
        import yaml  # noqa: F401
        out, _ = self.generate()
        self.assertIsInstance(parse_yaml(out), dict)

    def test_output_file_has_restrictive_permissions(self):
        out, _ = self.generate()
        mode = stat.S_IMODE(os.stat(out).st_mode)
        self.assertEqual(mode & 0o077, 0, "output must not be group/world readable")


import stat  # noqa: E402  (used above)


class TestPowerShellGeneratorStatic(unittest.TestCase):
    """Static checks. Executable equivalence runs in CI (pwsh on ubuntu)."""

    def setUp(self):
        self.text = PS1_GENERATOR.read_text()

    def test_fixed_image_bases(self):
        self.assertIn("ghcr.io/dracoform/chatgpt-youtube-mcp", self.text)
        self.assertIn("ghcr.io/dracoform/openai-mcp-tunnel", self.text)
        self.assertNotIn("registryNamespace", self.text)

    def test_official_tunnel_id_variable_only(self):
        self.assertIn("CONTROL_PLANE_TUNNEL_ID", self.text)
        self.assertNotIn("OPENAI_TUNNEL_ID", self.text)

    def test_no_tunnel_config_volume(self):
        self.assertNotIn("tunnel-config", self.text)

    def test_no_host_published_ports(self):
        self.assertNotIn('"8080:8080"', self.text)
        self.assertNotIn("ports:", self.text)

    def test_security_options_present(self):
        for needle in ["read_only: true", "/tmp:size=16m", "cap_drop:",
                       "no-new-privileges:true", "stop_grace_period: 30s",
                       "depends_on:", "NO_PROXY: 'youtube-mcp,localhost,127.0.0.1'"]:
            self.assertIn(needle, self.text)

    def test_ascii_only_source(self):
        self.assertTrue(self.text.isascii(), "ps1 must remain ASCII-only")


class TestTunnelWrapperFiles(unittest.TestCase):

    def test_versions_env_pins(self):
        text = VERSIONS.read_text()
        self.assertRegex(text, r"TUNNEL_CLIENT_VERSION=v0\.0\.14")
        self.assertRegex(text, r"TUNNEL_CLIENT_IMAGE_DIGEST=sha256:[0-9a-f]{64}")
        self.assertRegex(text, r"TUNNEL_CLIENT_AMD64_DIGEST=sha256:[0-9a-f]{64}")
        self.assertRegex(text, r"TUNNEL_CLIENT_ARM64_DIGEST=sha256:[0-9a-f]{64}")

    def test_dockerfile_pins_digest_not_tag(self):
        text = DOCKERFILE.read_text()
        self.assertRegex(text, r"FROM \$\{BASE_IMAGE\}")
        self.assertRegex(
            text, r"BASE_IMAGE=ghcr\.io/openai/tunnel-client@sha256:[0-9a-f]{64}"
        )
        self.assertRegex(text, r"USER 10001:10001")
        self.assertRegex(text, r"COPY --chmod=0755 entrypoint\.sh")
        self.assertIn("/healthz", text)
        self.assertNotIn("su-exec", text)

    def test_entrypoint_executable_bit(self):
        mode = stat.S_IMODE(ENTRYPOINT.stat().st_mode)
        self.assertTrue(mode & 0o111, "entrypoint.sh must be executable")

    def test_entrypoint_uses_official_name_with_legacy_alias(self):
        text = ENTRYPOINT.read_text()
        self.assertIn("CONTROL_PLANE_TUNNEL_ID", text)
        self.assertIn("OPENAI_TUNNEL_ID", text)  # accepted as deprecated alias
        self.assertIn("EX_CONFIG=78", text)
        self.assertIn("exec", text)
        self.assertNotIn("su-exec", text)


if __name__ == "__main__":
    unittest.main()
