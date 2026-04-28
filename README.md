# rhythm-game-trainer

Rhythm trainer scaffold for Raspberry Pi 5 and Windows development.

## Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the game:

```bash
python main.py
```

Use `--headless` to avoid fullscreen mode while developing on Windows.

## Controls

- `1` `2` `3` `4`: Zone 1-4
- `Space`: AIR (hand raise)
- `R`: Restart after a session
- `Esc`: Quit

## Charts

Charts can optionally include a `training` block to repeat patterns and add a randomized final round.
See the example in [charts/sample_chart.json](charts/sample_chart.json).

## Structure

- `main.py` launches the trainer.
- `gpio_mock.py` hides the GPIO platform difference.
- `game/` contains input, judgment, chart loading, and rendering.
- `camera/air_detector.py` keeps camera support optional.
- `data/` handles CSV session logging and analytics output.
- `charts/sample_chart.json` provides the first playable chart.# rhythm-game-trainer
