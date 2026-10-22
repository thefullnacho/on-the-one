"""Assemble the 2-minute competition demo: film hook, the app's steps (captioned), the live re-roll, the film, end card.

usage: python3 demo/build_demo.py      -> demo/on-the-one_demo.mp4
inputs: songs/green_flag/cut_master_1080.mp4 (the film, with foley), demo/app_walkthrough.webm + marks.json
        (demo/record_app.mjs), demo/reroll_live.mp4 (demo/reroll_cut.py)
Audio is the film's own track: the hook and payoff at full level, the song from 0:00 under the app (full while the
animatic plays it, then low), so the music never stops.
"""
import json, subprocess, tempfile
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
FILM = ROOT / "songs" / "green_flag" / "cut_master_1080.mp4"
WALK = HERE / "app_walkthrough.webm"
REROLL = HERE / "reroll_live.mp4"
FONT, LED = HERE / "fonts" / "Bricolage.ttf", HERE / "fonts" / "DotGothic16.ttf"
OUT = HERE / "on-the-one_demo.mp4"
INK, MINT, DUSK = "0xe4eaf6", "0x8ef0c0", "0x121c33"

m = {x["name"]: x["t"] for x in json.loads((HERE / "marks.json").read_text())}
# (source, start, end, caption, step label); film times are song times. Marks are taken just before each
# click, so a step starts ~0.9 s after its mark (once the screen has drawn) and ends just after the next mark.
VIDEO = [
    (FILM, 44.87, 52.87, None, None),
    (WALK, m["song"] + 0.2, m["song"] + 9.2, "Drop in your song. It becomes a beat-locked animatic.", "1  Song"),
    (WALK, m["story"] + 0.9, m["look"] - 2.4, "Story beats come from the lyrics. Rewrite any section.", "2  Story"),
    (WALK, m["look"], m["cast"] + 0.6, "Test looks in motion, then lock one.", "3  Look"),
    (WALK, m["cast"] + 0.9, m["shots"] + 0.6, "Every character gets a locked sheet.", "4  Cast"),
    (WALK, m["shots"] + 0.9, m["review"] + 0.4, "Three keyframes per shot. Pick one.", "5  Shots"),
    (REROLL, 0, 30, None, None),                       # captioned already: the live re-roll, 3:10 cut to 30 s
    (FILM, 113.97, 161.47, "The finished film: 1080p, every frame rendered locally.", None),
]
END_SECONDS = 7.5


def esc(s):
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace(",", "\\,")


def run(*cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp, parts = Path(tmp), []
        for k, (src, a, b, cap, step) in enumerate(VIDEO):
            vf = ["fps=30", "scale=1920:1080:force_original_aspect_ratio=increase", "crop=1920:1080", "setsar=1"]
            if k == 0:   # title over the hook
                vf += [f"drawtext=fontfile={FONT}:text='on-the-one':fontsize=120:fontcolor={INK}:x=110:y=h-330"
                       ":shadowcolor=black@0.6:shadowx=3:shadowy=3",
                       f"drawtext=fontfile={FONT}:text='{esc('Your song, as a music video. On your own GPU.')}':fontsize=52"
                       f":fontcolor={MINT}:x=114:y=h-190:shadowcolor=black@0.6:shadowx=2:shadowy=2"]
            if cap:
                vf += [f"drawbox=x=0:y=ih-170:w=iw:h=170:color={DUSK}@0.88:t=fill",
                       f"drawtext=fontfile={FONT}:text='{esc(cap)}':fontsize=50:fontcolor={INK}:x=70:y=h-112"]
                if src == FILM:   # the payoff caption leaves after 5 s
                    vf[-2] += ":enable='lt(t,5)'"; vf[-1] += ":enable='lt(t,5)'"
            if step:
                vf += [f"drawtext=fontfile={LED}:text='{step}':fontsize=44:fontcolor={MINT}:x=w-tw-70:y=h-108"]
            p = tmp / f"v{k:02d}.mp4"
            run("ffmpeg", "-v", "error", "-y", "-ss", a, "-t", b - a, "-i", src, "-an", "-vf", ",".join(vf),
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", p)
            parts.append(p)
        card = tmp / "end.mp4"
        lines = [("on-the-one", 110, INK, FONT, 330), ("Open weights only: LTX-2.5 and FLUX Klein, in ComfyUI", 46, INK, FONT, 530),
                 ("Driven by the Comfy SDK. No API nodes. Free re-rolls on your own GPU.", 40, MINT, FONT, 610),
                 ("github.com/thefullnacho/on-the-one", 44, MINT, LED, 760)]
        run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={DUSK}:s=1920x1080:r=30:d={END_SECONDS}", "-vf",
            ",".join(f"drawtext=fontfile={f}:text='{esc(t)}':fontsize={s}:fontcolor={c}:x=(w-tw)/2:y={y}" for t, s, c, f, y in lines)
            + ",fade=t=in:st=0:d=0.6", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", card)
        parts.append(card)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        video = tmp / "video.mp4"
        run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video)

        # audio: hook | song from 0:00 under the app and the re-roll (full for the animatic, then low) | payoff | outro tail
        hook = VIDEO[0][2] - VIDEO[0][1]
        app = sum(b - a for _, a, b, _, _ in VIDEO[1:7])
        full = VIDEO[1][2] - VIDEO[1][1]
        pay_a, pay_b = VIDEO[7][1], VIDEO[7][2]
        af = (f"[0:a]atrim={VIDEO[0][1]}:{VIDEO[0][2]},asetpts=N/SR/TB,afade=t=out:st={hook - 0.25}:d=0.25[a0];"
              f"[0:a]atrim=0:{app},asetpts=N/SR/TB,volume='if(lt(t,{full}),1,0.22)':eval=frame,"
              f"afade=t=in:d=0.25,afade=t=out:st={app - 0.6}:d=0.6[a1];"
              f"[0:a]atrim={pay_a}:{pay_b + END_SECONDS},asetpts=N/SR/TB,afade=t=in:d=0.6,"
              f"afade=t=out:st={pay_b - pay_a + END_SECONDS - 2.5}:d=2.5[a2];"
              f"[a0][a1][a2]concat=n=3:v=0:a=1[a]")
        run("ffmpeg", "-v", "error", "-y", "-i", FILM, "-i", video, "-filter_complex", af, "-map", "1:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", OUT)
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(OUT)],
                             capture_output=True, text=True).stdout)
    print(f"{OUT} {d:.1f}s")


if __name__ == "__main__":
    main()
