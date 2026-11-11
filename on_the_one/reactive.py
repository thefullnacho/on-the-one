"""Reactive FX layer: beat-reactive picture movement, applied in the conform.

The analysis (beat grid, Whisper word timings) already knows where the song
is; this module adds the other half -- *when* the drums hit -- and turns both
into ffmpeg filter expressions for one shot segment at a time.

Design (parameter tables adapted from OneShot MV, fv/fx.py, MIT -- see the
attribution note in PRESETS; the code here is reimplemented for this repo,
not pasted):
- kick onsets -> zoompan punch-in (zoom); snare onsets -> eq brightness
  flash + saturation lift (an exposure lift standing in for OneShot's bloom);
  downbeats get an extra punch; the beats after the drop strobe.
- PRESETS are OneShot's INTENSE / SUBTLE dicts, rescaled for a 1408x768
  cut: zoom is in fractions of the frame (0.05 == a 5% punch-in), flash
  in eq-brightness units (-1..1).
- Everything is a gated exponential decay per onset: amp * gte(t, t0) *
  exp(-(t - t0)/tau). Gates use gte() inside ffmpeg's documented
  single-quote option quoting (sign() is NOT an ffmpeg function).
- The layer is a post-process: it changes pixels only. The conform still
  emits exactly round(end*fps) - round(start*fps) frames per shot, so cut
  times are byte-identical with the layer on or off (see
  tests/test_reactive.py::TestConformInvariance).

What is NOT here (deliberate, per docs/APP_FLOW.md): word-timed freezes
(no ffmpeg mechanism in this change), true blur-based bloom (approximated
as exposure/saturation lift), RGB split, twitch/shake translation
(zoompan evaluates x/y once per input, not per frame; random() is constant
per graph -- both probed on this box), echo/ghost/trail. Demucs stem
separation is a separate pick and is not a dependency of this module.
"""
import json
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np

# Attribution: the preset tables and the kick->zoom / snare->flash mapping
# are adapted from OneShot MV (github.com/VisualFrisson/ComfyUI-OneShotMV,
# fv/fx.py and fv/audiofx.py), MIT License, Copyright (c) VisualFrisson.
# OneShot's per-frame fx_params are: flash = 0.3*cfg.flash*snare*lv,
# rgb = 0.5*cfg.rgb*snare, zoom = cfg.zoom*kick, bloom =
# 0.35*cfg.bloom*snare*lv, plus a shot-start punch, a drop strobe and an
# echo trail -- the first four plus the punch and the strobe are what this
# module implements. Its band envelopes are 100 Hz hop, bands
# kick 30-150 Hz / snare 1500-6000 Hz / hat 7000-16000 Hz, onsets by an
# adaptive median peak-pick with a 60 ms minimum gap, decays
# tau 0.12 / 0.10 / 0.05 s. This file reimplements that analysis in
# numpy (no librosa -- the repo does not depend on it) and re-expresses
# the mapping as ffmpeg filter fragments.
PRESETS = {
    # OneShot INTENSE, as shipped: flash 0.55, rgb 9, zoom 0.05, shake 0,
    # twitch 8, bloom 1.0, ghost 0.6, insert_flash 0.75, drop_strobe 1.0,
    # trail 0, echo 1.0. (rgb, twitch, ghost, echo: not implementable in
    # this filter chain -- see the module docstring.)
    "intense": {"flash": 0.55, "zoom": 0.05, "bloom": 1.0, "drop_strobe": 1.0},
    "subtle": {"flash": 0.30, "zoom": 0.025, "bloom": 0.6, "drop_strobe": 0.4},
}

LEVELS = ("off", "subtle", "intense")
ANALYSIS_SR = 22050
N_FFT = 2048
# The kick/snare envelopes run at 100 Hz (OneShot audiofx): one STFT frame
# per 220 samples. _band_env and _onsets MUST agree on this hop, or onset
# times come out scaled -- that exact mismatch (220-sample frames timed at
# 441-sample steps) was caught by test_kick_snare_detection.
ENV_HOP = 220
ENV_HOP_S = ENV_HOP / ANALYSIS_SR  # 0.01 s per envelope frame
TAU_KICK = 0.12
TAU_SNARE = 0.10
VERSION = 1


