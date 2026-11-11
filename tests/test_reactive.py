"""Tests for the reactive FX layer (on_the_one.reactive + conform --fx).

No GPU, no ComfyUI, no network: synthetic audio + ffmpeg testsrc fixtures.
Run from the repo root:  python -m unittest tests.test_reactive -v
"""
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from on_the_one import reactive  # noqa: E402

SR = 22050
BPM = 120.0
BEAT = 60.0 / BPM  # 0.5 s


def synth_song(seconds=16.0):
    """120 BPM: a 55 Hz kick burst every beat, a noise snare on beats 2 & 4
    of each bar (t = 1.0, 2.0, 3.0, ...), plus a quiet 440 Hz bed."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    x = 0.05 * np.sin(2 * np.pi * 440 * t)
    rng = np.random.default_rng(7)
    for k in range(int(seconds / BEAT)):
        t0 = k * BEAT
        i0 = int(t0 * SR)
        m = min(int(0.18 * SR), n - i0)
        env = np.exp(-np.arange(m) / (0.03 * SR))
        x[i0:i0 + m] += 0.9 * env * np.sin(2 * np.pi * 55 * np.arange(m) / SR)
    for t0 in np.arange(1.0, seconds, 2.0):
        i0 = int(t0 * SR)
        m = min(int(0.12 * SR), n - i0)
        noise = rng.standard_normal(m)
        # crude band-pass 1.5-6 kHz via FFT mask
        F = np.fft.rfft(noise)
        f = np.fft.rfftfreq(m, 1 / SR)
        F[(f < 1500) | (f > 6000)] = 0
        burst = np.fft.irfft(F, m) * np.exp(-np.arange(m) / (0.03 * SR))
        x[i0:i0 + m] += 0.7 * burst / (np.abs(burst).max() + 1e-9)
    x = x / np.abs(x).max()
    return x.astype(np.float32)


def write_wav(path, x, sr=SR):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def ffprobe_frames(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return int(out)


def frame_md5(path, frame=30):
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "f.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf",
                        f"select='eq(n\\,{frame})'", "-frames:v", "1", str(png)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return hashlib.md5(png.read_bytes()).hexdigest()


class TestEvents(unittest.TestCase):
    def test_kick_snare_detection(self):
        x = synth_song()
        ev = reactive.analyze_events(x, SR, BPM, 0.0, 16.0, drop=None)
        kicks = [k["t"] for k in ev["kick"]]
        snares = [s["t"] for s in ev["snare"]]
        # a kick every 0.5 s from 0: expect ~30 of the 32, each within 70 ms
        expect_k = [k * BEAT for k in range(32)]
        hits = sum(1 for e in expect_k if min((abs(e - k) for k in kicks), default=9) < 0.07)
        self.assertGreaterEqual(hits, 28, f"kick hits {hits}/32, got {kicks[:8]}")
        expect_s = [1.0 + 2.0 * k for k in range(8)]
        hits = sum(1 for e in expect_s if min((abs(e - s) for s in snares), default=9) < 0.07)
        self.assertGreaterEqual(hits, 6, f"snare hits {hits}/8, got {snares[:8]}")
        self.assertEqual(len(ev["beats"]), 32)
        self.assertEqual(len(ev["downbeats"]), 8)
        self.assertTrue(all(0.0 <= s <= 1.0 for k in ev["kick"] for s in [k["s"]]))

    def test_silence_yields_no_events(self):
        ev = reactive.analyze_events(np.zeros(SR * 4, dtype=np.float32), SR, BPM, 0.0, 4.0)
        self.assertEqual(ev["kick"], [])
        self.assertEqual(ev["snare"], [])
        self.assertIsNone(ev["drop"])


class TestFilters(unittest.TestCase):
    def setUp(self):
        x = synth_song(8.0)
        self.ev = reactive.analyze_events(x, SR, BPM, 0.0, 8.0, drop=4.0)
        self.shot = {"id": "verse1-1", "section": "Verse", "start": 0.0, "end": 4.0,
                     "duration": 4.0, "bars": 2.0}

    def test_off_is_empty(self):
        self.assertEqual(reactive.shot_filters(self.ev, self.shot, 24, (320, 180), "off"), "")
        self.assertEqual(reactive.shot_filters(None, self.shot, 24, (320, 180), "subtle"), "")

    def test_fragment_shape(self):
        frag = reactive.shot_filters(self.ev, self.shot, 24, (320, 180), "subtle")
        self.assertIn("zoompan=z=", frag)
        self.assertIn("eq=brightness=", frag)
        self.assertIn("eval=frame", frag)
        self.assertIn("s=320x180", frag)
        # gated expressions carry commas, so each must be single-quoted
        # (ffmpeg's documented option quoting); unquoted commas would split
        # the filtergraph and fail to parse
        self.assertIn("brightness='", frag)
        self.assertIn("saturation='", frag)
        self.assertIn("z='", frag)
        intense = reactive.shot_filters(self.ev, self.shot, 24, (320, 180), "intense")
        self.assertNotEqual(frag, intense)

    def test_drop_strobe_crosses_shot_boundary(self):
        # drop 0.26 s before this shot starts: the 2-bar strobe belongs to
        # the shot after the drop, not the shot the drop lands in
        ev = dict(self.ev)
        ev["drop"] = self.shot["start"] - 0.26
        ev["beats"] = [self.shot["start"] + 0.24 + 0.5 * k for k in range(8)]
        frag = reactive.shot_filters(ev, self.shot, 24, (320, 180), "intense")
        self.assertIn("gte(time,0.240)", frag)  # a strobe zoom term inside the shot
        ev["drop"] = self.shot["start"] + 10.0  # drop long after the shot: no strobe
        frag2 = reactive.shot_filters(ev, self.shot, 24, (320, 180), "intense")
        self.assertNotIn("gte(time,0.240)", frag2)

    def test_envelope_decays(self):
        terms = [(0.5, 0.05, 0.12)]
        self.assertAlmostEqual(reactive.envelope_value(terms, 0.5), 0.05, places=3)
        self.assertLess(reactive.envelope_value(terms, 1.0), 0.001)
        self.assertEqual(reactive.envelope_value(terms, 0.4), 0.0)

    def test_filter_graph_parses_and_preserves_frames(self):
        frag = reactive.shot_filters(self.ev, self.shot, 24, (320, 180), "intense")
        out = Path(tempfile.mkdtemp()) / "fx.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc=duration=2:size=320x180:rate=24",
                        "-vf", f"scale=320:180:force_original_aspect_ratio=increase,crop=320:180{frag}"
                        f",fps=24,format=yuv420p", "-frames:v", "48", "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.assertEqual(ffprobe_frames(out), 48)


class TestConformInvariance(unittest.TestCase):
    """A 3-shot fake song (clip / keyframe / card): --fx off/subtle/intense must
    produce identical frame counts and durations, but different pixels."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name) / "song"
        (self.d / "clips").mkdir(parents=True)
        (self.d / "keyframes").mkdir()
        # 6 s synthetic song audio
        write_wav(self.d / "audio.wav", synth_song(6.0))
        shots = {"bpm": BPM, "phase": 0.0, "bar": 2.0, "fps": 24, "duration": 6.0,
                 "shots": [
                     {"id": "s1", "section": "Verse", "part": "1/3", "start": 0.0, "end": 2.0,
                      "duration": 2.0, "bars": 1.0, "render_frames": 49, "lines": ["line one"]},
                     {"id": "s2", "section": "Verse", "part": "2/3", "start": 2.0, "end": 4.0,
                      "duration": 2.0, "bars": 1.0, "render_frames": 49, "lines": ["line two"]},
                     {"id": "s3", "section": "Chorus", "part": "3/3", "start": 4.0, "end": 6.0,
                      "duration": 2.0, "bars": 1.0, "render_frames": 49, "lines": ["line three"]}]}
        (self.d / "shots.json").write_text(json.dumps(shots))
        (self.d / "direction.json").write_text(json.dumps({"shots": {}}))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc=duration=2:size=320x180:rate=24",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        str(self.d / "clips" / "s1.mp4")],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        from PIL import Image
        Image.new("RGB", (320, 180), (40, 90, 140)).save(self.d / "keyframes" / "s2.png")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        self.env = env

    def tearDown(self):
        self.tmp.cleanup()

    def _conform(self, level):
        out = self.d / f"cut_{level}.mp4"
        r = subprocess.run([sys.executable, "-m", "on_the_one.conform", str(self.d),
                            "--out", str(out), "--size", "320x180", "--fx", level],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return out

    def test_cut_times_unchanged(self):
        outs = {lv: self._conform(lv) for lv in ("off", "subtle", "intense")}
        frames = {lv: ffprobe_frames(p) for lv, p in outs.items()}
        self.assertEqual(frames["off"], 144)  # 6 s * 24 fps
        self.assertEqual(frames, {"off": 144, "subtle": 144, "intense": 144})
        # ... but the pixels do move: a flash frame differs with the layer on
        m = {lv: frame_md5(p, frame=30) for lv, p in outs.items()}
        self.assertNotEqual(m["off"], m["subtle"])
        self.assertNotEqual(m["off"], m["intense"])


class TestLevels(unittest.TestCase):
    def test_resolve_level(self):
        self.assertEqual(reactive.resolve_level({}, None)["level"], "off")
        self.assertEqual(reactive.resolve_level({"fx": {"level": "subtle"}}, None)["level"], "subtle")
        self.assertEqual(reactive.resolve_level({"fx": {"level": "subtle"}}, "intense")["level"], "intense")
        self.assertEqual(reactive.resolve_level({"fx": {"level": "subtle", "sections": {"Chorus": "intense"}}}, None)["sections"],
                         {"Chorus": "intense"})
        with self.assertRaises(SystemExit):
            reactive.resolve_level({"fx": {"level": "loud"}}, None)


if __name__ == "__main__":
    unittest.main()
