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
from game.judgment import MAX_JUDGE_WINDOW_MS, JudgmentResult, judge_timing


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
NOTE_TRAVEL_MS = 2000
JUDGMENT_LINE_OFFSET = 120
JUDGMENT_DISPLAY_MS = 900
NOTE_HEIGHT = 40

NOTE_TRAVEL_MIN_MS = 800
NOTE_TRAVEL_MAX_MS = 5000
NOTE_TRAVEL_STEP_MS = 100
INPUT_OFFSET_MIN_MS = -300
INPUT_OFFSET_MAX_MS = 300
INPUT_OFFSET_STEP_MS = 10

HOME_MENU = (
    "Mix & Match Quick Start",
    "Practice Pattern",
    "Performance",
    "Settings",
    "Quit",
)
RESULTS_MENU = (
    "Retry",
    "Detailed Performance",
    "Return Home",
)
SETTINGS_MENU = (
    "Note Travel (ms)",
    "Input Offset (ms)",
    "Back",
)


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
        if config.fullscreen:
            display_info = pygame.display.Info()
            flags = pygame.FULLSCREEN | pygame.SCALED
            target_size = (display_info.current_w, display_info.current_h)
        else:
            flags = 0
            target_size = (config.width, config.height)
        self.screen = pygame.display.set_mode(target_size, flags)
        config.width, config.height = self.screen.get_size()
        pygame.display.set_caption("Rhythm Game Trainer")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Arial", 28)
        self.small_font = pygame.font.SysFont("Arial", 22)
        self.judgment_font = pygame.font.SysFont("Arial", 64, bold=True)
        self.title_font = pygame.font.SysFont("Arial", 46, bold=True)
        self.chart = load_chart(config.chart_path)
        self.input_handler = InputHandler()
        self.air_detector = AirDetector()
        self.running = True
        self._ui_state = "home"
        self._home_index = 0
        self._practice_index = 0
        self._results_index = 0
        self._settings_index = 0
        self._practice_pattern: str | None = None
        self._current_mode = "quick"
        self._last_session_summary: dict[str, object] | None = None
        self._judgment_counts: dict[str, int] = {}
        self._session_complete = False
        self._session_saved = False
        self._active_holds: dict[int, HoldState] = {}
        self._recent_presses: list[tuple[int, int]] = []
        self._active_zones: set[int] = set()
        self._last_judgment_ms = -JUDGMENT_DISPLAY_MS
        self.note_travel_ms = NOTE_TRAVEL_MS
        self.input_offset_ms = 0
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
        self.last_judgment = ""
        self._last_judgment_ms = -JUDGMENT_DISPLAY_MS
        self._session_complete = False
        self._session_saved = False
        self._active_holds.clear()
        self._recent_presses.clear()
        self._active_zones.clear()
        self._next_beat_ms = 0
        self._total_notes = len(self.pending_notes)
        self._hit_notes = 0
        self._perfect_notes = 0
        self._judgment_counts = {}
        print(f"Loaded {len(self.pending_notes)} notes from chart.")

    def _build_pending_notes(self) -> list[ChartNote]:
        if self._practice_pattern:
            notes = self._build_practice_notes(self._practice_pattern)
        else:
            notes = build_note_sequence(self.chart)
        scaled_notes: list[ChartNote] = []
        scale = (self.chart.bpm / self.config.bpm) if self.config.bpm > 0 else 1.0
        for note in notes:
            scaled_notes.append(
                ChartNote(
                    time_ms=int(note.time_ms * scale),
                    lane=note.lane,
                    note_type="TAP",
                    duration_ms=int(note.duration_ms * scale),
                    pattern_name=note.pattern_name,
                )
            )
        return sorted(scaled_notes, key=lambda note: note.time_ms)

    def _build_practice_notes(self, pattern_name: str) -> list[ChartNote]:
        pattern = next((item for item in self.chart.patterns if item.name == pattern_name), None)
        if pattern is None or not pattern.notes:
            return []
        training = self.chart.training
        repeats = training.pattern_repeats if training is not None else 4
        gap_ms = training.pattern_gap_ms if training is not None else 400
        lead_in_ms = training.lead_in_ms if training is not None else 1000
        min_time = min(note.time_ms for note in pattern.notes)
        max_time = max(note.time_ms for note in pattern.notes)
        duration = max(0, max_time - min_time)
        sequence: list[ChartNote] = []
        cursor_ms = lead_in_ms
        for _ in range(repeats):
            for note in pattern.notes:
                sequence.append(
                    ChartNote(
                        time_ms=note.time_ms - min_time + cursor_ms,
                        lane=note.lane,
                        note_type="TAP",
                        duration_ms=note.duration_ms,
                        pattern_name=pattern.name,
                    )
                )
            cursor_ms += duration + gap_ms
        return sequence

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

    def _start_session(self, mode: str, practice_pattern: str | None) -> None:
        self._current_mode = mode
        self._practice_pattern = practice_pattern
        self._session_complete = False
        self._session_saved = False
        self._reset_session()
        self._ui_state = "play"

    def _end_session(self) -> None:
        if self._session_complete:
            return
        self._session_complete = True
        self._finalize_session()
        self._last_session_summary = self._build_session_summary()
        self._results_index = 0
        self._ui_state = "results"

    def _build_session_summary(self) -> dict[str, object]:
        rows = self.recorder.rows
        total = len(rows)
        counts = dict(self._judgment_counts)
        hits = total - counts.get("Miss", 0)
        clean_hits = sum(counts.get(item, 0) for item in ("Perfect", "Great", "Good"))
        hit_rate = (hits / total) * 100.0 if total > 0 else 0.0
        clean_rate = (clean_hits / total) * 100.0 if total > 0 else 0.0
        pattern_stats: dict[str, dict[str, object]] = {}
        for row in rows:
            pattern = str(row.get("pattern_name", "Unknown") or "Unknown")
            judgment = str(row.get("judgment", ""))
            stats = pattern_stats.setdefault(pattern, {"total": 0, "hits": 0, "clean": 0})
            stats["total"] += 1
            if judgment != "Miss":
                stats["hits"] += 1
            if judgment in {"Perfect", "Great", "Good"}:
                stats["clean"] += 1
        for stats in pattern_stats.values():
            total_notes = stats["total"]
            stats["hit_rate"] = (stats["hits"] / total_notes) * 100.0 if total_notes else 0.0
            stats["clean_rate"] = (stats["clean"] / total_notes) * 100.0 if total_notes else 0.0
        return {
            "total": total,
            "hit_rate": hit_rate,
            "clean_rate": clean_rate,
            "counts": counts,
            "pattern_stats": pattern_stats,
        }

    def _handle_events(self, events: list[InputEvent], quit_requested: bool) -> None:
        if quit_requested:
            self.running = False
            return
        for event in events:
            if self._ui_state != "play":
                if event.action == "restart" and self._ui_state == "results":
                    self._start_session(self._current_mode, self._practice_pattern)
                self._handle_menu_input(event)
                continue
            if event.action == "restart":
                self._start_session(self._current_mode, self._practice_pattern)
                continue
            if event.zone is not None:
                if event.pressed:
                    self._handle_zone_press(event.zone, event.timestamp_ms)
                else:
                    self._handle_zone_release(event.zone, event.timestamp_ms)

    def _handle_menu_input(self, event: InputEvent) -> None:
        action = None
        if event.action in {
            "menu_up",
            "menu_down",
            "menu_left",
            "menu_right",
            "menu_select",
            "menu_back",
        }:
            action = event.action
        elif event.zone is not None and event.pressed:
            if self._ui_state == "settings":
                mapping = {
                    1: "menu_left",
                    2: "menu_right",
                    3: "menu_down",
                    4: "menu_back",
                }
            else:
                mapping = {
                    1: "menu_up",
                    2: "menu_down",
                    3: "menu_select",
                    4: "menu_back",
                }
            action = mapping.get(event.zone)
        if action is None:
            return
        if self._ui_state == "home":
            self._handle_home_menu_action(action)
        elif self._ui_state == "practice_select":
            self._handle_practice_menu_action(action)
        elif self._ui_state == "settings":
            self._handle_settings_menu_action(action)
        elif self._ui_state == "results":
            self._handle_results_menu_action(action)
        elif self._ui_state == "performance":
            if action in {"menu_back", "menu_select"}:
                self._ui_state = "home"

    def _handle_home_menu_action(self, action: str) -> None:
        if action == "menu_up":
            self._home_index = (self._home_index - 1) % len(HOME_MENU)
        elif action == "menu_down":
            self._home_index = (self._home_index + 1) % len(HOME_MENU)
        elif action == "menu_select":
            selection = HOME_MENU[self._home_index]
            if selection == "Mix & Match Quick Start":
                self._start_session("quick", None)
            elif selection == "Practice Pattern":
                self._practice_index = 0
                self._ui_state = "practice_select"
            elif selection == "Performance":
                self._ui_state = "performance"
            elif selection == "Settings":
                self._settings_index = 0
                self._ui_state = "settings"
            elif selection == "Quit":
                self.running = False
        elif action == "menu_back":
            self.running = False

    def _handle_practice_menu_action(self, action: str) -> None:
        items = self._practice_menu_items()
        if action == "menu_up":
            self._practice_index = (self._practice_index - 1) % len(items)
        elif action == "menu_down":
            self._practice_index = (self._practice_index + 1) % len(items)
        elif action == "menu_select":
            selection = items[self._practice_index]
            if selection == "Back":
                self._ui_state = "home"
            else:
                self._start_session("practice", selection)
        elif action == "menu_back":
            self._ui_state = "home"

    def _handle_settings_menu_action(self, action: str) -> None:
        if action == "menu_up":
            self._settings_index = (self._settings_index - 1) % len(SETTINGS_MENU)
        elif action == "menu_down":
            self._settings_index = (self._settings_index + 1) % len(SETTINGS_MENU)
        elif action in {"menu_left", "menu_right"}:
            delta = -1 if action == "menu_left" else 1
            if self._settings_index == 0:
                self.note_travel_ms = max(
                    NOTE_TRAVEL_MIN_MS,
                    min(NOTE_TRAVEL_MAX_MS, self.note_travel_ms + (delta * NOTE_TRAVEL_STEP_MS)),
                )
            elif self._settings_index == 1:
                self.input_offset_ms = max(
                    INPUT_OFFSET_MIN_MS,
                    min(INPUT_OFFSET_MAX_MS, self.input_offset_ms + (delta * INPUT_OFFSET_STEP_MS)),
                )
        elif action == "menu_select":
            if SETTINGS_MENU[self._settings_index] == "Back":
                self._ui_state = "home"
        elif action == "menu_back":
            self._ui_state = "home"

    def _handle_results_menu_action(self, action: str) -> None:
        if action == "menu_up":
            self._results_index = (self._results_index - 1) % len(RESULTS_MENU)
        elif action == "menu_down":
            self._results_index = (self._results_index + 1) % len(RESULTS_MENU)
        elif action == "menu_select":
            selection = RESULTS_MENU[self._results_index]
            if selection == "Retry":
                self._start_session(self._current_mode, self._practice_pattern)
            elif selection == "Detailed Performance":
                self._ui_state = "performance"
            elif selection == "Return Home":
                self._ui_state = "home"
        elif action == "menu_back":
            self._ui_state = "home"

    def _practice_menu_items(self) -> list[str]:
        items = [pattern.name for pattern in self.chart.patterns]
        items.append("Back")
        return items

    def _update(self, now_ms: int) -> None:
        if self._ui_state != "play":
            return
        self._tick_metronome(now_ms)
        self._update_active_holds(now_ms)
        self._expire_missed_notes(now_ms)

        if not self.pending_notes and not self._active_holds and not self._session_complete:
            self._end_session()

    def _handle_zone_press(self, zone: int, actual_time_ms: int) -> None:
        if self._try_hit_tap(zone, actual_time_ms):
            return
        return

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
        candidate = self._find_matching_note(actual_time_ms, note_types={"AIR"}, allow_any_timing=True)
        if candidate is None:
            return
        result = self._coerce_hit_judgment(judge_timing(candidate.time_ms, actual_time_ms))
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
        candidate = self._find_matching_note(actual_time_ms, zone=zone, note_types={"SLIDE"}, allow_any_timing=True)
        if candidate is None:
            return False
        result = self._coerce_hit_judgment(judge_timing(candidate.time_ms, actual_time_ms))
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
        candidate = self._find_matching_note(actual_time_ms, zone=zone, note_types={"HOLD"}, allow_any_timing=True)
        if candidate is None:
            return False
        result = self._coerce_hit_judgment(judge_timing(candidate.time_ms, actual_time_ms))
        required_duration_ms = max(HOLD_MIN_MS, candidate.duration_ms)
        self._active_holds[zone] = HoldState(
            note=candidate,
            start_time_ms=actual_time_ms,
            required_duration_ms=required_duration_ms,
            timing_result=result,
        )
        self.pending_notes.remove(candidate)
        self._set_last_judgment(result.judgment, actual_time_ms)
        return True

    def _try_hit_tap(self, zone: int, actual_time_ms: int) -> bool:
        adjusted_time_ms = self._adjust_time(actual_time_ms)
        candidate = self._find_matching_note(
            adjusted_time_ms,
            zone=zone,
            note_types={"TAP"},
            allow_any_timing=True,
        )
        if candidate is None:
            return False
        result = self._coerce_hit_judgment(judge_timing(candidate.time_ms, adjusted_time_ms))
        self._record_note_result(
            candidate,
            actual_time_ms=actual_time_ms,
            offset_ms=result.offset_ms,
            judgment=result.judgment,
            zone=zone,
        )
        self.pending_notes.remove(candidate)
        return True

    def _adjust_time(self, time_ms: int) -> int:
        return time_ms + self.input_offset_ms

    def _find_matching_note(
        self,
        actual_time_ms: int,
        zone: int | None = None,
        note_types: set[str] | None = None,
        allow_any_timing: bool = False,
    ) -> ChartNote | None:
        best_note = None
        best_offset = None
        for note in self.pending_notes:
            if zone is not None and note.lane != zone:
                continue
            if note_types is not None and note.note_type not in note_types:
                continue
            offset = abs(note.time_ms - actual_time_ms)
            if not allow_any_timing and offset > MAX_JUDGE_WINDOW_MS:
                continue
            if best_note is None or (best_offset is not None and offset < best_offset):
                best_note = note
                best_offset = offset
        return best_note

    @staticmethod
    def _coerce_hit_judgment(result: JudgmentResult) -> JudgmentResult:
        if result.judgment == "Miss":
            return JudgmentResult("Bad", 40, result.offset_ms)
        return result

    def _record_note_result(
        self,
        note: ChartNote,
        actual_time_ms: int,
        offset_ms: int,
        judgment: str,
        zone: int,
        display_time_ms: int | None = None,
    ) -> None:
        if display_time_ms is None:
            display_time_ms = actual_time_ms
        self._set_last_judgment(judgment, display_time_ms)
        self._judgment_counts[judgment] = self._judgment_counts.get(judgment, 0) + 1
        if judgment == "Miss":
            self.combo = 0
        else:
            self.combo += 1
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
        if judgment != "Miss":
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
                    display_time_ms=now_ms,
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
        adjusted_now_ms = self._adjust_time(now_ms)
        expired_notes = [note for note in self.pending_notes if adjusted_now_ms > note.time_ms + MAX_JUDGE_WINDOW_MS]
        if expired_notes:
            print(f"Expiring {len(expired_notes)} notes at now_ms={now_ms}")
        for note in expired_notes:
            self._record_note_result(
                note,
                actual_time_ms=now_ms,
                offset_ms=adjusted_now_ms - note.time_ms,
                judgment="Miss",
                zone=note.lane,
            )
            self.pending_notes.remove(note)

    def _draw(self, now_ms: int) -> None:
        if self._ui_state == "home":
            self._draw_home()
            return
        if self._ui_state == "practice_select":
            self._draw_practice_select()
            return
        if self._ui_state == "settings":
            self._draw_settings()
            return
        if self._ui_state == "performance":
            self._draw_performance()
            return
        if self._ui_state == "results":
            self._draw_results()
            return
        self.screen.fill((12, 12, 18))
        lane_width = self.config.width // 4
        lane_inner_width = lane_width - 2
        judgment_line_y = self.config.height - JUDGMENT_LINE_OFFSET
        for index in range(4):
            x = index * lane_width
            pygame.draw.rect(self.screen, (32, 32, 44), (x, 0, lane_inner_width, self.config.height))
            pygame.draw.rect(self.screen, (70, 70, 92), (x, judgment_line_y, lane_inner_width, 10))
        for note in self.pending_notes[:12]:
            travel_progress = max(0, min(1, (now_ms - note.time_ms + self.note_travel_ms) / self.note_travel_ms))
            spawn_y = -40
            y = int(spawn_y + travel_progress * (judgment_line_y - spawn_y))
            lane_x = (note.lane - 1) * lane_width
            note_half_height = NOTE_HEIGHT // 2
            note_rect = pygame.Rect(lane_x, y - note_half_height, lane_inner_width, NOTE_HEIGHT)
            pygame.draw.rect(self.screen, (135, 206, 250), note_rect, border_radius=8)
            pygame.draw.rect(self.screen, (255, 255, 255), note_rect, width=2, border_radius=8)
        self._draw_judgment(now_ms)

    def _draw_home(self) -> None:
        self._draw_menu("Rhythm Game Trainer", HOME_MENU, self._home_index)
        self._draw_footer("GPIO: 1/2/3/4 = up/down/select/back")

    def _draw_practice_select(self) -> None:
        items = self._practice_menu_items()
        self._draw_menu("Select Pattern", items, self._practice_index)
        self._draw_footer("Select a pattern to practice")

    def _draw_settings(self) -> None:
        self.screen.fill((12, 12, 18))
        title = self.title_font.render("Settings", True, (240, 240, 240))
        self.screen.blit(title, title.get_rect(center=(self.config.width // 2, 80)))
        lines = [
            f"{SETTINGS_MENU[0]}: {self.note_travel_ms}",
            f"{SETTINGS_MENU[1]}: {self.input_offset_ms}",
            SETTINGS_MENU[2],
        ]
        self._draw_menu_items(lines, self._settings_index, start_y=160)
        self._draw_footer("GPIO: 1/2 = -/+ | 3 = next | 4 = back")

    def _draw_results(self) -> None:
        self.screen.fill((12, 12, 18))
        title = self.title_font.render("Session Complete", True, (240, 220, 140))
        self.screen.blit(title, title.get_rect(center=(self.config.width // 2, 80)))
        summary = self._last_session_summary or {}
        total = int(summary.get("total", 0))
        hit_rate = float(summary.get("hit_rate", 0.0))
        clean_rate = float(summary.get("clean_rate", 0.0))
        counts = summary.get("counts", {}) if isinstance(summary.get("counts", {}), dict) else {}
        lines = [
            f"Total Notes: {total}",
            f"Hit Rate: {hit_rate:.1f}%",
            f"Clean Rate: {clean_rate:.1f}%",
            f"Perfect: {counts.get('Perfect', 0)} | Great: {counts.get('Great', 0)} | Good: {counts.get('Good', 0)}",
            f"Bad: {counts.get('Bad', 0)} | Miss: {counts.get('Miss', 0)}",
        ]
        self._draw_text_block(lines, start_y=150)
        self._draw_menu_items(RESULTS_MENU, self._results_index, start_y=360)

    def _draw_performance(self) -> None:
        self.screen.fill((12, 12, 18))
        title = self.title_font.render("Performance", True, (240, 240, 240))
        self.screen.blit(title, title.get_rect(center=(self.config.width // 2, 80)))
        summary = self._last_session_summary
        if not summary:
            self._draw_text_block(["No sessions recorded yet."], start_y=180)
            self._draw_footer("Press back to return")
            return
        self._draw_text_block(
            [
                f"Total Notes: {int(summary.get('total', 0))}",
                f"Hit Rate: {float(summary.get('hit_rate', 0.0)):.1f}%",
                f"Clean Rate: {float(summary.get('clean_rate', 0.0)):.1f}%",
            ],
            start_y=150,
        )
        pattern_stats = summary.get("pattern_stats", {})
        if isinstance(pattern_stats, dict) and pattern_stats:
            items = []
            for name, stats in sorted(pattern_stats.items()):
                clean_rate = float(stats.get("clean_rate", 0.0))
                items.append(f"{name}: {clean_rate:.1f}% clean")
            self._draw_text_block(items[:6], start_y=260)
        self._draw_footer("Back to return")

    def _draw_menu(self, title: str, items: tuple[str, ...] | list[str], selected_index: int) -> None:
        self.screen.fill((12, 12, 18))
        title_surf = self.title_font.render(title, True, (240, 240, 240))
        self.screen.blit(title_surf, title_surf.get_rect(center=(self.config.width // 2, 80)))
        self._draw_menu_items(items, selected_index, start_y=170)

    def _draw_menu_items(self, items: tuple[str, ...] | list[str], selected_index: int, start_y: int) -> None:
        y = start_y
        for index, item in enumerate(items):
            is_selected = index == selected_index
            color = (255, 255, 255) if is_selected else (180, 180, 180)
            text = self.font.render(item, True, color)
            rect = text.get_rect(center=(self.config.width // 2, y))
            if is_selected:
                highlight = rect.inflate(30, 14)
                pygame.draw.rect(self.screen, (40, 40, 60), highlight, border_radius=8)
            self.screen.blit(text, rect)
            y += 44

    def _draw_text_block(self, lines: list[str], start_y: int) -> None:
        y = start_y
        for line in lines:
            text = self.font.render(line, True, (210, 210, 210))
            self.screen.blit(text, (80, y))
            y += 32

    def _draw_footer(self, text: str) -> None:
        hint = self.small_font.render(text, True, (160, 160, 160))
        self.screen.blit(hint, (40, self.config.height - 50))

    def _draw_judgment(self, now_ms: int) -> None:
        if not self.last_judgment:
            return
        elapsed_ms = now_ms - self._last_judgment_ms
        if elapsed_ms < 0 or elapsed_ms > JUDGMENT_DISPLAY_MS:
            return
        color = {
            "Perfect": (255, 226, 130),
            "Great": (150, 220, 255),
            "Good": (150, 240, 170),
            "Bad": (255, 170, 120),
            "Miss": (255, 110, 110),
        }.get(self.last_judgment, (240, 240, 240))
        text = self.judgment_font.render(self.last_judgment, True, color)
        if self.last_judgment != "Miss":
            fade_ratio = max(0.0, 1.0 - (elapsed_ms / JUDGMENT_DISPLAY_MS))
            alpha = int(255 * fade_ratio)
            if alpha <= 0:
                return
            text.set_alpha(alpha)
        rect = text.get_rect(center=(self.config.width // 2, self.config.height // 2))
        self.screen.blit(text, rect)

    def _set_last_judgment(self, judgment: str, display_time_ms: int) -> None:
        self.last_judgment = judgment
        self._last_judgment_ms = display_time_ms

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