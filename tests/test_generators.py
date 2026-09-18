"""Static and executable tests for the capability-oriented generators and the
tunnel wrapper (Stage 4).

The generators are driven NON-interactively through their canonical `--input`
mode and their output is compared byte-for-byte against
generators/canonical_model.py (the single source of truth) for the fixture set
in tests/fixtures/. The detailed three-way (Bash/PowerShell/Web) equivalence and
the PowerShell arm live in tests/test_generator_equivalence.py.

No Docker and no real credentials required.
"""

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SH_GENERATOR = REPO / "generators" / "generate_docker-compose_for_ChatGPT_MCP.sh"
PS1_GENERATOR = REPO / "generators" / "generate_docker-compose_for_ChatGPT_MCP.ps1"
FIXTURES_DIR = REPO / "tests" / "fixtures"
ENTRYPOINT = REPO / "docker" / "openai-mcp-tunnel" / "entrypoint.sh"
MCP_DOCKERFILE = REPO / "Dockerfile"
DOCKERFILE = REPO / "docker" / "openai-mcp-tunnel" / "Dockerfile"
VERSIONS = REPO / "docker" / "openai-mcp-tunnel" / "versions.env"

VALID_ID = "tunnel_0123456789abcdef0123456789abcdef"
# Built at runtime so no complete test secret appears verbatim in this file.
YOUTUBE_KEY = "AIza" + "Sy" + "A1234567890" + "abcdefghijklmnopqrstuv"
assert len(YOUTUBE_KEY) == 39
RUNTIME_KEY = "s" + "k-" + "proj-test1234567890123456789012345678"
assert RUNTIME_KEY.startswith("sk-")


def _fixture_secrets(fixture):
    fx = json.loads((FIXTURES_DIR / f"{fixture}.json").read_text())
    secrets = [fx.get("mcp", {}).get("youtube_api_key", "")]
    tun = fx.get("tunnel") or {}
    secrets.append(tun.get("runtime_api_key", ""))
    secrets += list((fx.get("edge") or {}).get("static_tokens", []))
    return [s for s in secrets if s]


def _redact(text, secrets):
    out = str(text)
    for s in secrets:
        if s:
            out = out.replace(s, "***REDACTED***")
    return out


def _oracle(fixture):
    sys.path.insert(0, str(REPO))
    from generators.canonical_model import render
    return render(str(FIXTURES_DIR / f"{fixture}.json"))


def _run_bash_input(fixture, out_path):
    secrets = _fixture_secrets(fixture)
    try:
        r = subprocess.run(
            ["bash", str(SH_GENERATOR),
             "--input", str(FIXTURES_DIR / f"{fixture}.json"),
             "--output", str(out_path)],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"bash timeout for {fixture}:\n"
            f"{_redact((exc.stdout or '') + (exc.stderr or ''), secrets)}"
        ) from None
    if r.returncode != 0:
        raise AssertionError(
            f"bash failed for {fixture} (rc={r.returncode}): "
            f"{_redact(r.stdout + r.stderr, secrets)}")
    return Path(out_path).read_text(), r


def parse_yaml(path):
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


