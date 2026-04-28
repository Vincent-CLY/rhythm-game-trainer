from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import array

import pygame

from camera.air_detector import AirDetector
from data.analytics import generate_analytics
from data.recorder import SessionRecorder
from game.chart_parser import ChartNote, build_note_sequence, load_chart
from game.input_handler import InputEvent, InputHandler
from game.judgment import GOOD_WINDOW_MS, JudgmentResult, judge_timing


@dataclass(slots=True)
class GameConfig:
    bpm: int = 120
    chart_path: Path = Path("charts/sample_chart.json")
    fullscreen: bool = True
    width: int = 1280
    height: int = 720
    metronome_path: Path = Path("assets/sounds/metronome.wav")
    metronome_volume: float = 0.6


SLIDE_WINDOW_MS = 300
HOLD_MIN_MS = 300


@dataclass(slots=True)
class HoldState:
    note: ChartNote
    start_time_ms: int
    required_duration_ms: int
    timing_result: JudgmentResult


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
        self.small_font = pygame.font.SysFont("Arial", 22)
        self.chart = load_chart(config.chart_path)
        self.input_handler = InputHandler()
        self.air_detector = AirDetector()
        self.running = True
        self._session_complete = False
        self._session_saved = False
        self._active_holds: dict[int, HoldState] = {}
        self._recent_presses: list[tuple[int, int]] = []
        self._active_zones: set[int] = set()
        self._metronome_sound = self._load_sound(config.metronome_path)
        if self._metronome_sound is not None:
            try:
                self._metronome_sound.set_volume(config.metronome_volume)
            except Exception:
                pass
        self._beat_interval_ms = int(60000 / self.config.bpm) if self.config.bpm > 0 else 0
        self._next_beat_ms = 0
        self._reset_session()

    def _load_sound(self, path: Path) -> pygame.mixer.Sound | None:
        if path.exists():
            try:
                return pygame.mixer.Sound(str(path))
            except Exception:
                return None
        return self._generate_click_sound()

    def _generate_click_sound(self) -> pygame.mixer.Sound | None:
        init = pygame.mixer.get_init()
        if init is None:
            return None
        frequency, size, channels = init
        if abs(size) != 16 or channels < 1:
            return None
        duration_s = 0.03
        sample_count = max(1, int(frequency * duration_s))
        max_amplitude = (2 ** (abs(size) - 1)) - 1
        buffer = array.array("h")
        for index in range(sample_count):
            amplitude = int(max_amplitude * (1 - index / sample_count))
            for _ in range(channels):
                buffer.append(amplitude)
        try:
            return pygame.mixer.Sound(buffer=buffer)
        except Exception:
            return None

    def _reset_session(self) -> None:
        self.start_ticks = pygame.time.get_ticks()
        self.pending_notes = self._build_pending_notes()
        self.recorder = SessionRecorder()
        self.combo = 0
        self.last_judgment = "Ready"
        self._session_complete = False
        self._session_saved = False
        self._active_holds.clear()
        self._recent_presses.clear()
        self._active_zones.clear()
        self._next_beat_ms = 0
        self._total_notes = len(self.pending_notes)
        self._hit_notes = 0
        self._perfect_notes = 0
        print(f"Loaded {len(self.pending_notes)} notes from chart.")

    def _build_pending_notes(self) -> list[ChartNote]:
        notes = build_note_sequence(self.chart)
        scaled_notes: list[ChartNote] = []
        scale = (self.chart.bpm / self.config.bpm) if self.config.bpm > 0 else 1.0
        for note in notes:
            scaled_notes.append(
                ChartNote(
                    time_ms=int(note.time_ms * scale),
                    lane=note.lane,
                    note_type=note.note_type,
                    duration_ms=int(note.duration_ms * scale),
                    pattern_name=note.pattern_name,
                )
            )
        return sorted(scaled_notes, key=lambda note: note.time_ms)

    def run(self) -> None:
        try:
            print("GameEngine inner run loop started")
            snapshot = None
            while self.running:
                now_ms = pygame.time.get_ticks() - self.start_ticks
                snapshot = self.input_handler.poll(now_ms)
                self._active_zones = snapshot.active_zones
                self._handle_events(snapshot.events, snapshot.quit_requested)
                self._update(now_ms)
                self._draw(now_ms)
                pygame.display.flip()
                self.clock.tick(60)
            quit_requested = snapshot.quit_requested if snapshot is not None else False
            print(f"GameEngine inner loop exited normally. pending_notes={len(self.pending_notes)}, quit_requested={quit_requested}")
        finally:
            print("GameEngine shutdown sequence initiated.")
            self._shutdown()

    def _handle_events(self, events: list[InputEvent], quit_requested: bool) -> None:
        if quit_requested:
            self.running = False
            return
        for event in events:
            if event.action == "restart":
                if self._session_complete:
                    self._reset_session()
                continue
            if self._session_complete:
                continue
            if event.zone is not None:
                if event.pressed:
                    self._handle_zone_press(event.zone, event.timestamp_ms)
                else:
                    self._handle_zone_release(event.zone, event.timestamp_ms)
            elif event.is_air and event.pressed:
                self._handle_air_trigger(event.timestamp_ms)

    def _update(self, now_ms: int) -> None:
        self._tick_metronome(now_ms)
        if self.air_detector.update():
            self._handle_air_trigger(now_ms)

        self._update_active_holds(now_ms)
        self._expire_missed_notes(now_ms)

        if not self.pending_notes and not self._active_holds and not self._session_complete:
            self._session_complete = True
            self._finalize_session()

    def _handle_zone_press(self, zone: int, actual_time_ms: int) -> None:
        did_slide = self._detect_slide(zone, actual_time_ms)
        if did_slide and self._try_hit_slide(zone, actual_time_ms):
            return
        if self._try_start_hold(zone, actual_time_ms):
            return
        if self._try_hit_tap(zone, actual_time_ms):
            return
        self.last_judgment = "Miss"
        self.combo = 0

    def _handle_zone_release(self, zone: int, actual_time_ms: int) -> None:
        hold = self._active_holds.pop(zone, None)
        if hold is None:
            return
        duration_ms = actual_time_ms - hold.start_time_ms
        judgment = hold.timing_result.judgment if duration_ms >= hold.required_duration_ms else "Miss"
        self._record_note_result(
            hold.note,
            actual_time_ms=hold.start_time_ms,
            offset_ms=hold.timing_result.offset_ms,
            judgment=judgment,
            zone=zone,
        )

    def _handle_air_trigger(self, actual_time_ms: int) -> None:
        candidate = self._find_matching_note(actual_time_ms, note_types={"AIR"})
        if candidate is None:
            self.last_judgment = "Miss"
            self.combo = 0
            return
        result = judge_timing(candidate.time_ms, actual_time_ms)
        self._record_note_result(
            candidate,
            actual_time_ms=actual_time_ms,
            offset_ms=result.offset_ms,
            judgment=result.judgment,
            zone=candidate.lane,
        )
        self.pending_notes.remove(candidate)

    def _detect_slide(self, zone: int, timestamp_ms: int) -> bool:
        self._recent_presses = [
            (time_ms, lane)
            for time_ms, lane in self._recent_presses
            if timestamp_ms - time_ms <= SLIDE_WINDOW_MS
        ]
        slide_ready = any(lane != zone for _, lane in self._recent_presses)
        self._recent_presses.append((timestamp_ms, zone))
        return slide_ready

    def _try_hit_slide(self, zone: int, actual_time_ms: int) -> bool:
        candidate = self._find_matching_note(actual_time_ms, zone=zone, note_types={"SLIDE"})
        if candidate is None:
            return False
        result = judge_timing(candidate.time_ms, actual_time_ms)
        self._record_note_result(
            candidate,
            actual_time_ms=actual_time_ms,
            offset_ms=result.offset_ms,
            judgment=result.judgment,
            zone=zone,
        )
        self.pending_notes.remove(candidate)
        return True

    def _try_start_hold(self, zone: int, actual_time_ms: int) -> bool:
        candidate = self._find_matching_note(actual_time_ms, zone=zone, note_types={"HOLD"})
        if candidate is None:
            return False
        result = judge_timing(candidate.time_ms, actual_time_ms)
        required_duration_ms = max(HOLD_MIN_MS, candidate.duration_ms)
        self._active_holds[zone] = HoldState(
            note=candidate,
            start_time_ms=actual_time_ms,
            required_duration_ms=required_duration_ms,
            timing_result=result,
        )
        self.pending_notes.remove(candidate)
        self.last_judgment = "Hold"
        return True

    def _try_hit_tap(self, zone: int, actual_time_ms: int) -> bool:
        candidate = self._find_matching_note(actual_time_ms, zone=zone, note_types={"TAP"})
        if candidate is None:
            return False
        result = judge_timing(candidate.time_ms, actual_time_ms)
        self._record_note_result(
            candidate,
            actual_time_ms=actual_time_ms,
            offset_ms=result.offset_ms,
            judgment=result.judgment,
            zone=zone,
        )
        self.pending_notes.remove(candidate)
        return True

    def _find_matching_note(
        self,
        actual_time_ms: int,
        zone: int | None = None,
        note_types: set[str] | None = None,
    ) -> ChartNote | None:
        best_note = None
        best_offset = None
        for note in self.pending_notes:
            if zone is not None and note.lane != zone:
                continue
            if note_types is not None and note.note_type not in note_types:
                continue
            offset = abs(note.time_ms - actual_time_ms)
            if offset > GOOD_WINDOW_MS:
                continue
            if best_note is None or (best_offset is not None and offset < best_offset):
                best_note = note
                best_offset = offset
        return best_note

    def _record_note_result(
        self,
        note: ChartNote,
        actual_time_ms: int,
        offset_ms: int,
        judgment: str,
        zone: int,
    ) -> None:
        if judgment == "Miss":
            self.combo = 0
        else:
            self.combo += 1
        self.last_judgment = judgment
        self.recorder.record(
            session_id=self.recorder.session_id,
            timestamp=self.recorder.session_timestamp(),
            pattern_name=note.pattern_name,
            note_type=note.note_type,
            zone=zone,
            expected_time=note.time_ms,
            actual_time=actual_time_ms,
            offset_ms=offset_ms,
            judgment=judgment,
            bpm=self.config.bpm,
            combo=self.combo,
        )
        if judgment in {"Perfect", "Good"}:
            self._hit_notes += 1
        if judgment == "Perfect":
            self._perfect_notes += 1

    def _update_active_holds(self, now_ms: int) -> None:
        for zone, hold in list(self._active_holds.items()):
            if now_ms - hold.start_time_ms >= hold.required_duration_ms and zone in self._active_zones:
                self._record_note_result(
                    hold.note,
                    actual_time_ms=hold.start_time_ms,
                    offset_ms=hold.timing_result.offset_ms,
                    judgment=hold.timing_result.judgment,
                    zone=zone,
                )
                self._active_holds.pop(zone, None)

    def _tick_metronome(self, now_ms: int) -> None:
        if self._metronome_sound is None or self._beat_interval_ms <= 0:
            return
        while now_ms >= self._next_beat_ms:
            try:
                self._metronome_sound.play()
            except Exception:
                break
            self._next_beat_ms += self._beat_interval_ms

    def _finalize_session(self) -> None:
        if self._session_saved:
            return
        try:
            self.recorder.save()
            generate_analytics()
        except Exception:
            pass
        self._session_saved = True

    def _expire_missed_notes(self, now_ms: int) -> None:
        expired_notes = [note for note in self.pending_notes if now_ms > note.time_ms + GOOD_WINDOW_MS]
        if expired_notes:
            print(f"Expiring {len(expired_notes)} notes at now_ms={now_ms}")
        for note in expired_notes:
            self._record_note_result(
                note,
                actual_time_ms=now_ms,
                offset_ms=now_ms - note.time_ms,
                judgment="Miss",
                zone=note.lane,
            )
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
            color = {
                "TAP": (135, 206, 250),
                "HOLD": (80, 200, 255),
                "SLIDE": (80, 220, 140),
                "AIR": (200, 160, 255),
            }.get(note.note_type, (200, 200, 200))
            if note.note_type == "HOLD":
                pygame.draw.rect(self.screen, color, (x - 16, y - 12, 32, 24), border_radius=6)
            elif note.note_type == "AIR":
                points = [(x, y - 18), (x - 16, y + 12), (x + 16, y + 12)]
                pygame.draw.polygon(self.screen, color, points)
            else:
                note_rect = pygame.Rect(x - 24, y - 12, 48, 24)
                pygame.draw.rect(self.screen, color, note_rect, border_radius=8)
                pygame.draw.rect(self.screen, (255, 255, 255), note_rect, width=2, border_radius=8)
        label = self.font.render(f"BPM {self.config.bpm} | {self.last_judgment} | Combo {self.combo}", True, (240, 240, 240))
        self.screen.blit(label, (24, 24))
        if self._total_notes > 0:
            accuracy = (self._hit_notes / self._total_notes) * 100.0
            perfect_rate = (self._perfect_notes / self._total_notes) * 100.0
            stats = self.small_font.render(
                f"Accuracy {accuracy:.1f}% | Perfect {perfect_rate:.1f}%",
                True,
                (200, 200, 200),
            )
            self.screen.blit(stats, (24, 60))
        if self._session_complete:
            message = self.font.render("Session complete", True, (240, 220, 140))
            hint = self.small_font.render("Press R to restart or ESC to quit", True, (200, 200, 200))
            self.screen.blit(message, (24, 100))
            self.screen.blit(hint, (24, 132))

    def _shutdown(self) -> None:
        self._finalize_session()
        try:
            self.air_detector.close()
        except Exception:
            pass
        try:
            self.input_handler.close()
        except Exception:
            pass
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        pygame.quit()