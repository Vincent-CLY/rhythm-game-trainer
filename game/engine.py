from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pygame

from camera.air_detector import AirDetector
from data.analytics import generate_analytics
from data.recorder import SessionRecorder
from game.chart_parser import ChartNote, load_chart
from game.input_handler import InputEvent, InputHandler
from game.judgment import judge_timing


@dataclass(slots=True)
class GameConfig:
    bpm: int = 120
    chart_path: Path = Path("charts/sample_chart.json")
    fullscreen: bool = True
    width: int = 1280
    height: int = 720


class GameEngine:
    def __init__(self, config: GameConfig) -> None:
        self.config = config
        pygame.init()
        try:
            pygame.mixer.init()
        except Exception:
            pass
        flags = pygame.FULLSCREEN if config.fullscreen else 0
        self.screen = pygame.display.set_mode((config.width, config.height), flags)
        pygame.display.set_caption("Rhythm Game Trainer")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Arial", 28)
        self.chart = load_chart(config.chart_path)
        self.input_handler = InputHandler()
        self.air_detector = AirDetector()
        self.recorder = SessionRecorder()
        self.running = True
        self.start_ticks = pygame.time.get_ticks()
        self.pending_notes: list[ChartNote] = sorted(
            [note for pattern in self.chart.patterns for note in pattern.notes],
            key=lambda note: note.time_ms,
        )
        self.combo = 0
        self.last_judgment = "Ready"

    def run(self) -> None:
        try:
            print("GameEngine inner run loop started")
            while self.running:
                now_ms = pygame.time.get_ticks() - self.start_ticks
                snapshot = self.input_handler.poll(now_ms)
                self._handle_events(snapshot.events, snapshot.quit_requested)
                self._update(now_ms)
                self._draw(now_ms)
                pygame.display.flip()
                self.clock.tick(60)
            print(f"GameEngine inner loop exited normally. pending_notes={len(self.pending_notes)}, quit_requested={snapshot.quit_requested}")
        finally:
            print("GameEngine shutdown sequence initiated.")
            self._shutdown()

    def _handle_events(self, events: list[InputEvent], quit_requested: bool) -> None:
        if quit_requested:
            self.running = False
            return
        for event in events:
            if event.zone is not None and event.pressed:
                self._judge_zone_press(event.zone, event.timestamp_ms)
            elif event.is_air and event.pressed:
                self.last_judgment = "AIR"

    def _update(self, now_ms: int) -> None:
        if self.air_detector.update():
            self.last_judgment = "AIR"

        self._expire_missed_notes(now_ms)

        if not self.pending_notes:
            self.running = False

    def _judge_zone_press(self, zone: int, actual_time_ms: int) -> None:
        candidate = self._next_note_for_zone(zone)
        if candidate is None:
            self.last_judgment = "Miss"
            self.combo = 0
            return
        result = judge_timing(candidate.time_ms, actual_time_ms)
        self.last_judgment = result.judgment
        self.combo = self.combo + 1 if result.judgment != "Miss" else 0
        self.recorder.record(
            session_id=self.recorder.session_id,
            timestamp=self.recorder.session_timestamp(),
            pattern_name=candidate.pattern_name,
            note_type=candidate.note_type,
            zone=zone,
            expected_time=candidate.time_ms,
            actual_time=actual_time_ms,
            offset_ms=result.offset_ms,
            judgment=result.judgment,
            bpm=self.config.bpm,
            combo=self.combo,
        )
        if candidate in self.pending_notes:
            self.pending_notes.remove(candidate)

    def _next_note_for_zone(self, zone: int) -> ChartNote | None:
        for note in self.pending_notes:
            if note.lane == zone:
                return note
        return None

    def _expire_missed_notes(self, now_ms: int) -> None:
        expired_notes = [note for note in self.pending_notes if now_ms > note.time_ms + 100]
        for note in expired_notes:
            self.recorder.record(
                session_id=self.recorder.session_id,
                timestamp=self.recorder.session_timestamp(),
                pattern_name=note.pattern_name,
                note_type=note.note_type,
                zone=note.lane,
                expected_time=note.time_ms,
                actual_time=now_ms,
                offset_ms=now_ms - note.time_ms,
                judgment="Miss",
                bpm=self.config.bpm,
                combo=0,
            )
            self.last_judgment = "Miss"
            self.combo = 0
            self.pending_notes.remove(note)

    def _draw(self, now_ms: int) -> None:
        self.screen.fill((12, 12, 18))
        lane_width = self.config.width // 4
        for index in range(4):
            x = index * lane_width
            pygame.draw.rect(self.screen, (32, 32, 44), (x, 0, lane_width - 2, self.config.height))
            pygame.draw.rect(self.screen, (70, 70, 92), (x, self.config.height - 120, lane_width - 2, 10))
        for note in self.pending_notes[:12]:
            travel_progress = max(0, min(1, (now_ms - note.time_ms + 2000) / 2000))
            y = int(80 + travel_progress * (self.config.height - 220))
            x = (note.lane - 1) * lane_width + lane_width // 2 - 18
            color = (240, 180, 40) if note.note_type == "TAP" else (80, 200, 255)
            pygame.draw.circle(self.screen, color, (x, y), 18)
        label = self.font.render(f"BPM {self.config.bpm} | {self.last_judgment} | Combo {self.combo}", True, (240, 240, 240))
        self.screen.blit(label, (24, 24))

    def _shutdown(self) -> None:
        try:
            self.recorder.save()
            generate_analytics()
        except Exception:
            pass
        try:
            self.air_detector.close()
        except Exception:
            pass
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        pygame.quit()