from __future__ import annotations


class AirDetector:
    def __init__(self) -> None:
        self.enabled = False
        self._capture = None
        try:  # pragma: no cover - optional dependency
            import cv2  # noqa: F401

            self.enabled = True
        except Exception:
            self.enabled = False

    def update(self) -> bool:
        if not self.enabled:
            return False
        return False

    def close(self) -> None:
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass