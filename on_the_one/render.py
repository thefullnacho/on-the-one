"""Render runner: shots.json + direction.json -> keyframe options, picked keyframes, clips. Via the Comfy SDK.

Runs against any Comfy API v2 surface; by default the local comfy-api-proxy in front of your own ComfyUI
(COMFY_BASE_URL=http://127.0.0.1:8189, no key). Every graph comes from workflows/*.json, which
tools/build_workflows.py has already stripped of API nodes.

usage:
  python -m on_the_one.render songs/<song> keyframes s09 s12 [--options 3]
  python -m on_the_one.render songs/<song> keyframes pending      # every shot still needing options
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


def refs_for(d, direction, spec):
    """Reference images for a shot, from the characters it lists (default: the lead). None = a character has no ref yet."""
    chars = spec.get("characters", ["blip"])
    paths, looks = [], []
    for c in chars:
        info = direction.get("characters", {}).get(c, {})
        ref = info.get("ref")
        if isinstance(ref, dict):              # per-world references (e.g. ink inside the phone, gouache in reality)
            ref = ref.get(spec.get("world", "real"))
        if not ref:
            return None, None
        for r in ref if isinstance(ref, list) else [ref]:
            paths.append(d / r)
            looks.append(info.get("look", c))
    for extra in spec.get("extra_refs", []):         # props/locations to hold steady (e.g. the same nightstand across a sequence)
        paths.append(d / extra["path"]); looks.append(extra["look"])
    return paths[:3], looks[:3]


def pad_16x9(src, dst):
    """The first reference sets Klein's output size, so it must be 16:9: centre it on a canvas of its own edge colour."""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    if abs(im.width / im.height - 16 / 9) < 0.02:
        return src
    h = 768; w = round(h * 16 / 9 / 8) * 8
    fit = im.copy(); fit.thumbnail((w, h))
    canvas = Image.new("RGB", (w, h), im.getpixel((2, 2)))
    canvas.paste(fit, ((w - fit.width) // 2, (h - fit.height) // 2))
    canvas.save(dst)
    return dst


def keyframe_prompt(direction, spec, looks):
    who = "; ".join(f"reference image {i + 1} shows {look}" for i, look in enumerate(looks))
    lead = f"Use the exact characters from the reference images ({who})." if looks else ""
    # a scene with no characters gets a scene-only style, or the model invents someone to fill the "character" slot
    art = direction.get("art", {}).get(spec.get("world", "real"), {})
    style = (art.get("style") or direction.get("style", "")) if looks else \
            (art.get("style_empty") or direction.get("style_empty", "Cinematic photograph, 16:9 widescreen. No people, no characters."))
    return " ".join(x for x in (lead, spec["keyframe"], style) if x)


def pending(d, direction):
    """Shots that still need keyframe options: no pick, no options, not chained or reused, every character has a ref."""
    out = []
    for sid, spec in direction["shots"].items():
        if spec.get("chain_from") or spec.get("reuse_keyframe") or (d / "keyframes" / f"{sid}.png").exists():
            continue
        if list((d / "keyframes" / "options").glob(f"{sid}_*.png")):
            continue
        if refs_for(d, direction, spec)[0] is None:
            continue
        out.append(sid)
    return out


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
        (d / "keyframes" / "options").mkdir(parents=True, exist_ok=True)
        shots = pending(d, direction) if a.args == ["pending"] else a.args
        order = [s["id"] for s in shots_doc["shots"]]
        shots.sort(key=order.index)
        print(f"{len(shots)} shots x {a.options} options: {' '.join(shots)}")
        with tempfile.TemporaryDirectory() as tmp:
            for shot in shots:
                spec = direction["shots"][shot]
                paths, looks = refs_for(d, direction, spec)
                if paths is None:
                    print(f"{shot}: skipped, a character has no reference yet"); continue
                if paths:
                    paths[0] = pad_16x9(paths[0], Path(tmp) / f"{shot}_ref0.png")
                assets = [client.assets.from_file(p) for p in paths]
                name = ["klein_t2i", "klein_edit", "klein_edit2", "klein_edit3"][len(assets)]
                values = {"prompt": keyframe_prompt(direction, spec, looks)}
                values.update({slot: a_ for slot, a_ in zip(("image", "image2", "image3"), assets)})
                base = spec.get("seed", 1)
                for i in range(a.options):
                    seed = base + 1000 * i
                    out = d / "keyframes" / "options" / f"{shot}_{seed}.png"
                    took = run(client, fill(client, name, {**values, "seed": seed}), out)
                    print(f"{shot} [{name}] option seed {seed}: {out.name} ({took:.0f}s)", flush=True)

    if a.stage == "clips":
        audio = next(d.glob("audio.*"))
        (d / "clips").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            for shot in a.args:
                s, spec = plan[shot], direction["shots"][shot]
                kf = d / "keyframes" / f"{shot}.png"
                if spec.get("chain_from"):            # continue from the previous shot's last frame
                    prev = d / "clips" / f"{spec['chain_from']}.mp4"
                    if not prev.exists():
                        raise SystemExit(f"{shot}: chained from {spec['chain_from']}, which has no clip yet")
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.1", "-i", str(prev),
                                    "-frames:v", "1", "-update", "1", str(kf)], check=True)
                elif spec.get("reuse_keyframe") and not kf.exists():
                    shutil.copy(d / "keyframes" / f"{spec['reuse_keyframe']}.png", kf)
                if not kf.exists():
                    raise SystemExit(f"{shot}: no keyframe picked yet (render keyframes, then pick)")
                # the shot's exact audio slice, render-length (LTX frames are 1 + 8n, a little past the slot)
                dur = s["render_frames"] / fps
                wav = Path(tmp) / f"{shot}.wav"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(s["start"]), "-t", f"{dur:.4f}",
                                "-i", str(audio), "-ac", "2", "-ar", "48000", str(wav)], check=True)
                seed = spec.get("seed", 1)
                picks = json.loads((d / "picks.json").read_text()) if (d / "picks.json").exists() else {}
                for v, seeds in spec.get("variant_seeds", {}).items():   # a shot with variants uses the picked variant's motion
                    if picks.get(shot) in seeds:
                        spec = {**spec, **spec["variants"][v]}
                wf = fill(client, "ltx_a2v", {
                    "image": client.assets.from_file(kf), "audio": client.assets.from_file(wav),
                    "prompt": spec["motion"], "negative": direction.get("negative", ""),
                    "duration": round(dur, 4), "seed": seed, "seed_2": seed + 1})
                out = d / "clips" / f"{shot}.mp4"
                took = run(client, wf, out)
                print(f"{shot} clip: {out} ({took:.0f}s, {s['render_frames']} frames for a {s['duration']}s slot)", flush=True)


if __name__ == "__main__":
    main()
