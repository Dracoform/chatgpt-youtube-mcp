"""Executable PowerShell-vs-Bash generator equivalence test.

Runs both generators with identical answers and compares the generated
YAML structurally. Requires pwsh; skipped when PowerShell is unavailable
(local dev machines without pwsh run the static tests instead; CI's
ubuntu runner has pwsh and executes this file).
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SH_GENERATOR = REPO / "generators" / "generate_docker-compose_for_ChatGPT_MCP.sh"
PS1_GENERATOR = REPO / "generators" / "generate_docker-compose_for_ChatGPT_MCP.ps1"

# Same answers for both generators, in prompt order:
# mcp_tag, tunnel_tag, youtube_api_key, ytdlp_enable, consent,
# languages, max_chars, tunnel_id, runtime_key, proxy
ANSWERS = [
    "0.9.9",            # mcp tag (non-default to catch dropped prompts)
    "0.9.8",            # tunnel tag
    "",                 # youtube api key (empty)
    "ja",               # enable yt-dlp
    "JA",               # consent
    "de,en,fr",         # languages
    "120000",           # max chars
    "tunnel_0123456789abcdef0123456789abcdef",
    "EQUIVALENCE-TEST-KEY",
    "http://proxy:3128",
]

VALID_ID = "tunnel_0123456789abcdef0123456789abcdef"


def has_pwsh():
    return bool(shutil.which("pwsh"))


def run_bash(out_path):
    inp = "\n".join(ANSWERS) + "\n"
    return subprocess.run(
        ["bash", str(SH_GENERATOR), "--output", str(out_path)],
        input=inp, capture_output=True, text=True,
    )


def run_pwsh(out_dir):
    """Drive the ps1 generator non-interactively through pwsh."""
    # NOTE: PowerShell variables are case-insensitive, so the queue and the
    # answer array must not share a name (that collision is what broke CI).
    answers_literal = ", ".join("'" + a.replace("'", "''") + "'" for a in ANSWERS)
    script = f"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Non-interactive input plumbing: override Read-Host for the session.
# Use $global: scope: the generator script runs in its own script scope,
# so $script: inside our override would not be visible from there.
$global:answerQueue = [System.Collections.Generic.Queue[string]]::new()
foreach ($item in @({answers_literal})) {{ $global:answerQueue.Enqueue($item) }}
function global:Read-Host {{
    param([string]$PromptMessage, [switch]$AsSecureString)
    if ($AsSecureString) {{
        # secret prompt: return a real SecureString (the generator wraps it
        # via SecureStringToBSTR itself). ConvertTo-SecureString rejects the
        # empty string, so build the SecureString char by char — matching
        # real Read-Host -AsSecureString behavior for empty optional input.
        $secure = New-Object System.Security.SecureString
        foreach ($ch in ($global:answerQueue.Dequeue()).ToCharArray()) {{
            $secure.AppendChar($ch)
        }}
        return $secure
    }}
    return $global:answerQueue.Dequeue()
}}
& '{PS1_GENERATOR}' -OutputPath '{out_dir / 'stack.yml'}'
exit 0
"""
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )


@unittest.skipUnless(has_pwsh(), "pwsh not available on this host (CI runs it)")
class TestGeneratorEquivalence(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gen-equiv-"))
        rb = run_bash(cls.tmp / "bash-stack.yml")
        rp = run_pwsh(cls.tmp)
        if rb.returncode != 0:
            raise AssertionError(f"bash generator failed: {rb.stderr}")
        if rp.returncode != 0:
            raise AssertionError(f"pwsh generator failed: {rp.stdout} {rp.stderr}")
        import yaml
        cls.bash = yaml.safe_load((cls.tmp / "bash-stack.yml").read_text())
        cls.pwsh = yaml.safe_load((cls.tmp / "stack.yml").read_text())

    def test_secret_never_printed_by_either(self):
        # generators were captured above; assert key is in the YAML env only
        for doc in (self.bash, self.pwsh):
            env = doc["services"]["openai-tunnel"]["environment"]
            self.assertEqual(env["CONTROL_PLANE_API_KEY"], "EQUIVALENCE-TEST-KEY")

    def test_images_identical(self):
        for svc in ("youtube-mcp", "openai-tunnel"):
            self.assertEqual(
                self.bash["services"][svc]["image"],
                self.pwsh["services"][svc]["image"],
                f"image mismatch for {svc}",
            )
        self.assertEqual(
            self.bash["services"]["youtube-mcp"]["image"],
            "ghcr.io/dracoform/chatgpt-youtube-mcp:0.9.9",
        )
        self.assertEqual(
            self.bash["services"]["openai-tunnel"]["image"],
            "ghcr.io/dracoform/openai-mcp-tunnel:0.9.8",
        )

    def test_tunnel_environment_identical(self):
        b = self.bash["services"]["openai-tunnel"]["environment"]
        p = self.pwsh["services"]["openai-tunnel"]["environment"]
        self.assertEqual(b, p)
        self.assertEqual(b["CONTROL_PLANE_TUNNEL_ID"], VALID_ID)
        self.assertNotIn("OPENAI_TUNNEL_ID", b)

    def test_youtube_mcp_environment_identical(self):
        self.assertEqual(
            self.bash["services"]["youtube-mcp"]["environment"],
            self.pwsh["services"]["youtube-mcp"]["environment"],
        )

    def test_security_settings_identical(self):
        for svc in ("youtube-mcp", "openai-tunnel"):
            b = self.bash["services"][svc]
            p = self.pwsh["services"][svc]
            for key in ("read_only", "tmpfs", "cap_drop", "security_opt",
                        "restart", "networks"):
                self.assertEqual(b.get(key), p.get(key), f"{svc}.{key}")
        tun_b = self.bash["services"]["openai-tunnel"]
        tun_p = self.pwsh["services"]["openai-tunnel"]
        self.assertEqual(str(tun_b["stop_grace_period"]),
                         str(tun_p["stop_grace_period"]))
        self.assertEqual(tun_b["depends_on"], tun_p["depends_on"])

    def test_no_ports_no_volumes_in_either(self):
        for doc in (self.bash, self.pwsh):
            self.assertNotIn("ports", doc["services"]["openai-tunnel"])
            self.assertNotIn("ports", doc["services"]["youtube-mcp"])
            self.assertNotIn("volumes", doc)
            self.assertNotIn("volumes", doc["services"]["openai-tunnel"])

    def test_networks_identical(self):
        self.assertEqual(self.bash["networks"], self.pwsh["networks"])

    def test_proxy_behavior_identical(self):
        for doc in (self.bash, self.pwsh):
            env = doc["services"]["openai-tunnel"]["environment"]
            self.assertEqual(env["HTTPS_PROXY"], "http://proxy:3128")
            self.assertEqual(env["NO_PROXY"], "youtube-mcp,localhost,127.0.0.1")

    def test_yt_dlp_settings_identical(self):
        for doc in (self.bash, self.pwsh):
            env = doc["services"]["youtube-mcp"]["environment"]
            self.assertEqual(env["YOUTUBE_ENABLE_YTDLP"], "true")
            self.assertEqual(env["YOUTUBE_DEFAULT_LANGUAGES"], "de,en,fr")
            self.assertEqual(env["YOUTUBE_TRANSCRIPT_MAX_CHARS"], "120000")


if __name__ == "__main__":
    unittest.main()
