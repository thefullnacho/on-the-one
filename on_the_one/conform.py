"""Conform: shots.json + whatever clips exist -> one beat-locked cut over the original song.

Deterministic. Each shot occupies exactly round(end*fps) - round(start*fps) frames, so cuts can
never drift off the bar lines. A shot with a rendered clip uses the clip's first frames (LTX renders a
little long); a shot without one shows a placeholder card (its keyframe if there is one, else the shot
id and lyric), so the whole song can be watched as an animatic before anything is rendered.
The audio is the original song, laid once under the whole cut: no seams at the cuts.

usage: python -m on_the_one.conform songs/<song> [--out cut.mp4] [--size 1408x768] [--clips clips]
  master: --clips clips_1080 --size 1920x1080 (a shot without a 1080p clip falls back to clips/)
reads  songs/<song>/{shots.json, audio.*, clips/<shot_id>.mp4, keyframes/<shot_id>.png}
"""
import argparse, json, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from on_the_one import song_audio

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def clip_path(d, folder, sid):
    p = d / folder / f"{sid}.mp4"
    return p if p.exists() else d / "clips" / f"{sid}.mp4"


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
    ap.add_argument("--clips", default="clips", help="folder of per-shot clips (clips_1080 for the master)")
    ap.add_argument("--audio", default=None, help="audio file in the song folder (mix_foley.wav for the song + foley)")
    a = ap.parse_args()
    d = Path(a.song_dir)
    plan = json.loads((d / "shots.json").read_text())
    notes = json.loads((d / "notes.json").read_text()) if (d / "notes.json").exists() else {}
    fps, size = plan["fps"], tuple(int(x) for x in a.size.split("x"))
    audio = d / a.audio if a.audio else song_audio(d)
    out = Path(a.out or d / "cut.mp4")
    vf = f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=increase,crop={size[0]}:{size[1]},fps={fps},format=yuv420p"
    used = {"clip": 0, "keyframe": 0, "card": 0}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts = []
        for sh in plan["shots"]:
            frames = round(sh["end"] * fps) - round(sh["start"] * fps)
            seg = tmp / f"{sh['id']}.mp4"
            clip, kf = clip_path(d, a.clips, sh["id"]), d / "keyframes" / f"{sh['id']}.png"
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
        # transitions: the outgoing clip lends its spare frames (LTX renders past the slot), so the overlap never moves a cut
        direction = json.loads((d / "direction.json").read_text()) if (d / "direction.json").exists() else {"shots": {}}
        XF = {"whip": "smoothleft", "flash": "fadewhite", "blend": "fade"}
        i = 0
        while i < len(plan["shots"]) - 1:
            sh, nxt = plan["shots"][i], plan["shots"][i + 1]
            tr = direction["shots"].get(sh["id"], {}).get("transition")
            clip = clip_path(d, a.clips, sh["id"])
            if not tr or not clip.exists():
                i += 1; continue
            n = int(tr.get("frames", 6)); frames = round(sh["end"] * fps) - round(sh["start"] * fps)
            ext = tmp / f"{sh['id']}_ext.mp4"
            run(["ffmpeg", "-y", "-i", str(clip), "-vf", vf + f",tpad=stop_mode=clone:stop={frames + n}", "-frames:v", str(frames + n),
                 "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(ext)])
            xout = tmp / f"{sh['id']}_x_{nxt['id']}.mp4"
            run(["ffmpeg", "-y", "-i", str(ext), "-i", str(parts[i + 1]), "-filter_complex",
                 f"[0][1]xfade=transition={XF.get(tr['type'], 'fade')}:duration={n / fps:.4f}:offset={frames / fps:.4f},format=yuv420p",
                 "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(xout)])
            parts[i] = xout; parts.pop(i + 1); plan["shots"].pop(i + 1)     # the pair is now one segment
            used.setdefault("transitions", []).append(f"{sh['id']}>{nxt['id']}:{tr['type']}")
            i += 1
        lst = tmp / "parts.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", str(out)])
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                            "stream=nb_read_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    expect = round(plan["shots"][-1]["end"] * fps)   # (the last shot's end is unchanged by merged transition pairs)
    print(f"{out}  frames {probe} (expected {expect})  shots: {used}")


if __name__ == "__main__":
    main()
