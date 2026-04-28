from __future__ import annotations

import time


class AirDetector:
    def __init__(self) -> None:
        self.enabled = False
        self._capture = None
        self._cv2 = None
        self._prev_gray = None
        self._last_trigger = 0.0
        self._cooldown_s = 0.3
        self._motion_threshold = 12.0
        try:  # pragma: no cover - optional dependency
            import cv2

            capture = cv2.VideoCapture(0)
            if capture.isOpened():
                self._capture = capture
                self._cv2 = cv2
                self.enabled = True
        except Exception:
            self.enabled = False

    def update(self) -> bool:
        if not self.enabled:
            return False
        if self._capture is None:
            return False
        try:
            ok, frame = self._capture.read()
            if not ok or frame is None:
                return False
            gray = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2GRAY)
            if self._prev_gray is None:
                self._prev_gray = gray
                return False
            diff = self._cv2.absdiff(gray, self._prev_gray)
            self._prev_gray = gray
            motion_score = float(diff.mean())
            now = time.monotonic()
            if motion_score > self._motion_threshold and now - self._last_trigger >= self._cooldown_s:
                self._last_trigger = now
                return True
        except Exception:
            return False
        return False

    def close(self) -> None:
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass