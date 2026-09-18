"""Three-way generator equivalence test (Stage 4).

Runs the REAL Bash and PowerShell generators (in non-interactive `--input`
mode) and the REAL Web generator (buildYaml via node), and compares their output
byte-for-byte against generators/canonical_model.py — the single source of
truth — for the representative canonical inputs in tests/fixtures/:

  tunnel-only, static-public, oauth-public, static-oauth, tunnel-static-oauth, local

Requires bash + node always; pwsh when available (CI ubuntu runners; local
hosts without pwsh skip the PowerShell arm).
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO / "tests" / "fixtures"
SH_GENERATOR = REPO / "generators" / "generate_docker-compose_for_ChatGPT_MCP.sh"
PS1_GENERATOR = REPO / "generators" / "generate_docker-compose_for_ChatGPT_MCP.ps1"
WEB_GENERATOR = REPO / "docs" / "assets" / "generator.js"

# Canonical inputs that must produce identical YAML across all generators.
CASES = [
    "tunnel-only",
    "static-public",
    "oauth-public",
    "static-oauth",
    "tunnel-static-oauth",
    "local",
]


def _secrets_for(fixture):
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


def oracle(fixture):
    """Render the reference YAML."""
    import sys
    sys.path.insert(0, str(REPO))
    from generators.canonical_model import render
    return render(str(FIXTURES_DIR / f"{fixture}.json"))


def run_bash(fixture, out_path):
    secrets = _secrets_for(fixture)
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
    return Path(out_path).read_text()


def run_pwsh(fixture, out_path):
    secrets = _secrets_for(fixture)
    script = (
        f"Set-Location '{REPO}'; "
        f"& '{PS1_GENERATOR}' -InputPath '{FIXTURES_DIR / (fixture + '.json')}' "
        f"-OutputPath '{out_path}'; exit $LASTEXITCODE"
    )
    try:
        r = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=40,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"pwsh timeout for {fixture}:\n"
            f"{_redact((exc.stdout or '') + (exc.stderr or ''), secrets)}"
        ) from None
    if r.returncode != 0:
        raise AssertionError(
            f"pwsh failed for {fixture} (rc={r.returncode}): "
            f"{_redact(r.stdout + r.stderr, secrets)}")
    return Path(out_path).read_text()


def run_web(fixture):
    """Drive the web generator's buildYaml with the fixture object via node."""
    secrets = _secrets_for(fixture)
    script = f"""
'use strict';
const fs = require('fs');
const gen = require({str(WEB_GENERATOR)!r});
const fx = JSON.parse(fs.readFileSync({str(FIXTURES_DIR / (fixture + '.json'))!r}, 'utf8'));
process.stdout.write(gen.buildYaml(fx));
"""
    try:
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"node timeout for {fixture}:\n"
            f"{_redact((exc.stdout or '') + (exc.stderr or ''), secrets)}"
        ) from None
    if r.returncode != 0:
        raise AssertionError(
            f"web failed for {fixture} (rc={r.returncode}): "
            f"{_redact(r.stderr, secrets)}")
    return r.stdout


def has_pwsh():
    return bool(shutil.which("pwsh"))


class TestThreeWayEquivalence(unittest.TestCase):

    def test_all_generators_match_oracle_for_all_cases(self):
        tmp = Path(tempfile.mkdtemp(prefix="gen3way-"))
        for case in CASES:
            expected = oracle(case)
            bash = run_bash(case, tmp / f"bash-{case}.yml")
            self.assertEqual(bash, expected, f"Bash != oracle for {case}")
            web = run_web(case)
            self.assertEqual(web, expected, f"Web != oracle for {case}")

    @unittest.skipUnless(has_pwsh(), "pwsh not available on this host (CI runs it)")
    def test_powershell_matches_oracle_for_all_cases(self):
        tmp = Path(tempfile.mkdtemp(prefix="gen3way-ps-"))
        for case in CASES:
            expected = oracle(case)
            ps = run_pwsh(case, tmp / f"ps-{case}.yml")
            self.assertEqual(ps, expected, f"PowerShell != oracle for {case}")

    def test_local_publishes_core_loopback_only(self):
        yml = oracle("local")
        self.assertIn("127.0.0.1:8765:8765", yml)

    def test_core_never_public_in_any_case(self):
        for case in CASES:
            yml = oracle(case)
            self.assertNotIn("0.0.0.0:8765", yml, f"core exposed publicly in {case}")

    def test_oracle_validates_rejected_combinations(self):
        import sys
        sys.path.insert(0, str(REPO))
        from generators.canonical_model import validate
        # public no-auth edge
        with self.assertRaises(ValueError):
            validate({"access": ["static"], "edge": {"auth_modes": []}})
        # oauth without resource
        with self.assertRaises(ValueError):
            validate({"access": ["oauth"],
                      "edge": {"auth_modes": ["oauth"], "oauth": {"issuer": "x"}}})
        # half TLS
        with self.assertRaises(ValueError):
            validate({"access": ["static"],
                      "edge": {"auth_modes": ["static"], "tls": "edge",
                               "cert_file": "/c", "key_file": "",
                               "static_tokens": ["ytsk_ok"], "oauth": {}}})
        # static+oauth namespace ambiguity
        with self.assertRaises(ValueError):
            validate({"access": ["static", "oauth"],
                      "edge": {"auth_modes": ["static", "oauth"], "tls": "ingress",
                               "static_tokens": ["noprefix"],
                               "oauth": {"issuer": "https://as/r", "resource": "https://m/mcp"}}})


if __name__ == "__main__":
    unittest.main()
