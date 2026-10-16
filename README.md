# on-the-one

Song in, beat-locked music video out — on open weights, on your own GPU.

Work in progress for the Comfy #OpenSourceHiggsfield build competition (deadline 2026-10-30).

- `on_the_one/planner.py` — lyric sheet + Whisper word timings + beat grid -> `shots.json`: every cut on a bar line,
  sections from the lyric sheet's `[Section]` tags, render lengths LTX accepts (1 + 8n frames).
- `songs/green_flag/` — example inputs.
