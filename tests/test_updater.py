"""Tests for the staged yt-dlp updater (src/youtube_mcp/updater.py)."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile

from youtube_mcp.core import Settings, YouTubeService
from youtube_mcp.updater import UpdaterSettings, YtDlpUpdater


class ResponseBytes:
    """Minimal context-manager response stub."""

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size=-1):
        data, self.data = self.data, b""
        return data


def make_wheel_bytes(version="2025.01.01"):
    """Build a tiny real wheel in-memory containing yt_dlp/__init__.py."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "yt_dlp/__init__.py",
            f'__version__ = "{version}"\n',
        )
        zf.writestr("yt_dlp/version.py", f'__version__ = "{version}"\n')
    return buf.getvalue()


def pypi_payload(version="2025.01.01", wheel_bytes=None, sha256=None):
    if wheel_bytes is None:
        wheel_bytes = make_wheel_bytes(version)
    digest = sha256 if sha256 is not None else hashlib.sha256(wheel_bytes).hexdigest()
    return {
        "releases": {
            version: [
                {
                    "filename": f"yt_dlp-{version}-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "python_version": "py3",
                    "url": "https://files.example/yt_dlp.whl",
                    "digests": {"sha256": digest},
                }
            ]
        }
    }


def fake_run_factory(record=None, *, version="2025.01.01", returncode=0, stdout=None):
    """Build a fake run for the updater's `--version` validation call."""

    def fake_run(command, capture_output, text, timeout, check, **kwargs):
        if record is not None:
            record.append({"command": list(command), "kwargs": dict(kwargs)})
        return subprocess.CompletedProcess(
            [sys.executable, "-m", "yt_dlp", "--version"],
            returncode,
            stdout if stdout is not None else f"{version}\n",
            "",
        )

    return fake_run


def make_updater(settings, *, responses=None, run=None, fail_metadata=False):
    """Build an updater with a fake urlopen serving the given payloads."""
    calls = []
    payloads = list(responses or [])

    def fake_urlopen(request, timeout=0):
        calls.append(getattr(request, "full_url", str(request)))
        if fail_metadata:
            raise RuntimeError("pypi exploded")
        return ResponseBytes(payloads.pop(0) if payloads else b"{}")

    return YtDlpUpdater(settings, urlopen=fake_urlopen, run=run), calls


class UpdaterSettingsEnvTests(unittest.TestCase):
    ENV_VARS = (
        "YTDLP_AUTO_UPDATE",
        "YTDLP_UPDATE_CHANNEL",
        "YTDLP_UPDATE_INTERVAL",
        "YTDLP_STATE_DIR",
        "YTDLP_UPDATE_SMOKE",
    )

    def setUp(self):
        self._saved = {name: os.environ.get(name) for name in self.ENV_VARS}

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_defaults_when_no_env(self):
        for name in self.ENV_VARS:
            os.environ.pop(name, None)
        settings = UpdaterSettings.from_env()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.channel, "stable")
        self.assertEqual(settings.interval_seconds, 86400)
        self.assertEqual(settings.state_dir, "/app/state/ytdlp")
        self.assertFalse(settings.smoke_test)

    def test_env_overrides_parse(self):
        os.environ["YTDLP_AUTO_UPDATE"] = "true"
        os.environ["YTDLP_UPDATE_CHANNEL"] = "nightly"
        os.environ["YTDLP_UPDATE_INTERVAL"] = "300"
        os.environ["YTDLP_STATE_DIR"] = "/tmp/state"
        os.environ["YTDLP_UPDATE_SMOKE"] = "1"
        settings = UpdaterSettings.from_env()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.channel, "nightly")
        self.assertEqual(settings.interval_seconds, 300)
        self.assertEqual(settings.state_dir, "/tmp/state")
        self.assertTrue(settings.smoke_test)

    def test_invalid_channel_falls_back_to_stable(self):
        os.environ["YTDLP_UPDATE_CHANNEL"] = "bogus"
        settings = UpdaterSettings.from_env()
        self.assertEqual(settings.channel, "stable")

    def test_invalid_interval_falls_back_to_default(self):
        os.environ["YTDLP_UPDATE_INTERVAL"] = "not-a-number"
        settings = UpdaterSettings.from_env()
        self.assertEqual(settings.interval_seconds, 86400)

    def test_interval_below_60_clamps_to_60(self):
        os.environ["YTDLP_UPDATE_INTERVAL"] = "5"
        settings = UpdaterSettings.from_env()
        self.assertEqual(settings.interval_seconds, 60)


class CustomEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ytdlp-test-")
        self.settings = UpdaterSettings(state_dir=self.state)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.state, ignore_errors=True)

    def test_no_active_pointer_returns_none(self):
        updater = YtDlpUpdater(self.settings, urlopen=lambda *a, **k: None, run=None)
        self.assertIsNone(updater.custom_environment())

    def test_bundled_pointer_returns_none(self):
        with open(os.path.join(self.state, "active"), "w") as fh:
            fh.write("bundled")
        updater = YtDlpUpdater(self.settings, urlopen=lambda *a, **k: None, run=None)
        self.assertIsNone(updater.custom_environment())

    def test_valid_active_version_returns_pythonpath(self):
        version_dir = os.path.join(self.state, "versions", "2025.01.01")
        os.makedirs(os.path.join(version_dir, "yt_dlp"))
        with open(os.path.join(self.state, "active"), "w") as fh:
            fh.write("versions/2025.01.01")
        updater = YtDlpUpdater(self.settings, urlopen=lambda *a, **k: None, run=None)
        env = updater.custom_environment()
        self.assertEqual(env, {"PYTHONPATH": version_dir})

    def test_active_without_yt_dlp_dir_is_invalid_bundled(self):
        version_dir = os.path.join(self.state, "versions", "2025.01.01")
        os.makedirs(version_dir)
        with open(os.path.join(self.state, "active"), "w") as fh:
            fh.write("versions/2025.01.01")
        updater = YtDlpUpdater(self.settings, urlopen=lambda *a, **k: None, run=None)
        self.assertIsNone(updater.custom_environment())


class UpdateNowHappyPathTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ytdlp-test-")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.state, ignore_errors=True)

    def test_promotes_version_and_is_idempotent(self):
        version = "2025.01.01"
        wheel = make_wheel_bytes(version)
        settings = UpdaterSettings(state_dir=self.state)
        run_record = []
        updater, _ = make_updater(
            settings,
            responses=[
                json.dumps(pypi_payload(version, wheel)).encode("utf-8"),
                wheel,
            ],
            run=fake_run_factory(run_record, version=version),
        )
        promoted = updater.update_now()
        self.assertEqual(promoted, version)
        self.assertEqual(updater._last_check_result, "promoted")
        self.assertIsNone(updater._last_error)
        self.assertEqual(updater.active_version(), version)
        self.assertEqual(updater.versions(), [version])
        version_dir = os.path.join(self.state, "versions", version)
        self.assertEqual(
            updater.custom_environment(),
            {"PYTHONPATH": version_dir},
        )
        # active pointer written, and validation ran via injected run.
        with open(updater.active_path) as fh:
            self.assertEqual(fh.read().strip(), f"versions/{version}")
        self.assertTrue(run_record)
        self.assertTrue(os.path.isdir(os.path.join(version_dir, "yt_dlp")))
        # No wheel left in staging.
        staging = os.path.join(self.state, "staging")
        self.assertFalse(
            any(name.startswith(".wheel-") for name in os.listdir(staging))
        )

    def test_second_and_third_update_now_cycles(self):
        """Promote a first version, then a newer one, then report up_to_date.

        Exercises the full cycle three times: metadata fetch is always
        followed by (only when a newer version is found) a wheel download.
        A call-sequence stub serves metadata first, then the wheel bytes, so
        SHA-256 verification passes for each real promotion.
        """
        version = "2025.01.01"
        wheel = make_wheel_bytes(version)
        version2 = "2025.01.02"
        wheel2 = make_wheel_bytes(version2)

        def sequence_urlopen(*, metadata_bytes, wheel_bytes):
            state = {"queued": [metadata_bytes, wheel_bytes]}

            def call(request, timeout=0):
                return ResponseBytes(state["queued"].pop(0) if state["queued"] else b"{}")

            return call

        settings = UpdaterSettings(state_dir=self.state)
        updater, _ = make_updater(
            settings,
            responses=[
                json.dumps(pypi_payload(version, wheel)).encode("utf-8"),
                wheel,
            ],
            run=fake_run_factory(version=version),
        )
        # Cycle 1: promote v1.
        self.assertEqual(updater.update_now(), version)
        self.assertEqual(updater.active_version(), version)

        # Cycle 2: PyPI now offers v2 (newer) -> promote v2.
        updater._urlopen = sequence_urlopen(
            metadata_bytes=json.dumps(pypi_payload(version2, wheel2)).encode("utf-8"),
            wheel_bytes=wheel2,
        )
        updater._run = fake_run_factory(version=version2)
        self.assertEqual(updater.update_now(), version2)
        self.assertEqual(updater._last_check_result, "promoted")
        self.assertEqual(updater.active_version(), version2)
        self.assertEqual(updater.versions(), sorted([version, version2]))
        # Previous version retained for rollback.
        with open(updater.previous_path, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), f"versions/{version}")

        # Cycle 3: PyPI still reports the ACTIVE v2 -> up_to_date, no download.
        updater._urlopen = lambda request, timeout=0: ResponseBytes(
            json.dumps(pypi_payload(version2, wheel2)).encode("utf-8")
        )
        self.assertIsNone(updater.update_now())
        self.assertEqual(updater._last_check_result, "up_to_date")
        self.assertEqual(updater.active_version(), version2)


class UpdateNowRejectionTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ytdlp-test-")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.state, ignore_errors=True)

    def _make_updater(self, *, sha256, wheel=None, run=None):
        settings = UpdaterSettings(state_dir=self.state)
        return make_updater(
            settings,
            responses=[
                json.dumps(pypi_payload("2025.01.01", wheel, sha256=sha256)).encode("utf-8"),
                wheel,
            ],
            run=run,
        )[0]

    def test_sha256_mismatch_rejects_candidate(self):
        wheel = make_wheel_bytes()
        # Correct hash for a DIFFERENT payload -> the downloaded wheel mismatches.
        wrong_digest = hashlib.sha256(b"not-the-wheel").hexdigest()
        updater = self._make_updater(sha256=wrong_digest, wheel=wheel)
        self.assertIsNone(updater.update_now())
        self.assertEqual(updater._last_check_result, "validation_failed")
        self.assertIsNotNone(updater._last_error)
        self.assertIn("SHA-256", updater._last_error)
        self.assertEqual(updater.active_version(), "bundled")
        staging = os.path.join(self.state, "staging")
        if os.path.isdir(staging):
            self.assertFalse(
                any(name.startswith(".wheel-") for name in os.listdir(staging))
            )

    def test_non_zip_wheel_rejects_candidate(self):
        bad_wheel = b"definitely not a zip file"
        digest = hashlib.sha256(bad_wheel).hexdigest()
        updater = self._make_updater(sha256=digest, wheel=bad_wheel)
        self.assertIsNone(updater.update_now())
        self.assertEqual(updater._last_check_result, "validation_failed")
        self.assertIsNotNone(updater._last_error)
        self.assertIn("extract", updater._last_error.lower())
        self.assertEqual(updater.active_version(), "bundled")
        self.assertEqual(updater.versions(), [])

    def test_validation_failure_rejects_candidate(self):
        wheel = make_wheel_bytes("2025.01.01")
        digest = hashlib.sha256(wheel).hexdigest()
        updater = self._make_updater(
            sha256=digest,
            wheel=wheel,
            run=fake_run_factory(version="2025.01.01", returncode=1),
        )
        self.assertIsNone(updater.update_now())
        self.assertEqual(updater._last_check_result, "validation_failed")
        self.assertEqual(updater.active_version(), "bundled")
        self.assertEqual(updater.active_version(), "bundled")


class DownloadSizeLimitTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ytdlp-test-")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.state, ignore_errors=True)

    def test_oversized_wheel_is_rejected_without_promotion_or_staging_leftovers(self):
        version = "2025.01.01"
        tiny = make_wheel_bytes(version)  # real wheel, content doesn't matter for the size limit
        oversized = b"x" * 1_000_000  # 1 MB >> the test-instance limit below
        settings = UpdaterSettings(state_dir=self.state)
        updater, _ = make_updater(
            settings,
            responses=[
                json.dumps(pypi_payload(version, oversized)).encode("utf-8"),
                oversized,
            ],
            run=fake_run_factory(version=version),
        )
        # Lower the instance limit so the test needs no real multi-MB payload.
        updater.MAX_WHEEL_BYTES = 8 * 1024  # 8 KiB cap
        updater._MAX_CHUNK_BYTES = 1024
        result = updater.update_now()
        # Rejected: no promotion, no active change, non-fatal.
        self.assertIsNone(result)
        self.assertEqual(updater._last_check_result, "validation_failed")
        self.assertIsNotNone(updater._last_error)
        self.assertIn("exceeded size limit", updater._last_error)
        self.assertEqual(updater.active_version(), "bundled")
        self.assertEqual(updater.versions(), [])
        self.assertIsNone(updater.custom_environment())
        # No partial wheel or staging garbage left behind.
        staging = os.path.join(self.state, "staging")
        self.assertFalse(
            any(name.startswith(".wheel-") for name in os.listdir(staging))
        )

    def test_download_wheel_removes_partial_file_on_size_overshoot(self):
        """Direct check: an oversize read aborts and unlinks the temp file."""
        from youtube_mcp.updater import UpdaterError

        settings = UpdaterSettings(state_dir=self.state)
        updater, _calls = make_updater(settings, responses=[])
        updater.MAX_WHEEL_BYTES = 8 * 1024
        updater._MAX_CHUNK_BYTES = 1024

        class Huge:
            def read(self, n=-1):
                # Return far more than the limit in a single read.
                return b"x" * 4096

        class Ctx:
            def __enter__(self):
                return Huge()

            def __exit__(self, *a):
                pass

        def fake_urlopen(request, timeout=0):
            return Ctx()

        updater._urlopen = fake_urlopen
        with self.assertRaises(UpdaterError) as raised:
            updater._download_wheel("https://files.example/huge.whl", "x" * 64, self.state + "/staging")
        self.assertIn("exceeded size limit", str(raised.exception))
        # No .wheel-* temp remains anywhere under the state dir.
        leftovers = []
        for root, _dirs, files in os.walk(self.state):
            leftovers.extend(f for f in files if f.startswith(".wheel-"))
        self.assertEqual(leftovers, [])


class UpdateFailureNonFatalTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ytdlp-test-")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.state, ignore_errors=True)

    def test_metadata_failure_returns_none_without_raising(self):
        settings = UpdaterSettings(state_dir=self.state)
        updater, _ = make_updater(settings, fail_metadata=True)
        try:
            result = updater.update_now()
        except Exception as exc:  # pragma: no cover - failure mode under test
            self.fail(f"update_now raised: {exc!r}")
        self.assertIsNone(result)
        self.assertEqual(updater._last_check_result, "metadata_failed")
        self.assertIsNotNone(updater._last_error)
        self.assertEqual(updater.active_version(), "bundled")

    def test_start_background_disabled_creates_no_thread(self):
        settings = UpdaterSettings(state_dir=self.state, enabled=False)
        updater = YtDlpUpdater(
            settings, urlopen=lambda *a, **k: None, run=lambda *a, **k: __import__("subprocess").CompletedProcess([], 0, "", "")
        )
        updater.start_background()
        self.assertIsNone(updater._thread)

    def test_start_background_enabled_starts_daemon_thread(self):
        settings = UpdaterSettings(state_dir=self.state, enabled=True, interval_seconds=60)
        updater = YtDlpUpdater(
            settings, urlopen=lambda *a, **k: None, run=lambda *a, **k: __import__("subprocess").CompletedProcess([], 0, "", "")
        )
        updater.start_background()
        self.assertIsNotNone(updater._thread)
        self.assertTrue(updater._thread.daemon)
        self.assertEqual(updater._thread.name, "ytdlp-updater")
        updater.stop_background()
        updater._thread.join(timeout=10)
        self.assertFalse(updater._thread.is_alive())


class RollbackAndForceBundledTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ytdlp-test-")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.state, ignore_errors=True)

    def _promote(self, updater, version):
        """Force-promote a version without network."""
        os.makedirs(os.path.join(self.state, "versions", version, "yt_dlp"))
        updater._promote_locked(version)

    def test_rollback_restores_previous_version(self):
        settings = UpdaterSettings(state_dir=self.state)
        updater = YtDlpUpdater(
            settings, urlopen=lambda *a, **k: None, run=lambda *a, **k: __import__("subprocess").CompletedProcess([], 0, "", "")
        )
        self._promote(updater, "v1")
        self._promote(updater, "v2")
        self.assertEqual(updater.active_version(), "v2")
        with open(updater.previous_path) as fh:
            self.assertEqual(fh.read().strip(), "versions/v1")
        restored = updater.rollback()
        self.assertEqual(restored, "versions/v1")
        self.assertEqual(updater.active_version(), "v1")
        self.assertEqual(updater._last_check_result, "rolled_back")

    def test_force_bundled_records_previous(self):
        settings = UpdaterSettings(state_dir=self.state)
        updater = YtDlpUpdater(
            settings, urlopen=lambda *a, **k: None, run=lambda *a, **k: __import__("subprocess").CompletedProcess([], 0, "", "")
        )
        self._promote(updater, "v1")
        updater.force_bundled()
        self.assertEqual(updater.active_version(), "bundled")
        with open(updater.previous_path) as fh:
            self.assertEqual(fh.read().strip(), "versions/v1")
        self.assertIsNone(updater.custom_environment())


class CoreYtDlpEnvExtraTests(unittest.TestCase):
    def _service(self, env_extra, *, fake_run=None):
        recorded = []

        def default_fake_run(command, capture_output, text, timeout, check, **kwargs):
            recorded.append(kwargs)
            return subprocess.CompletedProcess(
                [sys.executable, "-m", "yt_dlp"], 0, "{}", ""
            )

        service = YouTubeService(
            Settings(enable_ytdlp=True),
            run=fake_run or default_fake_run,
            ytdlp_env_extra=env_extra,
        )
        return service, recorded

    def test_env_extra_passed_merged_with_os_environ(self):
        service, recorded = self._service(lambda: {"PYTHONPATH": "/x"})
        service._yt_dlp_json("dQw4w9WgXcQ")
        self.assertEqual(len(recorded), 1)
        self.assertIn("env", recorded[0])
        env = recorded[0]["env"]
        self.assertEqual(env["PYTHONPATH"], "/x")
        # Merged with inherited environment.
        if "PATH" in os.environ:
            self.assertEqual(env["PATH"], os.environ["PATH"])

    def test_no_env_extra_means_no_env_kwarg(self):
        service, recorded = self._service(None)
        service._yt_dlp_json("dQw4w9WgXcQ")
        self.assertEqual(len(recorded), 1)
        self.assertNotIn("env", recorded[0])

    def test_env_extra_returning_none_means_no_env_kwarg(self):
        service, recorded = self._service(lambda: None)
        service._yt_dlp_json("dQw4w9WgXcQ")
        self.assertEqual(len(recorded), 1)
        self.assertNotIn("env", recorded[0])


if __name__ == "__main__":
    unittest.main()