class TestBashGenerator(unittest.TestCase):
    """The Bash generator, driven via --input, must match the canonical model."""

    CASES = ["tunnel-only", "static-public", "oauth-public", "static-oauth",
             "tunnel-static-oauth", "local"]

    def _gen(self, fixture):
        out = tempfile.mktemp(suffix=".yml")
        text, r = _run_bash_input(fixture, out)
        return out, r, text

    def test_matches_oracle_for_all_fixtures(self):
        for case in self.CASES:
            _, _, text = self._gen(case)
            self.assertEqual(text, _oracle(case), f"Bash mismatch for {case}")

    def test_output_parses_and_has_expected_services(self):
        import yaml
        for case in self.CASES:
            out, _, _ = self._gen(case)
            with open(out) as f:
                doc = yaml.safe_load(f)
            fx = json.loads((FIXTURES_DIR / f"{case}.json").read_text())
            expected = {"youtube-mcp"}
            if "tunnel" in fx["access"]:
                expected.add("openai-tunnel")
            if fx.get("edge", {}).get("enabled"):
                expected.add("youtube-mcp-edge")
            self.assertEqual(set(doc["services"]), expected, case)

    def test_core_never_public(self):
        for case in self.CASES:
            _, _, text = self._gen(case)
            self.assertNotIn("0.0.0.0:8765", text, f"core exposed in {case}")

    def test_local_lists_loopback_core_port(self):
        out, _, _ = self._gen("local")
        self.assertIn("127.0.0.1:8765:8765", Path(out).read_text())

    def test_secrets_never_printed(self):
        for case in self.CASES:
            _, r, text = self._gen(case)
            secrets = _fixture_secrets(case)
            for s in secrets:
                self.assertNotIn(s, r.stdout + r.stderr, f"secret leaked in {case}")
            self.assertNotIn("authorization", r.stdout + r.stderr)

    def test_rejected_canonical_input_is_nonzero(self):
        # a public edge with no auth mode must be rejected (never a public
        # no-auth proxy)
        import tempfile as tf
        bad = json.loads((FIXTURES_DIR / "static-public.json").read_text())
        bad["edge"]["auth_modes"] = []
        p = tf.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(bad, p); p.close()
        r = subprocess.run(
            ["bash", str(SH_GENERATOR), "--input", p.name, "--output", "/tmp/x.yml"],
            capture_output=True, text=True, timeout=30)
        os.unlink(p.name)
        self.assertNotEqual(r.returncode, 0, "must reject no-auth public edge")

    def test_output_file_restrictive_permissions(self):
        out, _, _ = self._gen("tunnel-only")
        mode = stat.S_IMODE(os.stat(out).st_mode)
        self.assertEqual(mode & 0o077, 0, "output must not be group/world readable")

    def test_generator_source_ascii(self):
        self.assertTrue(SH_GENERATOR.read_text(errors="replace").isascii())


class TestPowerShellGeneratorStatic(unittest.TestCase):
    """Static checks on the capability-oriented PowerShell generator."""

    def setUp(self):
        self.text = PS1_GENERATOR.read_text()

    def test_delegates_to_canonical_model(self):
        self.assertIn("generators.canonical_model", self.text)
        self.assertIn("-InputPath", self.text)

    def test_capability_model_present(self):
        for needle in ("local", "tunnel", "static", "oauth"):
            self.assertIn(needle, self.text)
        self.assertIn("ACCESS", self.text)

    def test_core_never_public_note_present(self):
        self.assertIn("NIE oeffentlich publiziert", self.text)

    def test_masked_confirmation_present(self):
        self.assertIn("Read-SecretText", self.text)
        self.assertIn("Get-SecretMask", self.text)
        self.assertIn("No YouTube API key was entered.", self.text)

    def test_ascii_only_source(self):
        self.assertTrue(self.text.isascii(), "ps1 must remain ASCII-only")


