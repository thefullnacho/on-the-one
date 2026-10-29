"""Song analysis for a beat-locked video: beat grid, energy map, word timings, phrase hits, chop onsets.

Run from the repo root with the project venv (needs ffmpeg; pip install faster-whisper librosa):
  python tools/analyze_song.py SONG.wav --out songs/<song> --bpm 135 --prompt "hook lines from the lyric sheet"
  python tools/analyze_song.py SONG.wav --out songs/<song> --rechunk 37.5-57.5,142.5-157.5 --prompt "..."   # retry weak spots
  python tools/analyze_song.py SONG.wav --out songs/<song> --phrase "one in the"                           # list every hit
  python tools/analyze_song.py SONG.wav --out songs/<song> --onsets 38-45                                  # measure a chop

Writes: <out>/mono.f32 (11025 Hz mono), <out>/grid.json {bpm, phase}, <out>/bars.txt,
<out>/words.json (Whisper segments with word timestamps).
"""
import argparse, json, os, subprocess, sys
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("song"); ap.add_argument("--out", default="analysis")
ap.add_argument("--bpm", type=float, default=None, help="from the Suno style tag if known; else estimated")
ap.add_argument("--prompt", default="", help="Whisper initial prompt: the hook lines, to anchor spellings")
ap.add_argument("--rechunk", default="", help="comma list of a-b windows to re-transcribe on their own")
ap.add_argument("--phrase", default="", help="print the start time of every occurrence of this phrase")
ap.add_argument("--onsets", default="", help="a-b: vocal-band onset peaks in this window, with 16th-grid phase")
ap.add_argument("--skip-whisper", action="store_true")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
SR = 11025
mono = os.path.join(a.out, "mono.f32")
if not os.path.exists(mono):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a.song, "-ac", "1", "-ar", str(SR), "-f", "f32le", mono], check=True)
x = np.fromfile(mono, dtype=np.float32)

# ---- beat grid: tempo (given or estimated) and the phase that best lines up with the onsets
if a.bpm is None:
    import librosa
    tempo, _ = librosa.beat.beat_track(y=x, sr=SR)
    a.bpm = float(np.atleast_1d(tempo)[0]); print(f"estimated bpm {a.bpm:.1f} (check it against the Suno tag)")
beat = 60 / a.bpm
hop = 128
fr = np.lib.stride_tricks.sliding_window_view(x, 1024)[::hop]
E = np.log1p((fr ** 2).sum(1)); on = np.maximum(0, np.diff(E))
best = max(((on[(np.arange(ph, len(x) / SR - 1, beat) * SR / hop).astype(int)].sum(), ph)
            for ph in np.arange(0, beat, 0.005)))
phase = round(float(best[1]), 3)
json.dump(dict(bpm=a.bpm, phase=phase), open(os.path.join(a.out, "grid.json"), "w"))
print(f"grid: {a.bpm} bpm, phase {phase}s")

# ---- energy per bar: the real section map (Suno often reorders the lyric sheet)
bar = 4 * beat
lines = []
for b in range(int(len(x) / SR / bar)):
    s = x[int(b * bar * SR):int((b + 1) * bar * SR)]
    S = np.abs(np.fft.rfft(s)) ** 2; f = np.fft.rfftfreq(len(s), 1 / SR)
    tot = 10 * np.log10(S.sum() + 1e-9); low = 10 * np.log10(S[f < 150].sum() + 1e-9)
    lines.append(f"{b:3d} {b * bar:6.1f}s tot{tot:5.1f} low{low:5.1f} " + "#" * int(max(0, tot - 60)))
open(os.path.join(a.out, "bars.txt"), "w").write("\n".join(lines))
print(f"energy map -> {a.out}/bars.txt ({len(lines)} bars)")


def whisper_model():
    from faster_whisper import WhisperModel
    try:
        return WhisperModel("medium.en", device="cpu", compute_type="int8")
    except Exception:
        return WhisperModel("small.en", device="cpu", compute_type="int8")


words_path = os.path.join(a.out, "words.json")
if not a.skip_whisper and not os.path.exists(words_path):
    m = whisper_model()
    segs, _ = m.transcribe(a.song, word_timestamps=True, vad_filter=False, initial_prompt=a.prompt or None)
    out = []
    for s in segs:
        out.append({"start": s.start, "end": s.end, "text": s.text,
                    "words": [{"w": w.word, "s": w.start, "e": w.end} for w in s.words]})
        print(f"{s.start:7.2f}-{s.end:7.2f} {s.text}")
    json.dump(out, open(words_path, "w"), indent=1)

if a.rechunk:
    # Whisper loses the thread in dense or chopped sections; a window on its own often comes back clean.
    # NOTE: a prompt containing the lyrics can bias it toward them: trust a timing only if two windows agree.
    m = whisper_model()
    for win in a.rechunk.split(","):
        lo, hi = map(float, win.split("-"))
        chunk = os.path.join(a.out, "chunk.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(lo), "-t", str(hi - lo), "-i", a.song,
                        "-ac", "1", "-ar", "16000", chunk], check=True)
        segs, _ = m.transcribe(chunk, word_timestamps=True, vad_filter=False, condition_on_previous_text=False,
                               initial_prompt=a.prompt or None)
        print(f"--- {lo}-{hi}")
        for s in segs:
            print(" ".join(f"{w.word.strip()}@{w.start + lo:.2f}" for w in s.words))

if a.phrase and os.path.exists(words_path):
    W = [w for s in json.load(open(words_path)) for w in s["words"]]
    toks = a.phrase.lower().split()
    norm = lambda w: w["w"].strip().lower().strip(",.!?")
    hits = [round(W[i]["s"], 2) for i in range(len(W) - len(toks) + 1)
            if all(norm(W[i + k]) == toks[k] for k in range(len(toks)))]
    print(f'"{a.phrase}": {len(hits)} hits {hits}')

if a.onsets:
    # peaks of spectral flux in the vocal band; the number in brackets is the phase on the 16th grid,
    # a constant phase across a run means a machine-gun chop you can remap time against
    lo, hi = map(float, a.onsets.split("-"))
    seg = x[int(lo * SR):int(hi * SR)]; n, hp = 512, 64
    fr = np.lib.stride_tricks.sliding_window_view(seg, n)[::hp] * np.hanning(n)
    S = np.abs(np.fft.rfft(fr, axis=1)); f = np.fft.rfftfreq(n, 1 / SR)
    flux = np.maximum(0, np.diff(np.log1p(S[:, (f > 700) & (f < 3000)]), axis=0)).sum(1)
    t = lo + np.arange(1, len(flux) + 1) * hp / SR; thr = np.percentile(flux, 90)
    peaks = []
    for i in range(1, len(flux) - 1):
        if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1] and (not peaks or t[i] - peaks[-1] > 0.06):
            peaks.append(t[i])
    print(" ".join(f"{p:.2f}({((p - phase) / (beat / 4)) % 1:.1f})" for p in peaks))
