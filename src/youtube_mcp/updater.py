"""Staged, versioned, self-updating yt-dlp for the MCP server.

Design (Phase 2 of the yt-dlp resilience audit):
- The bundled yt-dlp installed in the image's site-packages is the always
  available, known-good fallback. A staged update is a fully extracted,
  validated copy of a yt-dlp release kept in an immutable version directory
  under a writable state directory (on a versioned volume in production).
- Requests resolve which yt-dlp to use via ``custom_environment()``: it
  returns a ``PYTHONPATH`` override pointing at the promoted version
  directory, or ``None`` (use bundled site-packages) when no valid staged
  version is active. Because ``PYTHONPATH`` precedes site-packages on
  ``sys.path``, a staged copy wins over the bundled one.
- Updates happen in a background daemon thread (never blocking startup):
  fetch release metadata from PyPI, download the wheel, verify its SHA-256,
  extract it into an immutable version directory, validate that it imports
  and reports the expected version, and then atomically promote it.
- Atomic promotion via ``os.replace`` on the ``active`` pointer means active
  requests never observe a partially written update: every subprocess
  inherits its environment at spawn, and promotion swaps the pointer in a
  single atomic step. The previous version pointer is retained for rollback.
- Any failure during fetch/download/verify/extract/validate is logged and
  leaves the current active version untouched; it never propagates and never
  prevents the server from starting or continuing to serve with bundled.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("youtube_mcp.updater")

DEFAULT_STATE_DIR = "/app/state/ytdlp"
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60  # 86400
VALID_CHANNELS = ("stable", "nightly")
PYPI_JSON_URL = "https://pypi.org/pypi/yt-dlp/json"


@dataclass(frozen=True)
class UpdaterSettings:
    """Runtime configuration for the yt-dlp updater (from environment)."""

    enabled: bool = False
    channel: str = "stable"
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    state_dir: str = DEFAULT_STATE_DIR
    # When true, promotion additionally runs a tiny live YouTube smoke check.
    smoke_test: bool = False
    smoke_url: str = "https://www.youtube.com/watch?v=BaW1PyD2tbw"

    @classmethod
    def from_env(cls) -> "UpdaterSettings":
        def _bool(name: str) -> bool:
            return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}

        channel = os.getenv("YTDLP_UPDATE_CHANNEL", "stable").strip().lower()
        if channel not in VALID_CHANNELS:
            # Never raise at import/startup config time; fall back to stable
            # and log so a bad value cannot take the server down.
            logger.warning("Ignoring invalid YTDLP_UPDATE_CHANNEL=%r; using 'stable'.", channel)
            channel = "stable"
        try:
            interval = int(os.getenv("YTDLP_UPDATE_INTERVAL", str(DEFAULT_INTERVAL_SECONDS)))
        except ValueError:
            interval = DEFAULT_INTERVAL_SECONDS
        if interval < 60:
            interval = 60
        return cls(
            enabled=_bool("YTDLP_AUTO_UPDATE"),
            channel=channel,
            interval_seconds=interval,
            state_dir=os.getenv("YTDLP_STATE_DIR", DEFAULT_STATE_DIR).strip() or DEFAULT_STATE_DIR,
            smoke_test=_bool("YTDLP_UPDATE_SMOKE"),
            smoke_url=os.getenv("YTDLP_UPDATE_SMOKE_URL", "https://www.youtube.com/watch?v=BaW1PyD2tbw").strip(),
        )


class UpdaterError(RuntimeError):
    """A non-fatal updater failure (logged, never fatal to the server)."""


class YtDlpUpdater:
    """Download, validate, and atomically promote staged yt-dlp versions."""

    # Upper bound on a single staged wheel download (bytes). yt-dlp wheels
    # are a few MB; this is generous headroom while guaranteeing a broken or
    # malicious wheel URL cannot exhaust the writable state volume.
    MAX_WHEEL_BYTES: int = 64 * 1024 * 1024  # 64 MiB
    _MAX_CHUNK_BYTES: int = 1 << 16  # 64 KiB read chunk

    def __init__(
        self,
        settings: UpdaterSettings | None = None,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.settings = settings or UpdaterSettings.from_env()
        self._urlopen = urlopen
        self._run = run
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # Diagnostics state (read by the status tool / final report).
        self._last_check_us: float = 0.0
        self._last_check_result: str | None = None
        self._last_error: str | None = None
        self._status_note: str = "updater initialized"
        self._checked_once = False

    # ---- paths -------------------------------------------------------------

    @property
    def versions_dir(self) -> str:
        return os.path.join(self.settings.state_dir, "versions")

    @property
    def active_path(self) -> str:
        return os.path.join(self.settings.state_dir, "active")

    @property
    def previous_path(self) -> str:
        return os.path.join(self.settings.state_dir, "previous")

    def _read_pointer(self, path: str) -> str | None:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
            return value or None
        except OSError:
            return None

    def _write_pointer_atomic(self, path: str, value: str) -> None:
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".pointer-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(value)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- resolver (consumed by core._yt_dlp_json) --------------------------

    def custom_environment(self) -> dict[str, str] | None:
        """Return a `PYTHONPATH` override for the active staged version.

        Returns None when the active pointer names the bundled version or
        points at a directory that is not a valid extracted yt-dlp package;
        in that case callers use site-packages (bundled), which is the
        known-good fallback.
        """
        active = self._read_pointer(self.active_path)
        if not active or active == "bundled":
            return None
        version_dir = os.path.join(self.settings.state_dir, active)
        if not self._is_importable_dir(version_dir):
            logger.warning("Active staged yt-dlp dir invalid (%s); using bundled", version_dir)
            return None
        return {"PYTHONPATH": version_dir}

    @staticmethod
    def _is_importable_dir(directory: str) -> bool:
        try:
            return os.path.isdir(os.path.join(directory, "yt_dlp"))
        except OSError:
            return False

    @staticmethod
    def bundled_token() -> str:
        return "bundled"

    # ---- release metadata ----------------------------------------------------

    def _fetch_release_metadata(self) -> tuple[str, str, str]:
        """Return (version, wheel_url, sha256) for the configured channel.

        Uses the public PyPI JSON API for the ``yt-dlp`` project. Nightlies
        are the ``*.dev0`` versions; stable is the latest non-dev version.
        """
        request = urllib.request.Request(PYPI_JSON_URL, headers={"Accept": "application/json"})
        try:
            with self._urlopen(request, timeout=20) as response:
                payload = json.loads(response.read())
        except Exception as exc:  # noqa: BLE001 - wrap as non-fatal
            raise UpdaterError(f"Could not fetch PyPI release metadata: {exc}") from exc
        releases = payload.get("releases") or {}
        versions = sorted(
            releases.keys(),
            key=lambda v: [int(part) for part in v.replace("-", ".").split(".") if part.isdigit()],
        )
        if self.settings.channel == "nightly":
            candidates = [v for v in releases if "dev" in v.lower()]
            candidates = candidates or list(releases)
        else:
            candidates = [v for v in releases if "dev" not in v.lower()]
        if not candidates:
            raise UpdaterError(f"No {self.settings.channel} releases found on PyPI.")
        target = max(candidates)
        files = releases.get(target) or []
        wheel = None
        for item in files:
            filename = item.get("filename", "")
            if filename.endswith(".whl") and item.get("packagetype") == "bdist_wheel":
                if item.get("python_version", "").startswith("py3"):
                    wheel = item
                    break
        if not wheel:
            raise UpdaterError(f"No pure-python wheel available for yt-dlp {target}.")
        sha256 = (wheel.get("digests") or {}).get("sha256")
        if not sha256:
            raise UpdaterError(f"No sha256 digest published for yt-dlp {target}.")
        return target, wheel["url"], sha256

    def _download_wheel(self, url: str, expected_sha256: str, dest_dir: str) -> str:
        """Download a wheel and verify its SHA-256 before staging it.

        The download is bounded: if the upstream response exceeds
        ``MAX_WHEEL_BYTES``, the transfer is aborted and the partial file
        removed, so a broken or malicious wheel URL cannot exhaust the state
        volume. The partial download is never hashed, extracted, or promoted.
        """
        os.makedirs(dest_dir, exist_ok=True)
        fd, dest = tempfile.mkstemp(dir=dest_dir, prefix=".wheel-", suffix=".whl")
        total = 0
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "youtube-current-data-mcp-updater/0.1"})
            with self._urlopen(request, timeout=60) as response:
                with os.fdopen(fd, "wb") as fh:
                    while True:
                        chunk = response.read(self._MAX_CHUNK_BYTES)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.MAX_WHEEL_BYTES:
                            raise UpdaterError(
                                f"yt-dlp wheel download exceeded size limit of {self.MAX_WHEEL_BYTES} bytes"
                            )
                        fh.write(chunk)
            hasher = hashlib.sha256()
            with open(dest, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    hasher.update(chunk)
            actual = hasher.hexdigest()
            if actual.lower() != expected_sha256.lower():
                raise UpdaterError(
                    f"SHA-256 mismatch for yt-dlp wheel: expected {expected_sha256}, got {actual}"
                )
            return dest
        except BaseException:
            try:
                os.unlink(dest)
            except OSError:
                pass
            raise

    def _extract_wheel(self, wheel_path: str, version: str) -> str:
        """Extract a wheel into an immutable version directory; return its path."""
        version_dir = os.path.join(self.versions_dir, version)
        tmp_dir = os.path.join(self.versions_dir, f".tmp-{version}")
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        os.makedirs(tmp_dir, exist_ok=True)
        try:
            with __import__("zipfile").ZipFile(wheel_path) as zf:
                zf.extractall(tmp_dir)
        except Exception as exc:  # noqa: BLE001 - non-fatal
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise UpdaterError(f"Could not extract yt-dlp {version} wheel: {exc}") from exc
        if not self._is_importable_dir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise UpdaterError(f"Extracted yt-dlp {version} wheel has no top-level yt_dlp package.")
        # Make the version directory immutable after extraction: place it only
        # when fully valid, then never modify it again.
        if os.path.exists(version_dir):
            shutil.rmtree(version_dir, ignore_errors=True)
        os.replace(tmp_dir, version_dir)
        return version_dir

    def _validate(self, version_dir: str, expected_version: str) -> None:
        """Validate a candidate: it must import and report the expected version.

        Raises UpdaterError on failure so promotion is refused.
        """
        env = dict(os.environ)
        env["PYTHONPATH"] = version_dir
        try:
            completed = self._run(
                [sys.executable, "-m", "yt_dlp", "--version"],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001 - non-fatal
            raise UpdaterError(f"yt-dlp {expected_version} did not run: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise UpdaterError(f"yt-dlp {expected_version} validation failed: {detail[:300]}")
        reported = (completed.stdout or "").strip()
        if not reported or expected_version not in reported:
            raise UpdaterError(
                f"yt-dlp reports {reported!r}, expected version {expected_version!r}"
            )

    def _smoke(self, version_dir: str) -> None:
        """Optional lightweight live smoke against the configured URL."""
        if not self.settings.smoke_test:
            return
        env = dict(os.environ)
        env["PYTHONPATH"] = version_dir
        try:
            completed = self._run(
                [sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", "1",
                 "--dump-single-json", "--skip-download", "--quiet", "--no-warnings", self.settings.smoke_url],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001 - non-fatal
            raise UpdaterError(f"Smoke test failed: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise UpdaterError(f"Smoke test failed: {detail[:200]}")

    # ---- update / promote / rollback ---------------------------------------

    def update_now(self) -> str | None:
        """Run one update cycle; returns the newly promoted version or None."""
        with self._lock:
            return self._update_locked()

    def _update_locked(self) -> str | None:
        self._last_check_us = time.time()
        self._last_error = None
        try:
            version, url, sha256 = self._fetch_release_metadata()
        except UpdaterError as exc:
            self._last_check_result = "metadata_failed"
            self._last_error = str(exc)
            self._status_note = f"update failed (metadata): {exc}"
            logger.warning("yt-dlp update (metadata): %s", exc)
            return None
        current = self._read_pointer(self.active_path) or "bundled"
        # The pointer stores a path like "versions/<v>"; compare the bare
        # version so an already-staged active version short-circuits.
        current_version = None if current == "bundled" else os.path.basename(current)
        if current_version == version:
            self._last_check_result = "up_to_date"
            self._status_note = f"already at {version}"
            logger.info("yt-dlp already at latest %s (%s)", self.settings.channel, version)
            return None
        try:
            staging = os.path.join(self.settings.state_dir, "staging")
            wheel = self._download_wheel(url, sha256, staging)
            try:
                version_dir = self._extract_wheel(wheel, version)
                self._validate(version_dir, version)
                self._smoke(version_dir)
            finally:
                try:
                    os.unlink(wheel)
                except OSError:
                    pass
        except UpdaterError as exc:
            self._last_check_result = "validation_failed"
            self._last_error = str(exc)
            self._status_note = f"update failed (candidate rejected): {exc}"
            logger.warning("yt-dlp update rejected: %s", exc)
            return None
        self._promote_locked(version)
        self._last_check_result = "promoted"
        self._status_note = f"promoted {version}"
        logger.info("yt-dlp promoted to %s (%s)", self.settings.channel, version)
        return version

    def _promote_locked(self, version: str) -> None:
        previous = self._read_pointer(self.active_path)
        if previous and previous != version:
            self._write_pointer_atomic(self.previous_path, previous)
        # Atomic single-file swap: requests see old or new, never partial.
        self._write_pointer_atomic(self.active_path, f"versions/{version}")

    def rollback(self) -> str | None:
        """Restore the previous version (test/recovery aid); returns restored value."""
        with self._lock:
            previous = self._read_pointer(self.previous_path)
            if not previous:
                self._status_note = "rollback: no previous version recorded"
                logger.warning("yt-dlp rollback: no previous version recorded")
                return None
            self._write_pointer_atomic(self.active_path, previous)
            self._last_check_result = "rolled_back"
            self._status_note = f"rolled back to {previous}"
            logger.info("yt-dlp rolled back to %s", previous)
            return previous

    def force_bundled(self) -> None:
        """Point the active pointer back at the bundled (site-packages) copy."""
        with self._lock:
            previous = self._read_pointer(self.active_path)
            if previous and previous != "bundled":
                self._write_pointer_atomic(self.previous_path, previous)
            self._write_pointer_atomic(self.active_path, "bundled")
            self._status_note = "forced bundled version"
            logger.info("yt-dlp active version set to bundled")

    # ---- background thread -------------------------------------------------

    def start_background(self) -> None:
        """Start a non-blocking daemon updater thread (no-op unless enabled).

        Never blocks server startup: the thread is a daemon that runs its
        first check quickly and then sleeps for the configured interval. Any
        failure inside the loop is caught and logged; it cannot stop the MCP.
        """
        if not self.settings.enabled:
            logger.info("yt-dlp auto-update disabled; using bundled version")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._background_loop, name="ytdlp-updater", daemon=True)
        self._thread.start()

    def _background_loop(self) -> None:
        # Short initial delay lets the server finish starting and spreads
        # PyPI/YouTube load, then it runs at the configured interval.
        if self._stop.wait(min(30, self.settings.interval_seconds)):
            return
        while not self._stop.is_set():
            try:
                self.update_now()
            except Exception as exc:  # noqa: BLE001 - must never kill the server
                self._last_error = str(exc)
                self._last_check_result = "update_error"
                logger.exception("yt-dlp updater loop failure: %s", exc)
            if self._stop.wait(self.settings.interval_seconds):
                return

    def stop_background(self) -> None:
        self._stop.set()

    # ---- diagnostics --------------------------------------------------------

    def active_version(self) -> str:
        active = self._read_pointer(self.active_path)
        if active and active != "bundled":
            return os.path.basename(active)
        return "bundled"

    def versions(self) -> list[str]:
        try:
            names = os.listdir(self.versions_dir)
        except OSError:
            return []
        # Exclude staging/temp/intermediate entries (`.tmp-*`, dotfiles).
        return sorted(
            name for name in names
            if not name.startswith(".") and not name.startswith(".tmp-")
        )

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.enabled,
            "channel": self.settings.channel,
            "interval_seconds": self.settings.interval_seconds,
            "state_dir": self.settings.state_dir,
            "active_version": self.active_version(),
            "previous_version": self._read_pointer(self.previous_path),
            "versions_available": self.versions(),
            "smoke_test": self.settings.smoke_test,
            "last_check_iso8601": None if not self._last_check_us else time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_check_us)
            ),
            "last_check_result": self._last_check_result,
            "last_error": self._last_error,
            "status_note": self._status_note,
        }