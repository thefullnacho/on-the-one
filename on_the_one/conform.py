"""Conform: shots.json + whatever clips exist -> one beat-locked cut over the original song.

Deterministic. Each shot occupies exactly round(end*fps) - round(start*fps) frames, so cuts can
never drift off the bar lines. A shot with a rendered clip uses the clip's first frames (LTX renders a
little long); a shot without one shows a placeholder card (its keyframe if there is one, else the shot
id and lyric), so the whole song can be watched as an animatic before anything is rendered.
The audio is the original song, laid once under the whole cut: no seams at the cuts.

usage: python -m on_the_one.conform songs/<song> [--out cut.mp4] [--size 1408x768]
reads  songs/<song>/{shots.json, audio.*, clips/<shot_id>.mp4, keyframes/<shot_id>.png}
"""
import argparse, json, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def card(path, size, shot, note=""):
    w, h = size
    im = Image.new("RGB", size, (24, 26, 30))
    d = ImageDraw.Draw(im)
    big, small = ImageFont.truetype(FONT, h // 10), ImageFont.truetype(FONT, h // 26)
    d.text((w * 0.06, h * 0.12), shot["id"], font=big, fill=(120, 220, 150))
    d.text((w * 0.06, h * 0.30), f"{shot['section']} {shot['part']}   {shot['start']:.2f}-{shot['end']:.2f}s   "
           f"{shot['bars']} bars", font=small, fill=(170, 170, 170))
    y = h * 0.42
    for line in shot["lines"][:4]:
        d.text((w * 0.06, y), line, font=small, fill=(235, 235, 235)); y += h * 0.07
    if note:                                   # the director's note, in porch amber, at the bottom of the card
        d.text((w * 0.06, h * 0.80), "note: " + note.replace("\n", " ")[:110], font=small, fill=(242, 182, 91))
    im.save(path)


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--size", default="1408x768")
    a = ap.parse_args()
    d = Path(a.song_dir)
    plan = json.loads((d / "shots.json").read_text())
    notes = json.loads((d / "notes.json").read_text()) if (d / "notes.json").exists() else {}
    fps, size = plan["fps"], tuple(int(x) for x in a.size.split("x"))
    audio = next(d.glob("audio.*"))
    out = Path(a.out or d / "cut.mp4")
    vf = f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=increase,crop={size[0]}:{size[1]},fps={fps},format=yuv420p"
    used = {"clip": 0, "keyframe": 0, "card": 0}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts = []
        for sh in plan["shots"]:
            frames = round(sh["end"] * fps) - round(sh["start"] * fps)
            seg = tmp / f"{sh['id']}.mp4"
            clip, kf = d / "clips" / f"{sh['id']}.mp4", d / "keyframes" / f"{sh['id']}.png"
            if clip.exists():
                src = ["-i", str(clip)]; used["clip"] += 1
                # a clip shorter than its slot holds its last frame
                pad = f",tpad=stop_mode=clone:stop={frames}"
            else:
                img = kf if kf.exists() else tmp / f"{sh['id']}.png"
                if not kf.exists():
                    card(img, size, sh, notes.get(sh["id"], "")); used["card"] += 1
                else:
                    used["keyframe"] += 1
                src = ["-loop", "1", "-i", str(img)]; pad = ""
            run(["ffmpeg", "-y", *src, "-vf", vf + pad, "-frames:v", str(frames), "-an",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(seg)])
            parts.append(seg)
        lst = tmp / "parts.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", str(out)])
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                            "stream=nb_read_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    expect = round(plan["shots"][-1]["end"] * fps)
    print(f"{out}  frames {probe} (expected {expect})  shots: {used}")


if __name__ == "__main__":
    main()
