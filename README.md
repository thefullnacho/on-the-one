# on-the-one

Song in, beat-locked music video out — on open weights, on your own GPU.

Work in progress for the Comfy #OpenSourceHiggsfield build competition (deadline 2026-10-30).

- `on_the_one/planner.py` — lyric sheet + Whisper word timings + beat grid -> `shots.json`: every cut on a bar line,
  sections from the lyric sheet's `[Section]` tags, render lengths LTX accepts (1 + 8n frames).
- `on_the_one/conform.py` — shots.json + clips/keyframes -> one cut over the original song; frame-exact cuts, placeholder cards for unrendered shots (a full-length animatic before any render).
- `on_the_one/render.py` — via the Comfy SDK: keyframe options per shot (character placed from a reference), pick one,
  then the clip: LTX-2.5 audio-to-video on that shot's exact audio slice.
- `workflows/` + `tools/build_workflows.py` — the two graphs the app runs, built with every API node stripped and
  the result re-checked (Open Weights Only by construction), with named slots instead of node ids.
- `songs/green_flag/` — example song: lyric sheet, timings, shot list, direction, picked keyframes.
