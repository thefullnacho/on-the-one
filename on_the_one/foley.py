"""Foley takes: each shot's direction "foley" line (and the lyric sheet's sound cues) -> N takes with LTX-2.5 text-to-audio.
Takes land in songs/<song>/foley/<id>_<seed>.flac for picking by ear; nothing is mixed in here.

usage: python -m on_the_one.foley songs/<song> [ids...] [--takes 2]     # no ids: every shot with a foley line, plus sound cues

Stage directions that describe the music itself ("Beat drops", "Riser builds") are skipped: the song already has them.
"""
import argparse, json, os, time
from pathlib import Path
from comfy_sdk import Comfy
from on_the_one.render import fill, run

MUSICAL = ("beat", "riser", "snare", "drop", "bass", "drum", "build")
NEGATIVE = "music, melody, singing, vocals, speech, talking, drums, beat"


def cues(d):
    shots = json.loads((d / "shots.json").read_text())
    direction = json.loads((d / "direction.json").read_text())["shots"]
    out = {}
    for sh in shots["shots"]:
        line = direction.get(sh["id"], {}).get("foley")
        if line:
            out[sh["id"]] = {"text": line, "seconds": sh["duration"]}
    for c in shots.get("cues", []):
        if not any(w in c["text"].lower() for w in MUSICAL):
            out[c["id"]] = {"text": c["text"], "seconds": 2.0}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--takes", type=int, default=2)
    a = ap.parse_args()
    d = Path(a.song_dir)
    todo = cues(d)
    ids = a.ids or list(todo)
    (d / "foley").mkdir(exist_ok=True)
    os.environ.setdefault("COMFY_BASE_URL", "http://127.0.0.1:8189")
    client = Comfy()
    for n, cid in enumerate(ids):
        c = todo[cid]
        for k in range(a.takes):
            seed = 101 + 1000 * k + n
            out = d / "foley" / f"{cid}_{seed}.flac"
            if out.exists():
                continue
            wf = fill(client, "ltx_t2a", {
                "prompt": f"Sound effects only, close and clean, no music: {c['text']}.",
                "negative": NEGATIVE, "duration": round(max(c["seconds"], 1.0), 3), "seed": seed})
            took = run(client, wf, out)
            print(f"{cid} take {seed}: {out.name} ({took:.0f}s) {c['text']}", flush=True)


if __name__ == "__main__":
    main()
