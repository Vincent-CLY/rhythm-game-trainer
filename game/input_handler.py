from __future__ import annotations
from dataclasses import dataclass, field
import pygame
import lgpio

SENSORS = {
    "Sensor 1": 22,
    "Sensor 2": 17,
    "Sensor 3": 27,
    "Sensor 4": 4,
}

ZONE_PINS = {
    1: SENSORS["Sensor 1"],
    2: SENSORS["Sensor 2"],
    3: SENSORS["Sensor 3"],
    4: SENSORS["Sensor 4"],
}

# Open GPIO chip once at module level
_GPIO_HANDLE = lgpio.gpiochip_open(0)
for _pin in ZONE_PINS.values():
    lgpio.gpio_claim_input(_GPIO_HANDLE, _pin)


@dataclass(slots=True)
class InputEvent:
    zone: int | None = None
    pressed: bool = False
    is_air: bool = False
    timestamp_ms: int = 0
    action: str | None = None


@dataclass(slots=True)
class InputSnapshot:
    events: list[InputEvent]
    quit_requested: bool = False
    active_zones: set[int] = field(default_factory=set)


class InputHandler:
    def __init__(self) -> None:
        self._previous_gpio_state: dict[int, int] = {zone: 0 for zone in ZONE_PINS}
        self._active_zones: set[int] = set()

    def poll(self, timestamp_ms: int) -> InputSnapshot:
        events: list[InputEvent] = []
        quit_requested = False

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                quit_requested = True
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE:
                    events.append(InputEvent(is_air=True, pressed=True, timestamp_ms=timestamp_ms))
                elif event.key in (pygame.K_UP, pygame.K_w):
                    events.append(InputEvent(action="menu_up", timestamp_ms=timestamp_ms))
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    events.append(InputEvent(action="menu_down", timestamp_ms=timestamp_ms))
                elif event.key in (pygame.K_LEFT, pygame.K_a):
                    events.append(InputEvent(action="menu_left", timestamp_ms=timestamp_ms))
                elif event.key in (pygame.K_RIGHT, pygame.K_d):
                    events.append(InputEvent(action="menu_right", timestamp_ms=timestamp_ms))
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    events.append(InputEvent(action="menu_select", timestamp_ms=timestamp_ms))
                elif event.key == pygame.K_BACKSPACE:
                    events.append(InputEvent(action="menu_back", timestamp_ms=timestamp_ms))
                elif event.key in (pygame.K_ESCAPE, pygame.K_q):
                    quit_requested = True
                elif event.key == pygame.K_r:
                    events.append(InputEvent(action="restart", timestamp_ms=timestamp_ms))

        events.extend(self._poll_gpio(timestamp_ms))
        return InputSnapshot(
            events=events,
            quit_requested=quit_requested,
            active_zones=set(self._active_zones)
        )

    def _poll_gpio(self, timestamp_ms: int) -> list[InputEvent]:
        events: list[InputEvent] = []
        for zone, pin in ZONE_PINS.items():
            try:
                current_state = lgpio.gpio_read(_GPIO_HANDLE, pin)
            except Exception:
                continue

            previous_state = self._previous_gpio_state[zone]

            if current_state == 1:
                self._active_zones.add(zone)
            else:
                self._active_zones.discard(zone)

            if current_state != previous_state:
                self._previous_gpio_state[zone] = current_state
                events.append(InputEvent(
                    zone=zone,
                    pressed=(current_state == 1),
                    timestamp_ms=timestamp_ms
                ))
        return events

    def close(self) -> None:
        try:
            lgpio.gpiochip_close(_GPIO_HANDLE)
        except Exception:
            pass