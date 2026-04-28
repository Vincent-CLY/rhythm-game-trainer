# camera/air_detector.py
from __future__ import annotations

import time
from pathlib import Path


class AirDetector:
    """
    Motion-based air trigger detector using Raspberry Pi picamera2.

    Install: sudo apt install -y python3-picamera2 python3-numpy
    """

    def __init__(self) -> None:
        self.enabled = False
        self._picam = None
        self._np = None
        self._prev_gray = None
        self._last_trigger = 0.0
        self._cooldown_s = 0.3
        self._motion_threshold = 12.0

        try:  # pragma: no cover - optional hardware dependency
            import numpy as np
            from picamera2 import Picamera2  # type: ignore[import]
            picam = Picamera2()
            config = picam.create_preview_configuration(
                main={"size": (320, 240), "format": "RGB888"}
            )
            picam.configure(config)
            picam.start()
            self._picam = picam
            self._np = np
            self.enabled = True
        except Exception:
            self.enabled = False

    # ─── MOTION DETECTION ────────────────────────────────────────────────────

    def update(self) -> bool:
        """Return True if a motion trigger is detected this frame."""
        if not self.enabled or self._picam is None or self._np is None:
            return False
        np = self._np
        try:
            frame = self._picam.capture_array()
            gray = np.mean(frame[:, :, :3], axis=2).astype(np.uint8)
            if self._prev_gray is None:
                self._prev_gray = gray
                return False
            diff = np.abs(gray.astype(np.int16) - self._prev_gray.astype(np.int16))
            self._prev_gray = gray
            motion_score = float(diff.mean())
            now = time.monotonic()
            if motion_score > self._motion_threshold and now - self._last_trigger >= self._cooldown_s:
                self._last_trigger = now
                return True
        except Exception:
            return False
        return False

    # ─── STILL CAPTURE ───────────────────────────────────────────────────────

    def capture_still(self, path: Path) -> bool:
        """
        Save a JPEG still to *path*.
        Returns True on success, False if picamera2 is unavailable or capture fails.
        """
        if not self.enabled or self._picam is None:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._picam.capture_file(str(path))
            return True
        except Exception:
            return False

    # ─── CLEANUP ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._picam is not None:
            try:
                self._picam.stop()
                self._picam.close()
            except Exception:
                pass