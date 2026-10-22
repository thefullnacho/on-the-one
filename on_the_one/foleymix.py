"""Foley mix: the song plus each picked foley take (foley_picks.json), placed and timed, into mix_foley.wav for the conform.

usage: python -m on_the_one.foleymix songs/<song> [--peak-db -10]
then:  python -m on_the_one.conform songs/<song> --audio mix_foley.wav ...

A take starts at its shot's start (a sheet cue's take at the cue's time). Per shot, in direction.json:
  foley_hits: [35.32]            the take's sound onsets land on these song times, one onset per hit, in order
  foley_keep: 1                   keep only the first N onsets (the rest of the take is dropped)
  foley_gain_db: -3               nudge one shot's level
Onsets come from the take's own envelope, so a click that sounds right but lands early moves without re-rendering.
"""
import argparse, json, subprocess
from pathlib import Path
import numpy as np

SR = 48000


def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()


def onsets(x, win=0.01, gap=0.08):
    """Sample indexes where the envelope jumps up out of quiet: the start of each tap, pop or boing."""
    hop = int(SR * win)
    env = np.abs(x).max(axis=1)[: len(x) // hop * hop].reshape(-1, hop).max(axis=1)
    if not len(env) or env.max() <= 0:
        return []
    hi, lo, out, armed = 0.3 * env.max(), 0.15 * env.max(), [], True
    for i, v in enumerate(env):
        if armed and v >= hi and (not out or (i * hop - out[-1]) > gap * SR):
            out.append(i * hop); armed = False
        elif v < lo:
            armed = True
    return out


def fade(seg, ms=5):
    n = min(len(seg) // 2, int(SR * ms / 1000))
    if n:
        ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
        seg[:n] *= ramp; seg[-n:] *= ramp[::-1]
    return seg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir")
    ap.add_argument("--peak-db", type=float, default=-10.0, help="each take's peak level before foley_gain_db")
    a = ap.parse_args()
    d = Path(a.song_dir)
    shots = json.loads((d / "shots.json").read_text())
    direction = json.loads((d / "direction.json").read_text())["shots"]
    picks = json.loads((d / "foley_picks.json").read_text()) if (d / "foley_picks.json").exists() else {}
    starts = {s["id"]: s["start"] for s in shots["shots"]} | {c["id"]: c["time"] for c in shots.get("cues", [])}
    song = decode(next(d.glob("audio.*")))
    bus = np.zeros_like(song)
    pre = int(SR * 0.02)                      # keep 20 ms before each onset so the attack is not clipped
    for cid, take in sorted(picks.items()):
        if not take or cid not in starts:
            continue
        x = decode(d / "foley" / take)
        spec = direction.get(cid, {})
        x *= 10 ** ((a.peak_db + spec.get("foley_gain_db", 0)) / 20) / max(np.abs(x).max(), 1e-6)
        ons = onsets(x)
        hits, keep = spec.get("foley_hits"), spec.get("foley_keep")
        pieces = []                           # (song time, audio)
        if (hits or keep) and ons:
            # more hits than onsets (two boings, four "down"s): the take's sounds repeat in order
            n = min(len(hits), keep or 10**6) if hits else min(len(ons), keep)
            for i in range(n):
                j = i % len(ons)
                a0 = max(0, ons[j] - pre)
                a1 = max(a0, ons[j + 1] - pre) if j + 1 < len(ons) else len(x)
                at = hits[i] - pre / SR if hits else starts[cid] + a0 / SR
                pieces.append((at, fade(x[a0:a1].copy())))
        else:
            pieces.append((starts[cid], fade(x)))
        for at, seg in pieces:
            i0 = int(round(at * SR))
            if i0 >= len(bus):
                continue
            seg = seg[: len(bus) - max(i0, 0)][max(0, -i0):]
            bus[max(i0, 0): max(i0, 0) + len(seg)] += seg
        print(f"{cid}: {take} {len(ons)} onsets, {len(pieces)} placed"
              + (f" at {', '.join(f'{t + pre / SR:.2f}' for t, _ in pieces)}" if hits else ""))
    mix = song + bus
    out = d / "mix_foley.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                    "-af", "alimiter=limit=0.98:level=disabled", "-c:a", "pcm_s16le", str(out)],
                   input=mix.astype(np.float32).tobytes(), check=True)
    print(f"{out} ({len(mix) / SR:.1f}s, foley peak {20 * np.log10(max(np.abs(bus).max(), 1e-9)):.1f} dBFS)")


if __name__ == "__main__":
    main()
