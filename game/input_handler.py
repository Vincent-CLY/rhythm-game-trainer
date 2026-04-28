from __future__ import annotations

from dataclasses import dataclass

import pygame

from gpio_mock import GPIO


ZONE_PINS = {1: 17, 2: 18, 3: 27, 4: 22}


@dataclass(slots=True)
class InputEvent:
    zone: int | None = None
    pressed: bool = False
    is_air: bool = False
    timestamp_ms: int = 0


@dataclass(slots=True)
class InputSnapshot:
    events: list[InputEvent]
    quit_requested: bool = False


class InputHandler:
    def __init__(self) -> None:
        self._previous_gpio_state: dict[int, int] = {zone: GPIO.LOW for zone in ZONE_PINS}
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in ZONE_PINS.values():
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        except Exception:
            pass

    def poll(self, timestamp_ms: int) -> InputSnapshot:
        events: list[InputEvent] = []
        quit_requested = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                quit_requested = True
            elif event.type == pygame.KEYDOWN:
                zone = self._key_to_zone(event.key)
                if zone is not None:
                    events.append(InputEvent(zone=zone, pressed=True, timestamp_ms=timestamp_ms))
                elif event.key == pygame.K_SPACE:
                    events.append(InputEvent(is_air=True, pressed=True, timestamp_ms=timestamp_ms))
            elif event.type == pygame.KEYUP:
                zone = self._key_to_zone(event.key)
                if zone is not None:
                    events.append(InputEvent(zone=zone, pressed=False, timestamp_ms=timestamp_ms))
        events.extend(self._poll_gpio(timestamp_ms))
        return InputSnapshot(events=events, quit_requested=quit_requested)

    def _poll_gpio(self, timestamp_ms: int) -> list[InputEvent]:
        events: list[InputEvent] = []
        for zone, pin in ZONE_PINS.items():
            try:
                current_state = GPIO.input(pin)
            except Exception:
                continue
            previous_state = self._previous_gpio_state[zone]
            if current_state != previous_state:
                self._previous_gpio_state[zone] = current_state
                events.append(InputEvent(zone=zone, pressed=current_state == GPIO.HIGH, timestamp_ms=timestamp_ms))
        return events

    @staticmethod
    def _key_to_zone(key: int) -> int | None:
        key_to_zone = {
            pygame.K_1: 1,
            pygame.K_2: 2,
            pygame.K_3: 3,
            pygame.K_4: 4,
        }
        return key_to_zone.get(key)