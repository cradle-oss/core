"""Headless Chrome-family renderer: DOM + screenshot.

Probe for an installed Chrome/Brave/Chromium binary and drive it via
subprocess (`--headless=new --dump-dom --screenshot`). Absent browsers
degrade gracefully: web_render reports "renderer unavailable", and the
entire Web subsystem never depends on rendering.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

STORE_LIMIT = 400_000
KNOWN_BINARIES = [
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
WHICH_NAMES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "chrome",
]


@dataclass
class RenderResult:
    dom: str
    dom_truncated: bool
    screenshot_path: str | None
    screenshot_dims: tuple[int, int] | None
    binary: str
    elapsed_ms: int
    exit_code: int
    error: str | None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


class WebRenderer:
    """Drive headless Chromium when a binary is available."""

    def __init__(self, timeout: int = 60, store_limit: int = STORE_LIMIT) -> None:
        self.timeout = timeout
        self.store_limit = store_limit
        self._binary: str | None = None
        self._probed = False

    def _probe(self) -> str | None:
        for p in KNOWN_BINARIES:
            if shutil.which(p):
                return p
        for name in WHICH_NAMES:
            path = shutil.which(name)
            if path:
                return path
        return None

    def available(self) -> bool:
        if not self._probed:
            self._binary = self._probe()
            self._probed = True
        return self._binary is not None

    def binary_info(self) -> str:
        if not self._probed:
            self._binary = self._probe()
            self._probed = True
        if self._binary:
            return f"renderer: {self._binary}"
        return (
            "renderer: unavailable "
            "(install a Chrome-family browser for DOM + screenshot)"
        )

    BASE_FLAGS = [
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    def _attempt(
        self,
        url: str,
        *,
        use_screenshot: bool,
        use_time_budget: bool,
        wait_ms: int,
    ) -> tuple[subprocess.CompletedProcess | None, str | None, str | None]:
        """Run one headless attempt.

        Returns (completed_process, screenshot_path, invocation_error).
        Some Chrome-family builds reject `--virtual-time-budget` or
        `--screenshot` when combined with `--no-sandbox`/`--disable-dev-shm-usage`
        ("Multiple targets are not supported in headless mode"). We try the
        richest flag set first, then degrade honestly rather than failing.
        """
        assert self._binary is not None
        cmd = [self._binary, *self.BASE_FLAGS]
        if use_time_budget:
            cmd += ["--virtual-time-budget", str(wait_ms)]
        out_file = None
        if use_screenshot:
            out_file = tempfile.NamedTemporaryFile(
                suffix=".png", prefix="core_render_", delete=False
            )
            out_file.close()
            cmd += ["--screenshot", out_file.name]
        cmd += ["--dump-dom", url]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout + 10,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            if out_file is not None:
                self._unlink(out_file.name)
            return None, None, f"headless invocation failed: {exc}"
        return result, (out_file.name if out_file is not None else None), None

    @staticmethod
    def _unlink(path: str | None) -> None:
        if not path:
            return
        import os

        try:
            os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _multiple_targets(stderr: str) -> bool:
        return "Multiple targets are not supported" in stderr

    @staticmethod
    def _screenshot_from(path: str | None) -> tuple[str | None, tuple[int, int] | None]:
        if not path:
            return None, None
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return None, None
        if len(data) >= 24 and data[:4] == b"\x89PNG":
            return path, (
                struct.unpack(">I", data[16:20])[0],
                struct.unpack(">I", data[20:24])[0],
            )
        return None, None

    def render(
        self,
        url: str,
        *,
        screenshot: bool = False,
        wait_ms: int = 5000,
    ) -> RenderResult:
        if not self.available():
            return RenderResult(
                dom="",
                dom_truncated=False,
                screenshot_path=None,
                screenshot_dims=None,
                binary="",
                elapsed_ms=0,
                exit_code=1,
                error="no Chrome-family browser found; install Chrome/Brave/Chromium "
                "to use the renderer (DOM fetch + screenshot).",
            )

        t0 = time.monotonic()
        notes: list[str] = []

        # Attempt 1: richest flags (time budget + optional screenshot).
        result, shot, invoke_err = self._attempt(
            url,
            use_screenshot=screenshot,
            use_time_budget=True,
            wait_ms=wait_ms,
        )
        if invoke_err is not None:
            return RenderResult(
                dom="",
                dom_truncated=False,
                screenshot_path=None,
                screenshot_dims=None,
                binary=self._binary or "",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                exit_code=1,
                error=invoke_err,
            )
        assert result is not None
        stderr = (result.stderr or "").strip()

        # Degrade on the known "Multiple targets" quirk: drop time budget, then
        # screenshot, retrying with whatever still yields a rendered DOM.
        if (
            result.returncode != 0
            and not result.stdout
            and self._multiple_targets(stderr)
        ):
            if shot is not None:
                self._unlink(shot)
                shot = None
            notes.append(
                "time budget/--screenshot unsupported by this browser build; "
                "retried with --dump-dom only"
            )
            result, shot, invoke_err = self._attempt(
                url,
                use_screenshot=screenshot,
                use_time_budget=False,
                wait_ms=wait_ms,
            )
            if invoke_err is None and result is not None and result.returncode != 0:
                if shot is not None and self._screenshot_from(shot)[0] is None:
                    self._unlink(shot)
                    shot = None
                    notes.append(
                        "screenshot capture is not supported by this browser "
                        "build; DOM render still returned"
                    )
                    result, shot, invoke_err = self._attempt(
                        url,
                        use_screenshot=False,
                        use_time_budget=False,
                        wait_ms=wait_ms,
                    )

        elapsed = int((time.monotonic() - t0) * 1000)
        if result is None:
            return RenderResult(
                dom="",
                dom_truncated=False,
                screenshot_path=None,
                screenshot_dims=None,
                binary=self._binary or "",
                elapsed_ms=elapsed,
                exit_code=1,
                error=invoke_err or "headless invocation failed",
            )

        dom = result.stdout[: self.store_limit]
        truncated = len(result.stdout) > self.store_limit
        screenshot_path, screenshot_dims = self._screenshot_from(shot)
        if screenshot and screenshot_path is None:
            notes.append("screenshot requested but not captured by this browser build")
        return RenderResult(
            dom=dom,
            dom_truncated=truncated,
            screenshot_path=screenshot_path,
            screenshot_dims=screenshot_dims,
            binary=self._binary or "",
            elapsed_ms=elapsed,
            exit_code=result.returncode,
            error=(f"headless exited {result.returncode}: {stderr or 'no stderr'}")
            if result.returncode != 0 and not dom
            else None,
            notes=notes,
        )