class TestCanonicalModel(unittest.TestCase):
    """Validation of the shared canonical model (single source of truth)."""

    def test_all_fixtures_validate(self):
        import sys
        sys.path.insert(0, str(REPO))
        from generators.canonical_model import validate
        for fixture in sorted(os.listdir(FIXTURES_DIR)):
            if not fixture.endswith(".json"):
                continue
            model = json.loads((FIXTURES_DIR / fixture).read_text())
            validate(model)  # must not raise
        self.assertTrue(True)

    def test_rejected_combinations(self):
        import sys
        sys.path.insert(0, str(REPO))
        from generators.canonical_model import validate
        base = {
            "mcp": {"tag": "latest"},
            "access": ["static"],
            "edge": {"auth_modes": ["static"], "tls": "ingress",
                     "static_tokens": ["ytsk_ok"], "oauth": {}},
        }
        cases = [
            {"access": ["static"], "edge": {"auth_modes": []}},            # no-auth public edge
            {"access": ["oauth"],
             "edge": {"auth_modes": ["oauth"], "oauth": {"issuer": "x"}}},  # oauth missing resource
            {"access": ["static"],
             "edge": {"auth_modes": ["static"], "tls": "edge",
                      "cert_file": "/c", "key_file": "",                   # half TLS
                      "static_tokens": ["ytsk_ok"], "oauth": {}}},
            {"access": ["static", "oauth"],
             "edge": {"auth_modes": ["static", "oauth"], "tls": "ingress",
                      "static_tokens": ["noprefix"],                       # namespace ambiguity
                      "oauth": {"issuer": "https://as/r",
                                "resource": "https://m/mcp"}}},
        ]
        for c in cases:
            with self.assertRaises(ValueError, msg=c):
                validate(c)


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

    def test_healthcheck_python_command_is_stdlib_and_targets_healthz(self):
        dockerfile = MCP_DOCKERFILE.read_text()
        m = re.search(r"HEALTHCHECK[^\n]*\n\s*CMD \[(.*?)\]", dockerfile, re.S)
        self.assertIsNotNone(m, "Dockerfile must define a HEALTHCHECK CMD")
        cmd = json.loads("[" + m.group(1) + "]")
        self.assertEqual(cmd[0], "python3")
        code = cmd[2]
        self.assertIn("/healthz", code)
        self.assertIn("urllib", code)
        self.assertNotIn("apt-get", dockerfile)
        self.assertNotIn("RUN apt", dockerfile)
        for line in dockerfile.splitlines():
            if line.strip().startswith("#"):
                continue
            self.assertNotIn("curl", line)
            self.assertNotIn("wget", line)

    def test_healthcheck_detects_delayed_listener(self):
        """Deterministic delayed-readiness test (same as before)."""
        dockerfile = MCP_DOCKERFILE.read_text()
        m = re.search(r"HEALTHCHECK[^\n]*\n\s*CMD \[(.*?)\]", dockerfile, re.S)
        cmd = json.loads("[" + m.group(1) + "]")
        script = (
            "import socket, subprocess, sys, time, threading\n"
            "def up():\n"
            "    s = socket.socket()\n"
            "    try: s.connect(('127.0.0.1', 8765)); return True\n"
            "    except OSError: return False\n"
            "    finally: s.close()\n"
            "self_fail = subprocess.run(cmd, capture_output=True)\n"
            "if self_fail.returncode == 0:\n"
            "    print('FAIL: healthcheck passed with no listener'); sys.exit(1)\n"
            "import http.server\n"
            "class H(http.server.BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        body = b'{\"status\": \"ok\"}'\n"
            "        self.send_response(200)\n"
            "        self.send_header('Content-Type', 'application/json')\n"
            "        self.send_header('Content-Length', str(len(body)))\n"
            "        self.end_headers()\n"
            "        self.wfile.write(body)\n"
            "    def log_message(self, *a): pass\n"
            "srv = http.server.HTTPServer(('127.0.0.1', 8765), H)\n"
            "t = threading.Thread(target=srv.serve_forever, daemon=True)\n"
            "t.start()\n"
            "ok = subprocess.run(cmd, capture_output=True)\n"
            "srv.shutdown()\n"
            "sys.exit(ok.returncode)\n"
        )
        harness = "import json, subprocess, sys\ncmd = " + repr(cmd) + "\n" + script
        out = tempfile.NamedTemporaryFile('w', suffix='.py', delete=False)
        out.write(harness)
        out.close()
        try:
            r = subprocess.run(["python3", out.name], capture_output=True,
                               text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        finally:
            os.unlink(out.name)

    def test_entrypoint_uses_official_name_with_legacy_alias(self):
        text = ENTRYPOINT.read_text()
        self.assertIn("CONTROL_PLANE_TUNNEL_ID", text)
        self.assertIn("OPENAI_TUNNEL_ID", text)
        self.assertIn("EX_CONFIG=78", text)
        self.assertIn("exec", text)
        self.assertNotIn("su-exec", text)


if __name__ == "__main__":
    unittest.main()