# --------------------------------------------------------------------------
# analysis: kick/snare onsets, beat grid, drop, loudness curve
# --------------------------------------------------------------------------
def _mel_filterbank(n_mels=40, fmin=20.0, fmax=11025.0):
    """Slaney-style mel filterbank over the N_FFT rfft bins (OneShot beat.py)."""
    def hz_to_mel(f):
        return 2595 * np.log10(1 + f / 700.0)
    def mel_to_hz(m):
        return 700 * (10 ** (m / 2595.0) - 1)
    n_freq = N_FFT // 2 + 1
    mel_pts = np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2)
    hz_pts = mel_to_hz(mel_pts)
    bins = np.floor((N_FFT + 1) * hz_pts / ANALYSIS_SR).astype(int)
    fb = np.zeros((n_mels, n_freq))
    for m in range(1, n_mels + 1):
        f_left, f_center, f_right = bins[m - 1], bins[m], bins[m + 1]
        for k in range(f_left, f_center):
            fb[m - 1, k] = (k - f_left) / max(f_center - f_left, 1)
        for k in range(f_center, f_right):
            fb[m - 1, k] = (f_right - k) / max(f_right - f_center, 1)
    return fb


def _band_env(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Log-energy envelope of one frequency band (OneShot audiofx).

    One STFT frame per ENV_HOP samples (100 fps), 2048-sample Hann window,
    plain band magnitude sum (no mel weighting -- the bands are narrow).
    """
    win = np.hanning(N_FFT)
    hop = ENV_HOP
    n = 1 + max(0, (len(x) - N_FFT) // hop)
    env = np.zeros(n)
    freqs = np.fft.rfftfreq(N_FFT, 1 / ANALYSIS_SR)
    sel = (freqs >= lo) & (freqs <= hi)
    for i in range(n):
        f = x[i * hop:i * hop + N_FFT] * win
        env[i] = np.log((np.abs(np.fft.rfft(f))[sel].sum()) + 1e-6)
    return env


def _onsets(env: np.ndarray, sens: float = 1.0) -> list[dict]:
    """Peak-picked onsets of a log-energy curve -> [{t, s}] (OneShot _onsets).

    t in seconds (envelope runs at 100 Hz, ENV_HOP_S per frame), s strength
    0..1, peak-picked with an adaptive median threshold and a 60 ms minimum
    gap (6 frames at 100 Hz). t is the centre of the analysis window, not
    its start: a burst only fills a window once the window slides over it,
    so the raw frame start reads up to a full window (~93 ms) early -- the
    centre keeps onsets within ~half a window of the hit, which is what the
    FX has to land on.
    """
    d = np.maximum(0.0, np.diff(env, prepend=env[:1]))
    if d.max() <= 0:
        return []
    k = 15
    pad = np.pad(d, k, mode="edge")
    med = np.array([np.median(pad[i:i + 2 * k + 1]) for i in range(len(d))])
    thr = med + sens * 0.5 * d.std()
    centre = N_FFT / (2.0 * ANALYSIS_SR)
    out, last = [], -10
    for i in range(1, len(d) - 1):
        if d[i] > thr[i] and d[i] >= d[i - 1] and d[i] >= d[i + 1] and i - last >= 6:
            out.append((i, float(d[i])))
            last = i
    if out:
        m = np.percentile([s for _, s in out], 95) or 1.0
        out = [(i, min(1.0, s / m)) for i, s in out]
    return [{"t": round(i * ENV_HOP_S + centre, 4), "s": round(s, 3)} for i, s in out]


def _bar_low_db(x: np.ndarray, bpm: float, phase: float, duration: float) -> np.ndarray:
    """Per-bar mean low-band (30-250 Hz) energy in dB, for the drop heuristic."""
    bar = 4 * 60.0 / bpm
    nbars = max(1, int(duration / bar))
    fb = _mel_filterbank()
    out = []
    for b in range(nbars):
        t0 = phase + b * bar
        i0 = int(t0 * ANALYSIS_SR)
        seg = x[i0:i0 + int(bar * ANALYSIS_SR)]
        if len(seg) < N_FFT:
            out.append(-80.0)
            continue
        mags = []
        for j in range(0, max(1, len(seg) - N_FFT), N_FFT):
            mag = np.abs(np.fft.rfft(seg[j:j + N_FFT] * np.hanning(N_FFT)))
            mags.append((fb * mag).sum(axis=1)[:6].mean())
        out.append(float(np.log10(np.mean(mags) + 1e-9) * 20) if mags else -80.0)
    return np.array(out)


def _find_drop(bar_low_db: np.ndarray) -> float | None:
    """First bar after the first fifth whose low-band energy jumps >= 5 dB over
    the mean of the two bars before it (a loud section arriving)."""
    if len(bar_low_db) < 8:
        return None
    start = max(2, int(len(bar_low_db) * 0.2))
    for i in range(start, len(bar_low_db)):
        before = float(np.mean(bar_low_db[max(0, i - 2):i]))
        if bar_low_db[i] - before >= 5.0:
            return round(float(i), 4)  # placeholder; converted to seconds by caller
    return None


def _rms_curve(x: np.ndarray, bpm: float, phase: float, duration: float) -> list[float]:
    """Per-bar RMS loudness 0..1 (peak-normalised), for the quiet-verse scale."""
    bar = 4 * 60.0 / bpm
    nbars = max(1, int(duration / bar))
    vals = []
    for b in range(nbars):
        i0 = int((phase + b * bar) * ANALYSIS_SR)
        seg = x[i0:i0 + int(bar * ANALYSIS_SR)]
        vals.append(float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0)
    m = max(vals) or 1.0
    return [round(v / m, 3) for v in vals]


def analyze_events(x: np.ndarray, sr: int, bpm: float, phase: float,
                   duration: float, drop: float | None = None) -> dict:
    """x: mono float32 (any sr; resampled to 22050). Returns the events dict
    written to songs/<song>/events.json. `drop` (seconds) overrides the
    heuristic -- conform passes the "(Beat drops)" cue time when there is one.
    """
    if sr != ANALYSIS_SR:
        n = int(len(x) * ANALYSIS_SR / sr)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
        sr = ANALYSIS_SR
    x = x / (np.abs(x).max() or 1.0)
    kick = _onsets(_band_env(x, 30.0, 150.0))
    snare = _onsets(_band_env(x, 1500.0, 6000.0))
    beat = 60.0 / bpm
    beats = [round(phase + k * beat, 4) for k in range(int((duration - phase) / beat))]
    downbeats = beats[::4]
    bar_low = _bar_low_db(x, bpm, phase, duration)
    bar = 4 * 60.0 / bpm
    if drop is None:
        di = _find_drop(bar_low)
        drop = round(phase + float(di) * bar, 4) if di is not None else None
    rms = _rms_curve(x, bpm, phase, duration)
    return {"version": VERSION, "bpm": bpm, "phase": phase, "duration": round(duration, 3),
            "kick": kick, "snare": snare, "beats": beats, "downbeats": downbeats,
            "drop": drop, "rms": rms}


def analyze_audio_file(path, bpm: float, phase: float, duration: float,
                       drop: float | None = None) -> dict:
    """Decode an audio file to mono float32 22050 via ffmpeg, then analyze_events."""
    with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as tmp:
        raw = tmp.name
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1",
                    "-ar", str(ANALYSIS_SR), "-f", "f32le", raw],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    x = np.fromfile(raw, dtype=np.float32)
    return analyze_events(x, ANALYSIS_SR, bpm, phase, duration, drop=drop)


def energy_at(events: dict, t0: float, t1: float) -> float:
    """Mean per-bar RMS loudness over [t0, t1] (0..1); 1.0 when unknown."""
    rms = events.get("rms") or []
    bpm = events.get("bpm") or 120.0
    phase = events.get("phase") or 0.0
    bar = 4 * 60.0 / bpm
    idx = [int((t - phase) // bar) for t in np.linspace(t0, t1, 5)]
    vals = [rms[i] for i in idx if 0 <= i < len(rms)]
    return float(np.mean(vals)) if vals else 1.0


# --------------------------------------------------------------------------
# ffmpeg expression builders.
# Terms are (onset_time, amplitude, tau): amp * exp(-(t - t0)/tau) once t
# passes t0. Gates use gte() (comma inside ffmpeg's documented single-quote
# option quoting -- verified on this box: the quoted comma survives the
# filtergraph parser, and sign() is NOT an ffmpeg function). exp() of a
# negative (t - t0) is harmless because the gate is 0 there.
# --------------------------------------------------------------------------
def _decay_sum(terms: list[tuple[float, float, float]], var: str) -> str:
    """Sum of gated exponential decays as one ffmpeg expression on `var`."""
    parts = []
    for t0, amp, tau in terms:
        if abs(amp) < 1e-4:
            continue
        parts.append(f"{amp:.4f}*gte({var},{t0:.3f})*exp(-({var}-{t0:.3f})/{tau:.3f})")
    return " + ".join(parts) if parts else "0"


def envelope_value(terms: list[tuple[float, float, float]], t: float) -> float:
    """The same envelope evaluated in Python (for tests / inspection)."""
    return float(sum(amp * math.exp(-(t - t0) / tau) if t >= t0 else 0.0
                     for t0, amp, tau in terms))


def shot_filters(events: dict, shot: dict, fps: int, size: tuple[int, int],
                 preset: str = "subtle", intensity: float = 1.0,
                 card: bool = False) -> str:
    """ffmpeg filter fragment (leading comma) applying the reactive layer to
    one shot segment, or "" when the preset is off / there are no events.

    shot: a shots.json entry (start/end/duration in song seconds).
    intensity: per-shot multiplier -- the global preset strength, times the
    shot's loudness (quiet verses breathe: level 1.0 loud, ~0.25 quiet,
    after OneShot's quiet_scale). card: placeholder text cards get the
    layer at half strength so the text stays legible.
    """
    if preset == "off" or not events:
        return ""
    cfg = PRESETS[preset]
    t0, t1 = float(shot["start"]), float(shot["end"])
    dur = t1 - t0
    I = float(intensity) * (0.25 + 0.75 * energy_at(events, t0, t1))
    if card:
        I *= 0.5  # the animatic card is for reading; never flash it at full strength
    if I <= 0.01:
        return ""
    # weak onsets (s < 0.25 -- the long tail of bass-note and hat detections
    # on real songs) are dropped: on Green Flag that cuts ~580 of 729 kicks,
    # each worth a sub-visible <0.01 zoom, and keeps the ffmpeg expression
    # short enough to read in a log line.
    rel = lambda ev: [(e["t"] - t0, e["s"]) for e in ev
                      if -0.5 <= e["t"] - t0 < dur and e["s"] >= 0.25]
    kick = rel(events.get("kick", []))
    snare = rel(events.get("snare", []))
    downs = [t - t0 for t in events.get("downbeats", []) if 0 <= t - t0 < dur]
    bar = 4 * 60.0 / max(float(events.get("bpm") or 120.0), 1.0)
    drop = events.get("drop")
    drop_beats = []
    # the strobe runs for the 2 bars after the drop, so it covers the shot
    # the drop lands in AND the shot(s) after it -- a drop 0.26 s before a
    # shot boundary (Green Flag: drop 118.14 s, Bridge ends 118.4) strobes
    # the next Chorus shot, not the tail of the Bridge.
    if drop is not None and t0 - 2 * bar <= drop < t1:
        drop_beats = [b - t0 for b in events.get("beats", [])
                      if drop <= b < drop + 2 * bar and 0 <= b - t0 < dur]
    # -- zoom (zoompan z, var `time`): kick punch-in, extra punch on
    #    downbeats (APP_FLOW), strobe zoom on the beats after the drop,
    #    and a shot-start punch over the first ~3 frames (OneShot).
    z_terms = ([(t, cfg["zoom"] * s * I, TAU_KICK) for t, s in kick]
               + [(t, cfg["zoom"] * 1.2 * I, 0.15) for t in downs]
               + [(t, cfg["zoom"] * 1.3 * cfg["drop_strobe"] * I, 0.12) for t in drop_beats]
               + [(0.0, cfg["zoom"] * 0.8 * I, 0.03)])
    z = f"1+{_decay_sum(z_terms, 'time')}" if z_terms else "1"
    # -- flash / bloom (eq, var `t`, eval=frame): OneShot flash is
    #    0.3*flash*snare*loud and bloom is 0.35*bloom*snare*loud; the eq
    #    brightness carries both (true blur-bloom is approximated as an
    #    exposure lift -- see module docstring). Shot-start flash and the
    #    drop strobe ride on top. Saturation lifts with the flash, as in
    #    OneShot's warm over-exposure.
    F, B = cfg["flash"], cfg["bloom"]
    f_terms = ([(t, 0.30 * F * s * I, TAU_SNARE) for t, s in snare]
               + [(t, 0.175 * B * s * I, TAU_SNARE) for t, s in snare]
               + [(t, 0.75 * F * cfg["drop_strobe"] * I, 0.10) for t in drop_beats]
               + [(0.0, 0.90 * F * I, 0.04)])
    s_terms = [(t, 0.25 * 0.30 * F * s * I, TAU_SNARE) for t, s in snare]
    bright = _decay_sum(f_terms, "t")
    sat = f"1+{_decay_sum(s_terms, 't')}" if s_terms else "1"
    w, h = size
    # expressions containing gte() carry commas, so each is single-quoted --
    # ffmpeg's documented option quoting (verified: a gated flash lands on
    # the onset frame and decays, see tests/test_reactive.py).
    return (f",zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={w}x{h}:fps={fps}"
            f",eq=brightness='{bright}':saturation='{sat}':eval=frame")


def resolve_level(direction: dict, cli_level: str | None) -> dict:
    """fx level resolution: --fx wins, else direction.json "fx" {"level",
    "sections": {section_name: level}}, else off. Returns {"level": ...,
    "sections": {...}} with levels validated to off/subtle/intense."""
    fx = direction.get("fx") or {}
    level = cli_level or fx.get("level") or "off"
    if level not in LEVELS:
        raise SystemExit(f"unknown fx level {level!r} (off/subtle/intense)")
    sections = {k: v for k, v in (fx.get("sections") or {}).items() if v in LEVELS}
    return {"level": level, "sections": sections}


def write_events(path, events: dict) -> None:
    Path(path).write_text(json.dumps(events, indent=1, ensure_ascii=False))


def read_events(path) -> dict | None:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else None
