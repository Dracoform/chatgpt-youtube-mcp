"""Static and executable tests for the Portainer stack generators and the tunnel wrapper.

No Docker and no real credentials required. Executable PowerShell tests run
wherever pwsh is available (CI ubuntu runners); they self-skip elsewhere.
"""

import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SH_GENERATOR = REPO / "generators" / "generate_docker-compose_for_ChatGPT_MCP.sh"
PS1_GENERATOR = REPO / "generators" / "generate_docker-compose_for_ChatGPT_MCP.ps1"
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


def _redact(text, *secrets):
    """Remove test secret values from captured output before diagnostics."""
    for s in secrets:
        if s:
            text = text.replace(s, "***REDACTED***")
    return text


# Every subprocess that drives an interactive generator gets a hard timeout:
# a misaligned answer queue would otherwise hang CI forever. On timeout the
# test fails with captured stdout/stderr (secrets redacted).
GEN_TIMEOUT = 15


def run_sh_generator(answers, out_path):
    inp = "\n".join(answers) + "\n"
    try:
        return subprocess.run(
            [str(SH_GENERATOR), "--output", str(out_path)],
            input=inp, capture_output=True, text=True, timeout=GEN_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        out = _redact((exc.stdout or b"").decode(errors="replace"),
                      YOUTUBE_KEY, RUNTIME_KEY)
        err = _redact((exc.stderr or b"").decode(errors="replace"),
                      YOUTUBE_KEY, RUNTIME_KEY)
        raise AssertionError(
            f"generator timed out after {GEN_TIMEOUT}s (answer queue "
            f"misaligned?)\nstdout: {out}\nstderr: {err}") from None


def parse_yaml(path):
    import yaml
    return yaml.safe_load(Path(path).read_text())


def bash_answers(youtube_key=YOUTUBE_KEY, runtime_key=RUNTIME_KEY,
                 proxy_answer="", confirm=True, youtube_extra=None,
                 runtime_extra=None):
    """Answers for the Bash generator prompt sequence.

    mcp_tag, tunnel_tag, youtube key (+ confirm when non-empty),
    [youtube_extra: answers consumed by the empty-key/confirm/override
    loop right after the key prompt], ytdlp(no), languages, max chars,
    tunnel id, runtime key (+ confirm), [runtime_extra: answers for the
    prefix-warning loop], proxy (+ confirm when non-empty).
    """
    a = ["", ""]                       # mcp_tag, tunnel_tag -> defaults
    a += [youtube_key]                 # key prompt always consumes one line
    if youtube_key and confirm:
        a += ["y"]
    a += list(youtube_extra or [])     # empty-key / override loop answers
    a += ["n"]                         # enable yt-dlp -> no (skips consent)
    a += ["", ""]                      # languages, max chars -> defaults
    a += [VALID_ID]                    # tunnel id (visible, not a secret)
    if runtime_key is not None:
        a += [runtime_key]
        if runtime_key and confirm:
            a += ["y"]
    a += list(runtime_extra or [])
    a += [proxy_answer]
    if proxy_answer and confirm:
        a += ["y"]
    return a


class TestBashGenerator(unittest.TestCase):
    """Executable tests against the Bash generator (bash is available)."""

    def generate(self, youtube_key=YOUTUBE_KEY, runtime_key=RUNTIME_KEY,
                 proxy_answer="", confirm=True, extra_first=None):
        out = tempfile.mktemp(suffix=".yml")
        r = run_sh_generator(
            bash_answers(youtube_key, runtime_key, proxy_answer, confirm,
                         extra_first),
            out,
        )
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

    def test_yaml_quoting_handles_single_quotes(self):
        out = tempfile.mktemp(suffix=".yml")
        r = run_sh_generator(
            bash_answers(runtime_key="s" + "k-it's-quoted"), out)
        self.assertEqual(r.returncode, 0, r.stderr)
        env = parse_yaml(out)["services"]["openai-tunnel"]["environment"]
        self.assertEqual(env["CONTROL_PLANE_API_KEY"], "sk-it's-quoted")

    def test_tunnel_depends_on_service_healthy(self):
        out, _ = self.generate()
        tun = parse_yaml(out)["services"]["openai-tunnel"]
        self.assertEqual(
            tun["depends_on"], {"youtube-mcp": {"condition": "service_healthy"}}
        )

    def test_expected_security_options(self):
        out, _ = self.generate()
        tun = parse_yaml(out)["services"]["openai-tunnel"]
        self.assertTrue(tun["read_only"])
        self.assertEqual(tun["tmpfs"], ["/tmp:size=16m"])
        self.assertEqual(tun["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", tun["security_opt"])
        self.assertEqual(str(tun["stop_grace_period"]), "30s")
        self.assertEqual(
            tun["depends_on"], {"youtube-mcp": {"condition": "service_healthy"}}
        )

    def test_ascii_only_output(self):
        _out, r = self.generate()
        self.assertTrue(all(ord(c) < 128 for c in r.stdout),
                        "generator output must stay ASCII-only (mojibake guard)")

    def test_generated_output_parses_as_yaml(self):
        out, _ = self.generate()
        self.assertIsInstance(parse_yaml(out), dict)

    def test_output_file_has_restrictive_permissions(self):
        out, _ = self.generate()
        mode = stat.S_IMODE(os.stat(out).st_mode)
        self.assertEqual(mode & 0o077, 0, "output must not be group/world readable")


class TestBashSecretConfirmation(TestBashGenerator):
    """Masked-confirmation behavior of the Bash generator."""

    def generate_raw(self, answers):
        out = tempfile.mktemp(suffix=".yml")
        r = run_sh_generator(answers, out)
        return out, r

    def test_mask_length_equals_value_length_and_shows_last_four(self):
        out, r = self.generate()
        err = r.stderr
        # masked line appears between "empfangen" and "Laenge"
        import re
        m = re.search(r"empfangen:\n([X][^\n]*)\nLaenge: (\d+) Zeichen", err)
        self.assertIsNotNone(m, err)
        masked, length = m.group(1), int(m.group(2))
        self.assertEqual(len(masked), length)
        self.assertEqual(length, 39)
        self.assertTrue(masked.startswith("X" * (length - 4)))
        self.assertEqual(masked[-4:], YOUTUBE_KEY[-4:])

    def test_full_secret_absent_from_stdout_and_stderr(self):
        out, r = self.generate()
        self.assertNotIn(YOUTUBE_KEY, r.stdout)
        self.assertNotIn(YOUTUBE_KEY, r.stderr)
        self.assertNotIn(RUNTIME_KEY, r.stdout)
        self.assertNotIn(RUNTIME_KEY, r.stderr)

    def test_generated_yaml_contains_exact_confirmed_values(self):
        out, _ = self.generate()
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["youtube-mcp"]["environment"]["YOUTUBE_API_KEY"],
            YOUTUBE_KEY,
        )
        self.assertEqual(
            d["services"]["openai-tunnel"]["environment"]["CONTROL_PLANE_API_KEY"],
            RUNTIME_KEY,
        )

    def test_empty_optional_youtube_key_requires_confirmation(self):
        # empty key -> explicit continue question, default No -> repeat entry
        out, r = self.generate_raw(bash_answers(youtube_key="", youtube_extra=[
            "n",               # refuse to continue without a key
            YOUTUBE_KEY, "y",  # provide the key on the second attempt
        ]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No YouTube API key was entered.", r.stdout)
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["youtube-mcp"]["environment"]["YOUTUBE_API_KEY"],
            YOUTUBE_KEY,
        )

    def test_continue_without_key_when_confirmed(self):
        out, r = self.generate_raw(bash_answers(youtube_key="", youtube_extra=[
            "y",   # accept continuing without a key
        ]))
        self.assertEqual(r.returncode, 0, r.stderr)
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["youtube-mcp"]["environment"]["YOUTUBE_API_KEY"], "")

    def test_rejected_confirmation_repeats_entry(self):
        out, r = self.generate_raw(bash_answers(youtube_key="", youtube_extra=[
            "n",               # refuse to continue without a key
            YOUTUBE_KEY, "n",  # first entry rejected at the confirmation
            YOUTUBE_KEY, "y",  # second entry accepted
        ]))
        self.assertEqual(r.returncode, 0, r.stderr)
        # the YouTube mask was shown twice (rejected, then accepted)
        self.assertEqual(r.stderr.count("YouTube API key empfangen:"), 2)
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["youtube-mcp"]["environment"]["YOUTUBE_API_KEY"],
            YOUTUBE_KEY,
        )

    def test_malformed_google_key_warns_and_can_be_overridden(self):
        odd = "not-a-google-key"
        out, r = self.generate_raw(bash_answers(youtube_key=odd, youtube_extra=[
            "y",   # explicit override of the format warning
        ]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Google API key", r.stderr)
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["youtube-mcp"]["environment"]["YOUTUBE_API_KEY"], odd)

    def test_doubled_google_key_paste_triggers_warning_and_reentry(self):
        out, r = self.generate_raw(bash_answers(youtube_key="", youtube_extra=[
            "n",                    # refuse to continue without a key
            YOUTUBE_KEY * 2, "y",   # doubled paste confirmed -> format warning
            "n",                    # do not keep it -> repeat entry
            YOUTUBE_KEY, "y",       # second attempt: good key
        ]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Google API key", r.stderr)
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["youtube-mcp"]["environment"]["YOUTUBE_API_KEY"],
            YOUTUBE_KEY,
        )

    def test_leading_trailing_whitespace_rejected(self):
        out, r = self.generate_raw(bash_answers(youtube_key=" " + YOUTUBE_KEY,
            youtube_extra=[
            YOUTUBE_KEY + " ",                # trailing whitespace -> rejected
            YOUTUBE_KEY, "y",                 # third attempt: clean key
        ], confirm=False))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("fuehrende/nachfolgende Leerzeichen", r.stderr)
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["youtube-mcp"]["environment"]["YOUTUBE_API_KEY"],
            YOUTUBE_KEY,
        )

    def test_mandatory_empty_runtime_key_rejected(self):
        out, r = self.generate_raw(bash_answers(runtime_key="", runtime_extra=[
            RUNTIME_KEY, "y",   # provide it on the second attempt
        ]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Eine Eingabe ist erforderlich.", r.stderr)
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["openai-tunnel"]["environment"]["CONTROL_PLANE_API_KEY"],
            RUNTIME_KEY,
        )

    def test_runtime_key_without_recognized_prefix_warns(self):
        odd_runtime = "custom-opaque-token"
        out, r = self.generate_raw(bash_answers(runtime_key=odd_runtime, runtime_extra=[
            "y",   # explicit override of the prefix warning
        ]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OpenAI-Key-Praefix", r.stderr)
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["openai-tunnel"]["environment"]["CONTROL_PLANE_API_KEY"],
            odd_runtime,
        )

    def test_tunnel_id_remains_fully_visible_and_unmasked(self):
        out, r = self.generate()
        # The tunnel ID is not a secret: it lands unmasked in the YAML and
        # no mask/confirmation step is applied to it.
        d = parse_yaml(out)
        self.assertEqual(
            d["services"]["openai-tunnel"]["environment"]["CONTROL_PLANE_TUNNEL_ID"],
            VALID_ID,
        )
        self.assertNotIn("XXXXXXXXXcdef", r.stderr)  # ID never masked

    def test_bash_output_remains_ascii(self):
        _out, r = self.generate()
        self.assertTrue(all(ord(c) < 128 for c in r.stdout + r.stderr))


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

    def test_tunnel_depends_on_service_healthy(self):
        self.assertIn("youtube-mcp:", self.text)  # mapping key
        self.assertIn("condition: service_healthy", self.text)

    def test_masked_confirmation_present(self):
        self.assertIn("Read-SecretText", self.text)
        self.assertIn("Confirm", self.text)
        self.assertIn("No YouTube API key was entered.", self.text)

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

    def test_healthcheck_python_command_is_stdlib_and_targets_healthz(self):
        dockerfile = MCP_DOCKERFILE.read_text()
        m = re.search(r"HEALTHCHECK[^\n]*\n\s*CMD \[(.*?)\]", dockerfile, re.S)
        self.assertIsNotNone(m, "Dockerfile must define a HEALTHCHECK CMD")
        cmd = json.loads("[" + m.group(1) + "]")
        self.assertEqual(cmd[0], "python3")
        code = cmd[2]
        self.assertIn("/healthz", code)
        self.assertIn("urllib", code)
        # no curl/wget may be installed or invoked for the healthcheck
        self.assertNotIn("apt-get", dockerfile)
        self.assertNotIn("RUN apt", dockerfile)
        for line in dockerfile.splitlines():
            if line.strip().startswith("#"):
                continue
            self.assertNotIn("curl", line)
            self.assertNotIn("wget", line)

    def test_healthcheck_detects_delayed_listener(self):
        """Deterministic delayed-readiness test without Docker or credentials.

        Starts a plain stdlib HTTP server after a delay on port 8765 and
        runs the exact healthcheck command from the Dockerfile: it must
        fail while no listener exists and succeed once /healthz responds.
        """
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
        harness = (
            "import json, subprocess, sys\n"
            "cmd = " + repr(cmd) + "\n"
            + script.replace("cmd", "cmd")
        )
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
        self.assertIn("OPENAI_TUNNEL_ID", text)  # accepted as deprecated alias
        self.assertIn("EX_CONFIG=78", text)
        self.assertIn("exec", text)
        self.assertNotIn("su-exec", text)


if __name__ == "__main__":
    unittest.main()
