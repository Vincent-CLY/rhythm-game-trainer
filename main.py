from __future__ import annotations

import argparse
from pathlib import Path

from game.engine import GameConfig, GameEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rhythm game trainer")
    parser.add_argument("--bpm", type=int, default=120, choices=[60, 80, 100, 120, 140])
    parser.add_argument("--chart", type=Path, default=Path("charts/sample_chart.json"))
    parser.add_argument("--headless", action="store_true", help="Run without fullscreen mode")
    return parser


def main() -> None:
    print("Starting rhythm-game-trainer...")
    args = build_parser().parse_args()
    config = GameConfig(
        bpm=args.bpm,
        chart_path=args.chart,
        fullscreen=not args.headless,
    )
    try:
        print("Initializing GameEngine...")
        engine = GameEngine(config)
        print("GameEngine initialized successfully. Starting run loop...")
        engine.run()
        print("GameEngine run loop finished smoothly.")
    except Exception as e:
        print(f"FATAL ERROR during GameEngine execution: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()