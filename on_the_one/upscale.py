"""Finish pass: every clip -> 1080p with LTX-2.5 TiledFusion and the refine-details IC-LoRA (the clip itself is the guide,
so motion and composition stay; the pass adds detail at 1920x1080). Writes clips_1080/<shot>.mp4; clips/ is untouched.

usage: python -m on_the_one.upscale songs/<song> [shots...]      # no shots: every clip without a 1080p version yet
The prompt is the shot's world style (direction art), so the refine keeps the painted look instead of sharpening it into a photo.
"""
import argparse, json, os
from pathlib import Path
from comfy_sdk import Comfy
from on_the_one.render import fill, run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir")
    ap.add_argument("shots", nargs="*")
    ap.add_argument("--force", action="store_true", help="redo shots that already have a 1080p clip")
    a = ap.parse_args()
    d = Path(a.song_dir)
    direction = json.loads((d / "direction.json").read_text())
    order = [s["id"] for s in json.loads((d / "shots.json").read_text())["shots"]]
    out_dir = d / "clips_1080"
    out_dir.mkdir(exist_ok=True)
    shots = a.shots or [s for s in order if (d / "clips" / f"{s}.mp4").exists()]
    os.environ.setdefault("COMFY_BASE_URL", "http://127.0.0.1:8189")
    client = Comfy()
    for shot in shots:
        out = out_dir / f"{shot}.mp4"
        if out.exists() and not a.force:
            continue
        spec = direction["shots"].get(shot, {})
        art = direction.get("art", {}).get(spec.get("world", "real"), {})
        wf = fill(client, "ltx_upscale", {
            "video": client.assets.from_file(d / "clips" / f"{shot}.mp4"),
            "prompt": " ".join(x for x in (spec.get("motion", ""), art.get("style", "")) if x),
            "negative": ", ".join(x for x in (direction.get("negative", ""), spec.get("negative_extra", "")) if x),
            "seed": spec.get("seed", 1)})
        took = run(client, wf, out)
        print(f"{shot} 1080p: {out} ({took:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
