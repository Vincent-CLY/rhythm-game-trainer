from __future__ import annotations

from typing import Dict


class _MockGPIO:
    BCM = "BCM"
    BOARD = "BOARD"
    IN = "IN"
    OUT = "OUT"
    PUD_UP = "PUD_UP"
    PUD_DOWN = "PUD_DOWN"
    HIGH = 1
    LOW = 0

    def __init__(self) -> None:
        self._mode = self.BCM
        self._warnings = False
        self._pin_state: Dict[int, int] = {}

    def setmode(self, mode: str) -> None:
        self._mode = mode

    def setwarnings(self, enabled: bool) -> None:
        self._warnings = enabled

    def setup(self, pin: int, mode: str, pull_up_down: str | None = None) -> None:
        self._pin_state.setdefault(pin, self.LOW)

    def input(self, pin: int) -> int:
        return self._pin_state.get(pin, self.LOW)

    def output(self, pin: int, value: int) -> None:
        self._pin_state[pin] = value

    def cleanup(self) -> None:
        self._pin_state.clear()

    def simulate_input(self, pin: int, value: int) -> None:
        self._pin_state[pin] = value


try:  # pragma: no cover - exercised on Raspberry Pi
    import RPi.GPIO as _real_gpio

    GPIO = _real_gpio
except Exception:  # pragma: no cover - exercised on non-Pi platforms
    GPIO = _MockGPIO()


def get_gpio() -> object:
    return GPIO