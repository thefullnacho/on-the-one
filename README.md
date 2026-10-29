# on-the-one

Song in, beat-locked music video out — on open weights, on your own GPU.

https://github.com/user-attachments/assets/0a0d6198-28e6-448c-aea8-d0d1967fe706

Built for the Comfy #OpenSourceHiggsfield build competition, October 2026. The example film, Green Flag (`songs/green_flag/`), was made end to end with it.

- `on_the_one/planner.py` — lyric sheet + Whisper word timings + beat grid -> `shots.json`: every cut on a bar line,
  sections from the lyric sheet's `[Section]` tags, render lengths LTX accepts (1 + 8n frames).
- `on_the_one/conform.py` — shots.json + clips/keyframes -> one cut over the original song; frame-exact cuts, placeholder cards for unrendered shots (a full-length animatic before any render).
- `on_the_one/render.py` — via the Comfy SDK: keyframe options per shot (character placed from a reference), pick one,
  then the clip: LTX-2.5 audio-to-video on that shot's exact audio slice.
- `workflows/` + `tools/build_workflows.py` — the two graphs the app runs, built with every API node stripped and
  the result re-checked (Open Weights Only by construction), with named slots instead of node ids.
- `on_the_one/app.py` — the app: `python -m on_the_one.app`, then open http://127.0.0.1:8700. Screen one is the live
  animatic: the song plays against its shots (clip, keyframe, or card), with a notes pad per shot that saves as you type.
- `songs/green_flag/` — example song: lyric sheet, timings, shot list, direction, picked keyframes.

## Run it

Needs Python 3.10+ and ffmpeg. Rendering also needs ComfyUI on an NVIDIA GPU (built on one RTX 5080, 16 GB).

```
git clone https://github.com/thefullnacho/on-the-one && cd on-the-one
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m on_the_one.app          # then open http://127.0.0.1:8700
```

The example song opens with its shot list, direction, all 44 picked keyframes and a 128k mp3 of the song, so the
animatic plays. The rendered clips are not in the repo; the finished film is the video above.

### Your own song

1. Make `songs/<song>/` with `audio.wav` and `lyrics.txt` (a lyric sheet with `[Section]` tags, like
   `songs/green_flag/lyrics.txt`).
2. Analyze it (Whisper word timings on the CPU, beat grid; pass `--bpm` if you know it):
   ```
   .venv/bin/pip install faster-whisper librosa
   .venv/bin/python tools/analyze_song.py songs/<song>/audio.wav --out songs/<song> --prompt "your hook lines"
   ```
3. Plan the shots: `.venv/bin/python -m on_the_one.planner songs/<song>`
4. Open the app and pick your song: the beat-locked animatic plays before anything is rendered.

### Rendering

`render.py` drives your own ComfyUI through the Comfy SDK, via `comfy-api-proxy` (0.1.6) in front of it
(`COMFY_BASE_URL`, default `http://127.0.0.1:8189`, no key). The workflows in `workflows/` load FLUX 3 Klein
(keyframes) and LTX-2.5 (audio-to-video, foley, upscale); the model file names are in the workflow JSONs.

```
.venv/bin/python -m on_the_one.render songs/<song> keyframes <shot ids | pending>   # 3 options per shot
.venv/bin/python -m on_the_one.render songs/<song> pick <shot> <seed>                # or pick in the app
.venv/bin/python -m on_the_one.render songs/<song> clips <shot ids>
.venv/bin/python -m on_the_one.conform songs/<song>                                  # the cut over the song
```
