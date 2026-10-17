"""Render runner: shots.json + direction.json -> keyframe options, picked keyframes, clips. Via the Comfy SDK.

Runs against any Comfy API v2 surface; by default the local comfy-api-proxy in front of your own ComfyUI
(COMFY_BASE_URL=http://127.0.0.1:8189, no key). Every graph comes from workflows/*.json, which
tools/build_workflows.py has already stripped of API nodes.

usage:
  python -m on_the_one.render songs/<song> keyframes s09 s12 [--options 3]
  python -m on_the_one.render songs/<song> pick s09 <seed>
  python -m on_the_one.render songs/<song> clips s09 s12

songs/<song>/direction.json:
  {"character": "character/hero_16x9.png",          # reference for every keyframe (16:9: output takes its size)
   "style": "appended to every keyframe prompt",
   "negative": "for clips",
   "shots": {"s09": {"keyframe": "...", "motion": "...", "seed": 5}}}
"""
import argparse, copy, json, os, shutil, subprocess, tempfile, time
from pathlib import Path
from comfy_sdk import Comfy

ROOT = Path(__file__).resolve().parents[1]


def template(name):
    t = json.loads((ROOT / "workflows" / f"{name}.json").read_text())
    return t["graph"], t["slots"]


def fill(client, name, values):
    graph, slots = template(name)
    wf = client.workflows.from_json(copy.deepcopy(graph))
    for slot, value in values.items():
        node, field = slots[slot]
        wf.set_input(node, field, value)
    return wf


def run(client, wf, out):
    t0 = time.time()
    job = client.run(wf)
    if job.status != "succeeded" or not job.outputs:
        raise SystemExit(f"job {job.id}: {job.status}")
    job.outputs[0].to_file(out)
    return time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir")
    ap.add_argument("stage", choices=["keyframes", "pick", "clips"])
    ap.add_argument("args", nargs="+")
    ap.add_argument("--options", type=int, default=3)
    a = ap.parse_args()
    d = Path(a.song_dir)
    shots_doc = json.loads((d / "shots.json").read_text())
    plan, fps = {s["id"]: s for s in shots_doc["shots"]}, shots_doc["fps"]
    direction = json.loads((d / "direction.json").read_text())
    os.environ.setdefault("COMFY_BASE_URL", "http://127.0.0.1:8189")

    if a.stage == "pick":
        shot, seed = a.args
        src = d / "keyframes" / "options" / f"{shot}_{seed}.png"
        shutil.copy(src, d / "keyframes" / f"{shot}.png")
        print(f"{shot}: keyframe <- {src.name}")
        return

    client = Comfy()
    if a.stage == "keyframes":
        ref = client.assets.from_file(d / direction["character"])
        (d / "keyframes" / "options").mkdir(parents=True, exist_ok=True)
        for shot in a.args:
            spec = direction["shots"][shot]
            prompt = f"{spec['keyframe']} {direction.get('style', '')}".strip()
            base = spec.get("seed", 1)
            for i in range(a.options):
                seed = base + 1000 * i
                out = d / "keyframes" / "options" / f"{shot}_{seed}.png"
                took = run(client, fill(client, "klein_edit", {"image": ref, "prompt": prompt, "seed": seed}), out)
                print(f"{shot} option seed {seed}: {out} ({took:.0f}s)")

    if a.stage == "clips":
        audio = next(d.glob("audio.*"))
        (d / "clips").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            for shot in a.args:
                s, spec = plan[shot], direction["shots"][shot]
                kf = d / "keyframes" / f"{shot}.png"
                if not kf.exists():
                    raise SystemExit(f"{shot}: no keyframe picked yet (render keyframes, then pick)")
                # the shot's exact audio slice, render-length (LTX frames are 1 + 8n, a little past the slot)
                dur = s["render_frames"] / fps
                wav = Path(tmp) / f"{shot}.wav"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(s["start"]), "-t", f"{dur:.4f}",
                                "-i", str(audio), "-ac", "2", "-ar", "48000", str(wav)], check=True)
                seed = spec.get("seed", 1)
                wf = fill(client, "ltx_a2v", {
                    "image": client.assets.from_file(kf), "audio": client.assets.from_file(wav),
                    "prompt": spec["motion"], "negative": direction.get("negative", ""),
                    "duration": round(dur, 4), "seed": seed, "seed_2": seed + 1})
                out = d / "clips" / f"{shot}.mp4"
                took = run(client, wf, out)
                print(f"{shot} clip: {out} ({took:.0f}s, {s['render_frames']} frames for a {s['duration']}s slot)")


if __name__ == "__main__":
    main()
