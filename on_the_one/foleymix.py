"""Foley mix: the song plus each picked foley take (foley_picks.json), placed and timed, into mix_foley.wav for the conform.

usage: python -m on_the_one.foleymix songs/<song> [--rms-db -26]
       python -m on_the_one.foleymix songs/<song> --level-takes    # loudness-match every take on disk (the app plays these)
then:  python -m on_the_one.conform songs/<song> --audio mix_foley.wav ...

A take starts at its shot's start (a sheet cue's take at the cue's time). Per shot, in direction.json:
  foley_hits: [35.32]            the take's sound onsets land on these song times, one onset per hit, in order
  foley_keep: 1                   keep only the first N onsets (the rest of the take is dropped)
  foley_gain_db: -3               nudge one shot's level
  foley_extend_to: "end" | 160.0  loop the take's second half (an ambience bed: crickets) on to that time, fading out
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


def level(x, rms_db=-26.0, ceiling_db=-6.0):
    """Loudness match, not peak match: set the RMS of the part that is actually sounding (within 30 dB of the peak, in
    10 ms windows) to rms_db, then pull down if the peak would pass ceiling_db. A one-frame click and a three-second
    breeze then sit at the same perceived level; peak matching made the breeze loud and the click vanish."""
    hop = int(SR * 0.01)
    w = x[: len(x) // hop * hop].reshape(-1, hop, 2)
    rms = np.sqrt((w ** 2).mean(axis=(1, 2)))
    if not len(rms) or rms.max() <= 0:
        return x
    active = rms[rms > rms.max() * 10 ** (-30 / 20)]
    g = 10 ** (rms_db / 20) / max(np.sqrt((active ** 2).mean()), 1e-9)
    g = min(g, 10 ** (ceiling_db / 20) / max(np.abs(x).max(), 1e-9))
    return x * g


def level_file(path, rms_db=-26.0):
    """Loudness-match one take on disk, so auditioning in the app sounds like the mix (idempotent)."""
    y = level(decode(path), rms_db)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-", str(path)],
                   input=y.astype(np.float32).tobytes(), check=True)


def fade(seg, ms=5):
    n = min(len(seg) // 2, int(SR * ms / 1000))
    if n:
        ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
        seg[:n] *= ramp; seg[-n:] *= ramp[::-1]
    return seg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir")
    ap.add_argument("--rms-db", type=float, default=-28.0, help="each take's loudness (active RMS) before foley_gain_db")
    ap.add_argument("--level-takes", action="store_true", help="rewrite foley/*.flac loudness-matched, then stop")
    a = ap.parse_args()
    d = Path(a.song_dir)
    if a.level_takes:
        for f in sorted((d / "foley").glob("*.flac")):
            level_file(f, a.rms_db)
        return
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
        x = level(x, a.rms_db) * 10 ** (spec.get("foley_gain_db", 0) / 20)
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
        until = spec.get("foley_extend_to")
        if until:
            end = len(song) / SR if until == "end" else float(until)
            bed, xf = x[len(x) // 2:].copy(), int(SR * 0.3)
            at, tail = starts[cid] + len(x) / SR - xf / SR, []
            while at < end:
                tail.append((at, fade(bed.copy(), ms=300)))
                at += (len(bed) - xf) / SR
            if tail:                           # the bed fades out over the last 3 s before `end`
                n_out = int(SR * 3)
                for k, (t0, seg) in enumerate(tail):
                    i0 = int((end - 3 - t0) * SR)
                    if i0 < len(seg):
                        ramp = np.clip(1 - (np.arange(len(seg)) - i0) / n_out, 0, 1).astype(np.float32)[:, None]
                        seg *= ramp
                pieces += tail
        for at, seg in pieces:
            i0 = int(round(at * SR))
            if i0 >= len(bus):
                continue
            seg = seg[: len(bus) - max(i0, 0)][max(0, -i0):]
            bus[max(i0, 0): max(i0, 0) + len(seg)] += seg
        print(f"{cid}: {take} {len(ons)} onsets, {len(pieces)} placed"
              + (f" at {', '.join(f'{t + pre / SR:.2f}' for t, _ in pieces)}" if hits else ""))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",   # the foley alone,
                    "-c:a", "pcm_s16le", str(d / "foley_stem.wav")],                                           # to check by measurement
                   input=bus.astype(np.float32).tobytes(), check=True)
    mix = song + bus
    out = d / "mix_foley.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                    "-af", "alimiter=limit=0.98:level=disabled", "-c:a", "pcm_s16le", str(out)],
                   input=mix.astype(np.float32).tobytes(), check=True)
    print(f"{out} ({len(mix) / SR:.1f}s, foley peak {20 * np.log10(max(np.abs(bus).max(), 1e-9)):.1f} dBFS)")


if __name__ == "__main__":
    main()
