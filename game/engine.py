# game/engine.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import array

import pygame

from camera.air_detector import AirDetector
from data.analytics import generate_analytics
from data.recorder import SessionRecorder
from data.performance_store import append_history, load_history
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

# 開場 lead-in：note time_ms 統一向後推，保證第一批 note 唔會喺入場瞬間被 expire
LEAD_IN_MS = 1500

NOTE_TRAVEL_MIN_MS = 800
NOTE_TRAVEL_MAX_MS = 5000
NOTE_TRAVEL_STEP_MS = 100
INPUT_OFFSET_MIN_MS = -300
INPUT_OFFSET_MAX_MS = 300
INPUT_OFFSET_STEP_MS = 10

# HOME_MENU 第 4 項係 "Settings"（4-lane 調校畫面）
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

JUDGMENT_COLORS = {
    "Perfect": (255, 226, 130),
    "Great": (150, 220, 255),
    "Good": (150, 240, 170),
    "Bad": (255, 170, 120),
    "Miss": (255, 110, 110),
}


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
        self.font = pygame.font.SysFont("Arial", 34)
        self.small_font = pygame.font.SysFont("Arial", 26)
        self.judgment_font = pygame.font.SysFont("Arial", 72, bold=True)
        self.title_font = pygame.font.SysFont("Arial", 56, bold=True)
        # 大號 * 用於 offset chart marker
        self.marker_font = pygame.font.SysFont("Arial", 30, bold=True)
        self.chart = load_chart(config.chart_path)
        self.input_handler = InputHandler()
        self.air_detector = AirDetector()
        self.running = True
        self._ui_state = "home"
        self._home_index = 0
        self._practice_index = 0
        self._results_index = 0
        self._performance_index = 0
        self._performance_section_index = 0
        self._practice_pattern: str | None = None
        self._current_mode = "quick"
        self._last_session_summary: dict[str, object] | None = None
        self._judgment_counts: dict[str, int] = {}
        self._performance_history = load_history()
        if self._performance_history:
            self._performance_index = len(self._performance_history) - 1
        # Settings 狀態（舊稱 Tuning）
        self._tuning_param: str = "input_offset_ms"
        self._tuning_note_start_ms = 0
        self._tuning_last_adjust_ms = 0
        self._tuning_tap_pending: int = 0
        # Session 狀態
        self._session_complete = True
        self._session_saved = True
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
        self.pending_notes: list[ChartNote] = []
        self.recorder = SessionRecorder()
        self.combo = 0
        self.last_judgment = ""
        self._total_notes = 0
        self._hit_notes = 0
        self._perfect_notes = 0
        # [CHANGE] Miss 相片路徑列表，每個 session 開始時清空
        self._miss_photos: list[str] = []
        # 初始值避免 run() 第一 frame AttributeError；_reset_session 會覆蓋
        self.start_ticks = pygame.time.get_ticks()

    # ─── SOUND ───────────────────────────────────────────────────────────────

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

    # ─── SESSION ─────────────────────────────────────────────────────────────

    def _reset_session(self) -> None:
        raw_notes = self._build_pending_notes()
        self.pending_notes = [
            ChartNote(
                time_ms=note.time_ms + LEAD_IN_MS,
                lane=note.lane,
                note_type=note.note_type,
                duration_ms=note.duration_ms,
                pattern_name=note.pattern_name,
                pattern_instance=note.pattern_instance,
            )
            for note in raw_notes
        ]
        self.recorder = SessionRecorder()
        self.combo = 0
        self.last_judgment = ""
        self._last_judgment_ms = -JUDGMENT_DISPLAY_MS
        self._session_complete = False
        self._session_saved = False
        self._active_holds.clear()
        self._recent_presses.clear()
        self._active_zones.clear()
        self._next_beat_ms = LEAD_IN_MS
        self._total_notes = len(self.pending_notes)
        self._hit_notes = 0
        self._perfect_notes = 0
        self._judgment_counts = {}
        # [CHANGE] 每個新 session 清空 Miss 相片列表
        self._miss_photos = []
        # ── 關鍵：start_ticks 喺所有 state reset 完之後先 set ──
        self.start_ticks = pygame.time.get_ticks()
        print(f"Loaded {len(self.pending_notes)} notes (lead-in={LEAD_IN_MS}ms).")

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
                    pattern_instance=note.pattern_instance,
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
        pattern_duration_ms = max_time - min_time if max_time > min_time else 1000
        notes: list[ChartNote] = []
        for rep in range(repeats):
            time_offset = lead_in_ms + rep * (pattern_duration_ms + gap_ms)
            for note in sorted(pattern.notes, key=lambda n: n.time_ms):
                notes.append(
                    ChartNote(
                        time_ms=note.time_ms - min_time + time_offset,
                        lane=note.lane,
                        note_type=note.note_type,
                        duration_ms=note.duration_ms,
                        pattern_name=note.pattern_name,
                        pattern_instance=rep + 1,
                    )
                )
        return notes

    def _start_session(self, mode: str, practice_pattern: str | None) -> None:
        self._current_mode = mode
        self._practice_pattern = practice_pattern
        self._reset_session()
        self._ui_state = "play"

    def _end_session(self) -> None:
        if self._session_complete:
            return
        try:
            rows_copy = list(self.recorder.rows)
        except Exception:
            rows_copy = []
        summary = self._build_session_summary(rows=rows_copy)
        total = int(summary.get("total", 0))
        if total == 0:
            self._session_complete = True
            return
        self._session_complete = True
        self._finalize_session()
        self._last_session_summary = summary
        try:
            self._performance_history = append_history(summary)
            self._performance_index = len(self._performance_history) - 1
        except Exception:
            self._performance_history = load_history()
            self._performance_index = max(0, len(self._performance_history) - 1)
        self._performance_section_index = 0
        self._results_index = 0
        self._ui_state = "results"

    def _build_session_summary(self, rows: list[dict[str, object]] | None = None) -> dict[str, object]:
        rows = rows if rows is not None else (self.recorder.rows if getattr(self, "recorder", None) is not None else [])

        def to_int(value: object, default: int = 0) -> int:
            try:
                return int(float(value))  # type: ignore[arg-type]
            except Exception:
                return default

        total = len(rows)
        counts: dict[str, int] = {"Perfect": 0, "Great": 0, "Good": 0, "Bad": 0, "Miss": 0}
        perfect_early = 0
        perfect_late = 0
        offsets: list[int] = []
        judgments_per_note: list[str] = []

        section_stats: dict[tuple[str, int], dict[str, object]] = {}
        for row in rows:
            judgment = str(row.get("judgment", ""))
            if judgment in counts:
                counts[judgment] += 1
            judgments_per_note.append(judgment)
            pattern = str(row.get("pattern_name", "Unknown") or "Unknown")
            instance = to_int(row.get("pattern_instance"), 1)
            key = (pattern, instance)
            stats = section_stats.setdefault(
                key,
                {
                    "pattern": pattern,
                    "instance": instance,
                    "total": 0,
                    "counts": {"Perfect": 0, "Great": 0, "Good": 0, "Bad": 0, "Miss": 0},
                    "min_time": None,
                },
            )
            stats["total"] += 1
            if judgment in stats["counts"]:  # type: ignore[operator]
                stats["counts"][judgment] += 1  # type: ignore[index]
            expected_time = to_int(row.get("expected_time"), 0)
            min_time = stats["min_time"]
            if min_time is None or expected_time < min_time:
                stats["min_time"] = expected_time
            if judgment == "Perfect":
                offset = to_int(row.get("offset_ms"), 0)
                if offset < 0:
                    perfect_early += 1
                elif offset > 0:
                    perfect_late += 1
            offsets.append(to_int(row.get("offset_ms"), 0))

        hits = total - counts.get("Miss", 0)
        hit_rate = (hits / total) * 100.0 if total > 0 else 0.0
        clean_hits = sum(counts.get(item, 0) for item in ("Perfect", "Great", "Good"))
        clean_rate = (clean_hits / total) * 100.0 if total > 0 else 0.0
        percentages = {
            key: (value / total) * 100.0 if total > 0 else 0.0
            for key, value in counts.items()
        }

        pattern_occurrences: dict[str, int] = {}
        for pattern, _ in section_stats:
            pattern_occurrences[pattern] = pattern_occurrences.get(pattern, 0) + 1

        sections = []
        for key, stats in section_stats.items():
            pattern = stats["pattern"]
            instance = stats["instance"]
            label = pattern
            if pattern_occurrences.get(pattern, 0) > 1:
                label = f"{pattern}_{instance}"
            counts_for_section = stats["counts"]
            section_total = stats["total"]
            section_hits = section_total - counts_for_section.get("Miss", 0)  # type: ignore[union-attr]
            section_clean = sum(counts_for_section.get(item, 0) for item in ("Perfect", "Great", "Good"))  # type: ignore[union-attr]
            sections.append(
                {
                    "label": label,
                    "pattern": pattern,
                    "instance": instance,
                    "total": section_total,
                    "counts": counts_for_section,
                    "hit_rate": (section_hits / section_total) * 100.0 if section_total else 0.0,
                    "clean_rate": (section_clean / section_total) * 100.0 if section_total else 0.0,
                    "percentages": {
                        k: (v / section_total) * 100.0 if section_total else 0.0
                        for k, v in counts_for_section.items()  # type: ignore[union-attr]
                    },
                    "min_time": stats["min_time"] if stats["min_time"] is not None else 0,
                }
            )
        sections.sort(key=lambda item: item.get("min_time", 0))

        return {
            "session_id": self.recorder.session_id,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "mode": self._current_mode,
            "practice_pattern": self._practice_pattern,
            "bpm": self.config.bpm,
            "note_travel_ms": self.note_travel_ms,
            "input_offset_ms": self.input_offset_ms,
            "total": total,
            "hits": hits,
            "hit_rate": hit_rate,
            "clean_rate": clean_rate,
            "counts": counts,
            "percentages": percentages,
            "perfect_early": perfect_early,
            "perfect_late": perfect_late,
            "offsets": offsets,
            "judgments_per_note": judgments_per_note,
            "sections": sections,
            # [CHANGE] 每個 Miss 對應嘅相片路徑（camera 唔可用時係空 list）
            "miss_photos": list(getattr(self, "_miss_photos", [])),
        }

    # ─── MAIN LOOP ───────────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            print("GameEngine inner run loop started")
            snapshot = None
            while self.running:
                # ── BUG FIX：poll 用當前 ticks，event 處理完之後再算 now_ms ──
                # 如果 _handle_events 入面 call 咗 _reset_session，
                # start_ticks 已經更新，now_ms 會正確反映新 session 嘅時間，
                # 唔會用舊 session 嘅大 now_ms 去 expire 新 notes
                poll_ms = pygame.time.get_ticks() - self.start_ticks
                snapshot = self.input_handler.poll(poll_ms)
                self._active_zones = snapshot.active_zones
                self._handle_events(snapshot.events, snapshot.quit_requested)
                # 重新計算 now_ms，確保用最新嘅 start_ticks
                now_ms = pygame.time.get_ticks() - self.start_ticks
                self._update(now_ms)
                self._draw(now_ms)
                pygame.display.flip()
                self.clock.tick(60)
            quit_requested = snapshot.quit_requested if snapshot is not None else False
            print(f"GameEngine inner loop exited. pending_notes={len(self.pending_notes)}, quit={quit_requested}")
        finally:
            print("GameEngine shutdown.")
            self._shutdown()

    # ─── EVENT HANDLING ──────────────────────────────────────────────────────

    def _handle_events(self, events: list[InputEvent], quit_requested: bool) -> None:
        if quit_requested:
            self.running = False
            return
        for event in events:
            if self._ui_state == "tuning":
                self._handle_tuning_input(event)
                continue
            if self._ui_state != "play":
                if event.action == "restart" and self._ui_state == "results":
                    self._start_session(self._current_mode, self._practice_pattern)
                    continue
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
        if event.action in {"menu_up", "menu_down", "menu_left", "menu_right", "menu_select", "menu_back"}:
            action = event.action
        elif event.zone is not None and event.pressed:
            mapping = {1: "menu_up", 2: "menu_down", 3: "menu_select", 4: "menu_back"}
            action = mapping.get(event.zone)
        if action is None:
            return
        if self._ui_state == "home":
            self._handle_home_menu_action(action)
        elif self._ui_state == "practice_select":
            self._handle_practice_menu_action(action)
        elif self._ui_state == "results":
            self._handle_results_menu_action(action)
        elif self._ui_state == "performance_list":
            self._handle_performance_list_action(action)
        elif self._ui_state == "performance_detail":
            self._handle_performance_detail_action(action)

    def _handle_home_menu_action(self, action: str) -> None:
        if action == "menu_up":
            self._home_index = (self._home_index - 1) % len(HOME_MENU)
        elif action == "menu_down":
            self._home_index = (self._home_index + 1) % len(HOME_MENU)
        elif action == "menu_select":
            self._dispatch_home_selection()
        elif action == "menu_back":
            pass

    def _dispatch_home_selection(self) -> None:
        selection = HOME_MENU[self._home_index]
        if selection == "Mix & Match Quick Start":
            self._start_session("quick", None)
        elif selection == "Practice Pattern":
            self._practice_index = 0
            self._ui_state = "practice_select"
        elif selection == "Performance":
            self._ui_state = "performance_list"
        # [CHANGE] 之前係 "Tuning"，已改名為 "Settings"，呢度要跟住改
        elif selection == "Settings":
            # 直接入 4-lane settings 畫面，預設調 input_offset
            self._start_tuning("input_offset_ms")
        elif selection == "Quit":
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
                if self._performance_history:
                    self._performance_index = len(self._performance_history) - 1
                    self._performance_section_index = 0
                    self._ui_state = "performance_detail"
                else:
                    self._ui_state = "performance_list"
            elif selection == "Return Home":
                self._ui_state = "home"
        elif action == "menu_back":
            self._ui_state = "home"

    def _handle_performance_list_action(self, action: str) -> None:
        if not self._performance_history:
            if action in {"menu_back", "menu_select"}:
                self._ui_state = "home"
            return
        if action == "menu_up":
            self._performance_index = (self._performance_index - 1) % len(self._performance_history)
        elif action == "menu_down":
            self._performance_index = (self._performance_index + 1) % len(self._performance_history)
        elif action == "menu_select":
            self._performance_section_index = 0
            self._ui_state = "performance_detail"
        elif action == "menu_back":
            self._ui_state = "home"

    def _handle_performance_detail_action(self, action: str) -> None:
        sections = self._current_sections()
        if action == "menu_up" and sections:
            self._performance_section_index = (self._performance_section_index - 1) % len(sections)
        elif action == "menu_down" and sections:
            self._performance_section_index = (self._performance_section_index + 1) % len(sections)
        elif action == "menu_select" and sections:
            current_section = sections[self._performance_section_index]
            pattern = str(current_section.get("pattern", ""))
            if pattern:
                self._start_session("practice", pattern)
        elif action == "menu_back":
            self._ui_state = "home"

    def _practice_menu_items(self) -> list[str]:
        items = [pattern.name for pattern in self.chart.patterns]
        items.append("Back")
        return items

    # ─── UPDATE ──────────────────────────────────────────────────────────────

    def _update(self, now_ms: int) -> None:
        if self._ui_state == "tuning":
            self._update_tuning(now_ms)
            return
        if self._ui_state != "play":
            return
        self._tick_metronome(now_ms)
        self._update_active_holds(now_ms)
        self._expire_missed_notes(now_ms)
        if not self.pending_notes and not self._active_holds and not self._session_complete:
            self._end_session()

    def _handle_zone_press(self, zone: int, actual_time_ms: int) -> None:
        self._try_hit_tap(zone, actual_time_ms)

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
        self._record_note_result(candidate, actual_time_ms=actual_time_ms, offset_ms=result.offset_ms, judgment=result.judgment, zone=candidate.lane)
        self.pending_notes.remove(candidate)

    def _try_hit_tap(self, zone: int, actual_time_ms: int) -> bool:
        adjusted_time_ms = self._adjust_time(actual_time_ms)
        candidate = self._find_matching_note(adjusted_time_ms, zone=zone, note_types={"TAP"}, allow_any_timing=True)
        if candidate is None:
            return False
        result = self._coerce_hit_judgment(judge_timing(candidate.time_ms, adjusted_time_ms))
        self._record_note_result(candidate, actual_time_ms=actual_time_ms, offset_ms=result.offset_ms, judgment=result.judgment, zone=zone)
        self.pending_notes.remove(candidate)
        return True

    def _adjust_time(self, time_ms: int) -> int:
        return time_ms + self.input_offset_ms

    def _find_matching_note(self, actual_time_ms: int, zone: int | None = None, note_types: set[str] | None = None, allow_any_timing: bool = False) -> ChartNote | None:
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

    def _record_note_result(self, note: ChartNote, actual_time_ms: int, offset_ms: int, judgment: str, zone: int, display_time_ms: int | None = None) -> None:
        if display_time_ms is None:
            display_time_ms = actual_time_ms
        self._set_last_judgment(judgment, display_time_ms)
        self._judgment_counts[judgment] = self._judgment_counts.get(judgment, 0) + 1
        if judgment == "Miss":
            self.combo = 0
            # [CHANGE] Miss 發生時，用 camera module 拍一張相並儲存
            miss_index = self._judgment_counts.get("Miss", 1)
            photo_path = (
                Path("data/miss_photos")
                / self.recorder.session_id
                / f"miss_{miss_index}.jpg"
            )
            if self.air_detector.capture_still(photo_path):
                self._miss_photos.append(str(photo_path))
        else:
            self.combo += 1
        self.recorder.record(
            session_id=self.recorder.session_id,
            timestamp=self.recorder.session_timestamp(),
            pattern_name=note.pattern_name,
            pattern_instance=note.pattern_instance,
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
                self._record_note_result(hold.note, actual_time_ms=hold.start_time_ms, offset_ms=hold.timing_result.offset_ms, judgment=hold.timing_result.judgment, zone=zone, display_time_ms=now_ms)

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
            self._record_note_result(note, actual_time_ms=now_ms, offset_ms=adjusted_now_ms - note.time_ms, judgment="Miss", zone=note.lane)
            self.pending_notes.remove(note)

    # ─── DRAW ────────────────────────────────────────────────────────────────

    def _draw(self, now_ms: int) -> None:
        if self._ui_state == "home":
            self._draw_home()
        elif self._ui_state == "practice_select":
            self._draw_practice_select()
        elif self._ui_state == "tuning":
            self._draw_tuning(now_ms)
        elif self._ui_state == "performance_list":
            self._draw_performance_list()
        elif self._ui_state == "performance_detail":
            self._draw_performance_detail()
        elif self._ui_state == "results":
            self._draw_results()
        else:
            self._draw_play(now_ms)

    def _draw_home(self) -> None:
        self._draw_menu("Rhythm Game Trainer", HOME_MENU, self._home_index)
        self._draw_footer("GPIO: 1=up  2=down  3=select  4=back")

    def _draw_practice_select(self) -> None:
        items = self._practice_menu_items()
        self._draw_menu("Select Pattern", items, self._practice_index)
        self._draw_footer("Select a pattern to practice")

    def _draw_results(self) -> None:
        self.screen.fill((12, 12, 18))
        title = self.title_font.render("Session Complete", True, (240, 220, 140))
        self.screen.blit(title, title.get_rect(center=(self.config.width // 2, 70)))
        summary = self._last_session_summary or {}
        total = int(summary.get("total", 0))
        hit_rate = float(summary.get("hit_rate", 0.0))
        clean_rate = float(summary.get("clean_rate", 0.0))
        counts = summary.get("counts", {}) if isinstance(summary.get("counts", {}), dict) else {}
        percentages = summary.get("percentages", {}) if isinstance(summary.get("percentages", {}), dict) else {}
        perfect_early = int(summary.get("perfect_early", 0))
        perfect_late = int(summary.get("perfect_late", 0))
        lines = [
            f"Total: {total}   Hit Rate: {hit_rate:.1f}%   Clean: {clean_rate:.1f}%",
            f"Perfect: {counts.get('Perfect', 0)} ({percentages.get('Perfect', 0.0):.1f}%)",
            f"Great:   {counts.get('Great', 0)} ({percentages.get('Great', 0.0):.1f}%)",
            f"Good:    {counts.get('Good', 0)} ({percentages.get('Good', 0.0):.1f}%)",
            f"Bad:     {counts.get('Bad', 0)} ({percentages.get('Bad', 0.0):.1f}%)",
            f"Miss:    {counts.get('Miss', 0)} ({percentages.get('Miss', 0.0):.1f}%)",
            f"Perfect Early: {perfect_early}   Late: {perfect_late}",
        ]
        self._draw_text_block(lines, start_y=140)
        self._draw_menu_items(RESULTS_MENU, self._results_index, start_y=430)

    def _draw_performance_list(self) -> None:
        self.screen.fill((12, 12, 18))
        title = self.title_font.render("Performance History", True, (240, 240, 240))
        self.screen.blit(title, title.get_rect(center=(self.config.width // 2, 80)))
        if not self._performance_history:
            self._draw_text_block(["No sessions recorded yet."], start_y=180)
            self._draw_footer("Back to return")
            return
        max_items = 7
        total_items = len(self._performance_history)
        start_index = max(0, self._performance_index - (max_items // 2))
        end_index = min(total_items, start_index + max_items)
        items = []
        for index in range(start_index, end_index):
            entry = self._performance_history[index]
            ended_at = str(entry.get("ended_at", ""))[:19].replace("T", " ")
            hit_rate = float(entry.get("hit_rate", 0.0))
            label = f"{ended_at} | Hit {hit_rate:.1f}%"
            items.append(label)
        selected = self._performance_index - start_index
        self._draw_menu_items(items, selected, start_y=170)
        self._draw_footer("Select a session for details")

    def _draw_performance_detail(self) -> None:
        self.screen.fill((12, 12, 18))
        title = self.title_font.render("Performance Detail", True, (240, 240, 240))
        self.screen.blit(title, title.get_rect(center=(self.config.width // 2, 55)))
        summary = self._current_performance()
        if not summary:
            self._draw_text_block(["No session selected."], start_y=180)
            self._draw_footer("Back to return")
            return
        counts = summary.get("counts", {}) if isinstance(summary.get("counts", {}), dict) else {}
        percentages = summary.get("percentages", {}) if isinstance(summary.get("percentages", {}), dict) else {}
        perfect_early = int(summary.get("perfect_early", 0))
        perfect_late = int(summary.get("perfect_late", 0))

        header = [
            f"Total: {int(summary.get('total', 0))}",
            f"Hit Rate: {float(summary.get('hit_rate', 0.0)):.1f}%",
            f"Perfect: {counts.get('Perfect', 0)} ({percentages.get('Perfect', 0.0):.1f}%)",
            f"Great:   {counts.get('Great', 0)} ({percentages.get('Great', 0.0):.1f}%)",
            f"Good:    {counts.get('Good', 0)} ({percentages.get('Good', 0.0):.1f}%)",
            f"Bad:     {counts.get('Bad', 0)} ({percentages.get('Bad', 0.0):.1f}%)",
            f"Miss:    {counts.get('Miss', 0)} ({percentages.get('Miss', 0.0):.1f}%)",
            f"P-Early: {perfect_early}  P-Late: {perfect_late}",
        ]
        self._draw_text_block_at(header, start_y=100, x=40, line_height=32)

        sections = self._current_sections()
        if sections:
            current = sections[self._performance_section_index % len(sections)]
            label = str(current.get("label", ""))
            sc = current.get("counts", {}) if isinstance(current.get("counts", {}), dict) else {}
            sp = current.get("percentages", {}) if isinstance(current.get("percentages", {}), dict) else {}
            section_lines = [
                f"[ {label} ]",
                f"Hit: {float(current.get('hit_rate', 0.0)):.1f}%  Clean: {float(current.get('clean_rate', 0.0)):.1f}%",
                f"Perfect: {sc.get('Perfect', 0)} ({sp.get('Perfect', 0.0):.1f}%)",
                f"Great:   {sc.get('Great', 0)} ({sp.get('Great', 0.0):.1f}%)",
                f"Good:    {sc.get('Good', 0)} ({sp.get('Good', 0.0):.1f}%)",
                f"Bad:     {sc.get('Bad', 0)} ({sp.get('Bad', 0.0):.1f}%)",
                f"Miss:    {sc.get('Miss', 0)} ({sp.get('Miss', 0.0):.1f}%)",
            ]
            self._draw_text_block_at(section_lines, start_y=100, x=self.config.width // 2 + 20, line_height=32)

        # [CHANGE] Miss 相片縮略圖區域（camera 唔可用時 miss_photos 係空 list，唔會顯示）
        miss_photos = summary.get("miss_photos", [])
        photo_strip_h = 0
        if isinstance(miss_photos, list) and miss_photos:
            photo_strip_h = 130
            strip_y = self.config.height - 60 - photo_strip_h
            # "Miss Photos" 標題
            label_surf = self.small_font.render("Miss Photos:", True, (255, 110, 110))
            self.screen.blit(label_surf, (40, strip_y))
            thumb_h = photo_strip_h - 30
            thumb_w = int(thumb_h * 4 / 3)
            x_cursor = 40
            for photo_path_str in miss_photos:
                if x_cursor + thumb_w > self.config.width - 40:
                    break
                photo_path = Path(photo_path_str)
                if photo_path.exists():
                    try:
                        img = pygame.image.load(str(photo_path))
                        img = pygame.transform.scale(img, (thumb_w, thumb_h))
                        self.screen.blit(img, (x_cursor, strip_y + 28))
                    except Exception:
                        # 相片載入失敗：顯示紅色佔位方塊
                        pygame.draw.rect(
                            self.screen,
                            (60, 30, 30),
                            (x_cursor, strip_y + 28, thumb_w, thumb_h),
                        )
                        err_surf = self.small_font.render("?", True, (255, 110, 110))
                        self.screen.blit(
                            err_surf,
                            err_surf.get_rect(
                                center=(x_cursor + thumb_w // 2, strip_y + 28 + thumb_h // 2)
                            ),
                        )
                x_cursor += thumb_w + 8

        offsets = summary.get("offsets", [])
        judgments_list = summary.get("judgments_per_note", [])
        if isinstance(offsets, list) and len(offsets) >= 1:
            chart_top = 370
            # [CHANGE] 如果有相片，offset chart 底部要留空俾相片條
            chart_bottom = self.config.height - 60 - photo_strip_h
            chart_rect = pygame.Rect(40, chart_top, self.config.width - 80, chart_bottom - chart_top)
            if chart_rect.height > 20:
                self._draw_offset_chart(offsets, judgments_list, chart_rect)

        self._draw_footer("1/2: sections | 3: practice pattern | 4: back")

    def _draw_play(self, now_ms: int) -> None:
        self.screen.fill((12, 12, 18))
        W, H = self.config.width, self.config.height
        lane_width = W // 4
        lane_inner_width = lane_width - 2
        judgment_line_y = H - JUDGMENT_LINE_OFFSET
        for index in range(4):
            x = index * lane_width
            pygame.draw.rect(self.screen, (32, 32, 44), (x, 0, lane_inner_width, H))
            pygame.draw.rect(self.screen, (70, 70, 92), (x, judgment_line_y, lane_inner_width, 10))
        for note in self.pending_notes[:16]:
            travel_progress = max(0.0, min(1.0, (now_ms - note.time_ms + self.note_travel_ms) / self.note_travel_ms))
            spawn_y = -NOTE_HEIGHT
            y = int(spawn_y + travel_progress * (judgment_line_y - spawn_y))
            lane_x = (note.lane - 1) * lane_width
            note_rect = pygame.Rect(lane_x, y - NOTE_HEIGHT // 2, lane_inner_width, NOTE_HEIGHT)
            pygame.draw.rect(self.screen, (135, 206, 250), note_rect, border_radius=8)
            pygame.draw.rect(self.screen, (255, 255, 255), note_rect, width=2, border_radius=8)
        combo_surf = self.font.render(f"Combo: {self.combo}", True, (200, 200, 255))
        self.screen.blit(combo_surf, (16, 16))
        total_so_far = sum(self._judgment_counts.values())
        hits_so_far = total_so_far - self._judgment_counts.get("Miss", 0)
        acc_str = f"{(hits_so_far / total_so_far * 100):.1f}%" if total_so_far > 0 else "--"
        acc_surf = self.small_font.render(f"Acc: {acc_str}", True, (180, 220, 180))
        self.screen.blit(acc_surf, (16, 54))
        self._draw_judgment(now_ms)

    # ─── SETTINGS (previously Tuning) ────────────────────────────────────────

    def _start_tuning(self, param: str) -> None:
        self._tuning_param = param
        self._tuning_note_start_ms = pygame.time.get_ticks() - self.start_ticks
        self._tuning_last_adjust_ms = self._tuning_note_start_ms
        self._tuning_tap_pending = 0
        self._set_last_judgment("", self._tuning_note_start_ms)
        self._ui_state = "tuning"

    def _handle_tuning_input(self, event: InputEvent) -> None:
        if event.zone is None or not event.pressed:
            return
        if event.zone == 4:
            self._ui_state = "home"
        elif event.zone == 2:
            now_ms = pygame.time.get_ticks() - self.start_ticks
            self._handle_tuning_press(now_ms)
        elif event.zone == 1:
            self._tuning_tap_pending = -1
        elif event.zone == 3:
            self._tuning_tap_pending = 1

    def _handle_tuning_press(self, actual_time_ms: int) -> None:
        if self.note_travel_ms <= 0:
            return
        adjusted_time_ms = self._adjust_time(actual_time_ms)
        elapsed_ms = adjusted_time_ms - self._tuning_note_start_ms
        cycle_index = max(0, elapsed_ms // self.note_travel_ms)
        expected_time = self._tuning_note_start_ms + (cycle_index + 1) * self.note_travel_ms
        result = self._coerce_hit_judgment(judge_timing(expected_time, adjusted_time_ms))
        self._set_last_judgment(result.judgment, actual_time_ms)

    def _update_tuning(self, now_ms: int) -> None:
        if self._tuning_tap_pending != 0:
            self._apply_tuning_step(self._tuning_tap_pending)
            self._tuning_tap_pending = 0
        hold_interval_ms = 80
        if now_ms - self._tuning_last_adjust_ms < hold_interval_ms:
            return
        direction = 0
        if 3 in self._active_zones and 1 in self._active_zones:
            direction = 0
        elif 3 in self._active_zones:
            direction = 1
        elif 1 in self._active_zones:
            direction = -1
        if direction == 0:
            return
        self._tuning_last_adjust_ms = now_ms
        self._apply_tuning_step(direction)

    def _apply_tuning_step(self, direction: int) -> None:
        if self._tuning_param == "note_travel_ms":
            self.note_travel_ms = max(
                NOTE_TRAVEL_MIN_MS,
                min(NOTE_TRAVEL_MAX_MS, self.note_travel_ms + direction * NOTE_TRAVEL_STEP_MS),
            )
        elif self._tuning_param == "input_offset_ms":
            self.input_offset_ms = max(
                INPUT_OFFSET_MIN_MS,
                min(INPUT_OFFSET_MAX_MS, self.input_offset_ms + direction * INPUT_OFFSET_STEP_MS),
            )

    def _draw_tuning(self, now_ms: int) -> None:
        """
        4-lane settings 畫面：
          Lane 2 有循環 test note
          Lane 1 = decrease active param（tap 單步 / hold 持續）
          Lane 2 = hit note（note 近判定線）/ 切換 active param（note 遠）
          Lane 3 = increase active param
          Lane 4 = 返回 Home
          兩個參數同時顯示喺 judgment 線下方，active 一個高亮
        """
        self.screen.fill((12, 12, 18))
        W, H = self.config.width, self.config.height
        lane_width = W // 4
        lane_inner_width = lane_width - 2
        # Settings 專用：比 play mode 高，預留足夠空間給底部 info panel
        judgment_line_y = H - 260

        for index in range(4):
            x = index * lane_width
            is_active = (index + 1) in self._active_zones
            lane_col = (50, 55, 75) if is_active else (32, 32, 44)
            pygame.draw.rect(self.screen, lane_col, (x, 0, lane_inner_width, H))
            pygame.draw.rect(self.screen, (70, 70, 92), (x, judgment_line_y, lane_inner_width, 10))

        # Lane 2 循環 test note
        if self.note_travel_ms > 0:
            elapsed = now_ms - self._tuning_note_start_ms
            phase = elapsed % self.note_travel_ms
            travel_progress = phase / self.note_travel_ms
            spawn_y = -NOTE_HEIGHT
            note_y = int(spawn_y + travel_progress * (judgment_line_y - spawn_y))
            lane_x = lane_width
            note_rect = pygame.Rect(lane_x, note_y - NOTE_HEIGHT // 2, lane_inner_width, NOTE_HEIGHT)
            pygame.draw.rect(self.screen, (135, 206, 250), note_rect, border_radius=8)
            pygame.draw.rect(self.screen, (255, 255, 255), note_rect, width=2, border_radius=8)

        # Judgment 顯示
        self._draw_judgment(now_ms)

        # ── 兩個參數同時顯示，active 高亮 ──
        # info_y 貼住 judgment line 下方 10px
        info_y = judgment_line_y + 10
        param_info = [
            ("Input Offset (ms)", self.input_offset_ms, "input_offset_ms"),
            ("Note Travel (ms)", self.note_travel_ms, "note_travel_ms"),
        ]
        col_w = W // 2
        for i, (label, value, param_key) in enumerate(param_info):
            is_selected = (self._tuning_param == param_key)
            cx = col_w * i + col_w // 2
            header_col = (255, 226, 130) if is_selected else (160, 160, 180)
            val_col = (255, 240, 100) if is_selected else (200, 200, 200)
            # 高亮背景：高度 96px 足夠包住 label(28px) + gap + value(60px)
            if is_selected:
                bg_rect = pygame.Rect(col_w * i + 8, info_y, col_w - 16, 96)
                pygame.draw.rect(self.screen, (40, 44, 64), bg_rect, border_radius=8)
                pygame.draw.rect(self.screen, (80, 80, 120), bg_rect, width=2, border_radius=8)
            lbl_surf = self.small_font.render(label, True, header_col)
            self.screen.blit(lbl_surf, lbl_surf.get_rect(center=(cx, info_y + 16)))
            val_surf = self.judgment_font.render(str(value), True, val_col)
            self.screen.blit(val_surf, val_surf.get_rect(center=(cx, info_y + 60)))

        # Lane 底部提示：固定在 footer 上方 50px 附近
        label_y = H - 84
        labels = ["[ ▼ Down ]", "[ Hit/Switch ]", "[ ▲ Up ]", "[ Home ]"]
        colors = [(255, 170, 120), (150, 220, 255), (150, 240, 170), (180, 180, 180)]
        for i, (txt, col) in enumerate(zip(labels, colors)):
            cx = i * lane_width + lane_inner_width // 2
            surf = self.small_font.render(txt, True, col)
            self.screen.blit(surf, surf.get_rect(center=(cx, label_y)))

        self._draw_footer("1/3: adjust  |  2: hit / switch param  |  4: back to home")

    # ─── OFFSET CHART ────────────────────────────────────────────────────────

    def _draw_offset_chart(self, offsets: list[int], judgments_list: list[str], rect: pygame.Rect) -> None:
        """
        Offset chart 改良版：
        - 自動 Y 軸範圍 + 更多刻度（gap 越大，刻度越密）
        - 粗線（3px）+ 大 * marker（marker_font）
        - 左邊留白顯示 ms 刻度，右對齊
        - Y 軸標籤「offset to perfect (ms)」
        """
        if not offsets:
            return

        pygame.draw.rect(self.screen, (22, 22, 36), rect)
        pygame.draw.rect(self.screen, (60, 60, 80), rect, width=2)

        # ── 動態 Y 範圍 ──
        actual_max = max(abs(o) for o in offsets)
        if actual_max == 0:
            actual_max = 10

        if actual_max <= 30:
            max_abs, tick_interval = 30, 10
        elif actual_max <= 50:
            max_abs, tick_interval = 50, 10
        elif actual_max <= 100:
            max_abs, tick_interval = 100, 25
        elif actual_max <= 150:
            max_abs, tick_interval = 150, 50
        elif actual_max <= 200:
            max_abs, tick_interval = 200, 50
        elif actual_max <= 300:
            max_abs, tick_interval = 300, 50
        elif actual_max <= 400:
            max_abs, tick_interval = 400, 100
        else:
            max_abs = min(500, ((actual_max + 99) // 100) * 100)
            tick_interval = 100

        # 左邊留白給 Y 軸 label（最長係 "-200" = 約 50px）
        left_margin = 58
        plot_left = rect.left + left_margin
        plot_right = rect.right - 4
        plot_width = plot_right - plot_left

        mid_y = rect.top + rect.height // 2
        scale_y = (rect.height // 2 - 8) / max_abs if max_abs > 0 else 1.0

        # ── Y 軸標題 ──
        title_surf = self.small_font.render("offset to perfect (ms)", True, (140, 140, 160))
        self.screen.blit(title_surf, (plot_left + 4, rect.top + 4))

        # ── Tick 線 + 數字 ──
        tick_vals = list(range(-max_abs, max_abs + 1, tick_interval))
        for tv in tick_vals:
            ty = int(mid_y - tv * scale_y)
            if ty < rect.top or ty > rect.bottom:
                continue
            line_col = (130, 130, 80) if tv == 0 else (65, 65, 88)
            line_w = 2 if tv == 0 else 1
            pygame.draw.line(self.screen, line_col, (plot_left, ty), (plot_right, ty), line_w)
            # 右對齊數字
            txt_surf = self.small_font.render(f"{tv:+d}" if tv != 0 else "0", True, (160, 160, 185))
            txt_rect = txt_surf.get_rect(midright=(plot_left - 4, ty))
            self.screen.blit(txt_surf, txt_rect)

        # ── 計算各 note 的像素位置 ──
        n = len(offsets)
        if n == 1:
            xs = [plot_left + plot_width // 2]
        else:
            xs = [plot_left + int(i * plot_width / (n - 1)) for i in range(n)]

        points: list[tuple[int, int]] = []
        for i, offset in enumerate(offsets):
            px = xs[i]
            clamped = max(-max_abs, min(max_abs, offset))
            py = int(mid_y - clamped * scale_y)
            points.append((px, py))

        # ── 粗連接線（3px）──
        if len(points) >= 2:
            pygame.draw.lines(self.screen, (85, 85, 115), False, points, 3)

        # ── 大 * marker，以 judgment 顏色區分 ──
        for i, (px, py) in enumerate(points):
            judgment = judgments_list[i] if i < len(judgments_list) else "Miss"
            color = JUDGMENT_COLORS.get(judgment, (220, 220, 220))
            star = self.marker_font.render("*", True, color)
            star_rect = star.get_rect(center=(px, py))
            self.screen.blit(star, star_rect)

    # ─── CHART HELPERS ───────────────────────────────────────────────────────

    def _current_performance(self) -> dict[str, object] | None:
        if not self._performance_history:
            return None
        self._performance_index = max(0, min(self._performance_index, len(self._performance_history) - 1))
        entry = self._performance_history[self._performance_index]
        return entry if isinstance(entry, dict) else None

    def _current_sections(self) -> list[dict[str, object]]:
        summary = self._current_performance()
        if not summary:
            return []
        sections = summary.get("sections", [])
        if isinstance(sections, list):
            return [s for s in sections if isinstance(s, dict)]
        return []

    # ─── DRAW HELPERS ────────────────────────────────────────────────────────

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
            text = self.font.render(str(item), True, color)
            rect = text.get_rect(center=(self.config.width // 2, y))
            if is_selected:
                highlight = rect.inflate(40, 18)
                pygame.draw.rect(self.screen, (40, 40, 60), highlight, border_radius=8)
            self.screen.blit(text, rect)
            y += 52

    def _draw_text_block(self, lines: list[str], start_y: int) -> None:
        y = start_y
        for line in lines:
            text = self.font.render(str(line), True, (210, 210, 210))
            self.screen.blit(text, (80, y))
            y += 38

    def _draw_text_block_at(self, lines: list[str], start_y: int, x: int, line_height: int = 36) -> None:
        y = start_y
        for line in lines:
            text = self.font.render(str(line), True, (210, 210, 210))
            self.screen.blit(text, (x, y))
            y += line_height

    def _draw_footer(self, text: str) -> None:
        hint = self.small_font.render(text, True, (160, 160, 160))
        self.screen.blit(hint, (40, self.config.height - 40))

    def _draw_judgment(self, now_ms: int) -> None:
        if not self.last_judgment:
            return
        elapsed_ms = now_ms - self._last_judgment_ms
        if elapsed_ms < 0 or elapsed_ms > JUDGMENT_DISPLAY_MS:
            return
        color = JUDGMENT_COLORS.get(self.last_judgment, (240, 240, 240))
        text = self.judgment_font.render(self.last_judgment, True, color)
        if self.last_judgment != "Miss":
            fade_ratio = max(0.0, 1.0 - (elapsed_ms / JUDGMENT_DISPLAY_MS))
            alpha = int(255 * fade_ratio)
            if alpha <= 0:
                return
            text.set_alpha(alpha)
        rect = text.get_rect(center=(self.config.width // 2, self.config.height // 2 - 60))
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