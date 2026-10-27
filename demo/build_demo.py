"""Assemble the 2-minute competition demo: film hook, the app in quick punch-ins cut on the beat, bar swipes that show
the effect, the live re-roll, the finish pass, the film, end card.

usage: python3 demo/build_demo.py      -> demo/on-the-one_demo.mp4
inputs: songs/green_flag/cut_master_1080.mp4 (the film, with foley); demo/capture/ (demo/capture.mjs: the app at 4K, so a
3x punch-in stays sharp); demo/reroll_live.mp4 (demo/reroll_cut.py)
Audio is the film's own track: hook and payoff at full level, the song from 0:00 low under the app, so the music never stops.
"""
import json, subprocess, tempfile
from pathlib import Path

HERE = Path(__file__).parent
SONG = HERE.parent / "songs" / "green_flag"
FILM = SONG / "cut_master_1080.mp4"
CAP = HERE / "capture"
REROLL = HERE / "reroll_live.mp4"
FONT, LED = HERE / "fonts" / "Bricolage.ttf", HERE / "fonts" / "DotGothic16.ttf"
OUT = HERE / "on-the-one_demo.mp4"
INK, MINT, DUSK = "0xe4eaf6", "0x8ef0c0", "0x121c33"
FPS, BEAT = 30, 60 / 107.7          # the song's tempo: cuts land on beats

M = {m["name"]: m for m in json.loads((CAP / "marks.json").read_text())}
R = lambda mark, key: M[mark]["rects"][key]


def esc(s):
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace(",", "\\,")


def run(*cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def caption(cap, step):
    out = []
    if cap:
        out += [f"drawbox=x=0:y=ih-150:w=iw:h=150:color={DUSK}@0.9:t=fill",
                f"drawtext=fontfile={FONT}:text='{esc(cap)}':fontsize=50:fontcolor={INK}:x=70:y=h-102"]
    if step:
        out += [f"drawtext=fontfile={LED}:text='{step}':fontsize=44:fontcolor={MINT}:x=w-tw-70:y=h-98"]
    return out


def moment(dst, a, beats, rect=None, push=0.08, cap=None, step=None, pad=1.25):
    """A stretch of the 4K capture, punched in on `rect` (4K px; None = the whole screen), pushing in slowly."""
    dur = beats * BEAT
    if rect:
        x, y, w, h = rect
        w2 = max(w * pad, h * pad * 16 / 9, 640)          # the rect plus margin, as a 16:9 window
        cx, cy = x + w / 2, y + h / 2
    else:
        w2, cx, cy = 3840, 1920, 1080
    z0 = 3840 / w2
    n = round(dur * FPS)
    zp = (f"zoompan=z='{z0}*(1+{push}*on/{n})':d=1:s=1920x1080:fps={FPS}"
          f":x='max(0,min(iw-iw/zoom,{cx}-iw/zoom/2))':y='max(0,min(ih-ih/zoom,{cy}-ih/zoom/2))'")
    run("ffmpeg", "-v", "error", "-y", "-ss", a, "-t", dur, "-i", CAP / "app4k.mp4", "-an",
        "-vf", ",".join([f"fps={FPS}", zp, *caption(cap, step)]), "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", dst)
    return dur


def swipe(dst, before, after, dur, start, travel, crop=None, labels=None, cap=None, step=None, before_still=False):
    """Bar swipe: a bright bar crosses the frame; behind it `after` replaces `before`."""
    src = ["-loop", "1", "-t", dur, "-i", before] if before_still else ["-t", dur, "-i", before]
    fit = f"{crop + ',' if crop else ''}scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos,crop=1920:1080,fps={FPS},setsar=1,format=yuv420p"
    bar = f"clip(1920*(t-{start})/{travel},0,1920)"
    vf = [f"[0:v]{fit}[a]", f"[1:v]{fit}[b]",
          f"[a][b]blend=all_expr='if(lte(X,1920*(T-{start})/{travel}),B,A)'[s]",
          f"[s]drawbox=x='{bar}-4':y=0:w=8:h=ih:color={MINT}:t=fill:enable='between(t,{start},{start + travel})'"]
    if labels:
        vf[-1] += (f",drawtext=fontfile={LED}:text='{esc(labels[0])}':fontsize=40:fontcolor={MINT}:x=70:y=60:enable='gte(t,{start + 0.2})'"
                   f",drawtext=fontfile={LED}:text='{esc(labels[1])}':fontsize=40:fontcolor={INK}:x=w-tw-70:y=60:enable='lt(t,{start + travel - 0.2})'")
    vf[-1] += "".join("," + c for c in caption(cap, step)) + "[v]"
    run("ffmpeg", "-v", "error", "-y", *src, "-t", dur, "-i", after, "-filter_complex", ";".join(vf), "-map", "[v]",
        "-t", dur, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-r", FPS, dst)
    return dur


def clip(dst, src, a, dur, vf_extra=()):
    vf = [f"fps={FPS}", "scale=1920:1080:force_original_aspect_ratio=increase", "crop=1920:1080", "setsar=1", *vf_extra]
    run("ffmpeg", "-v", "error", "-y", "-ss", a, "-t", dur, "-i", src, "-an", "-vf", ",".join(vf),
        "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-r", FPS, dst)
    return dur


def build_capture_video():
    out = CAP / "app4k.mp4"
    if out.exists():
        return
    f = json.loads((CAP / "frames.json").read_text())
    lst = CAP / "list.txt"
    lst.write_text("".join(f"file 'frames/{a['file']}'\nduration {b['t'] - a['t']:.4f}\n" for a, b in zip(f, f[1:]))
                   + f"file 'frames/{f[-1]['file']}'\nduration 0.5\nfile 'frames/{f[-1]['file']}'\n")
    run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-vf", "fps=30,format=yuv420p",
        "-c:v", "libx264", "-crf", "12", "-preset", "fast", out)


def main():
    build_capture_video()
    song_t, story_t, typed_t = M["song"]["t"], M["story"]["t"], M["story_typed"]["t"]
    look_t, cast_t, shots_t = M["look"]["t"], M["cast"]["t"], M["shots"]["t"]
    words = R("song", "transport")[:2] + [R("song", "words")[2], R("song", "words")[1] + R("song", "words")[3] - R("song", "transport")[1]]
    typing = [R("story", "pad")[0], R("story", "pad")[1] - 60, 1500, R("story", "pad")[3] + 120]   # the start of the rewrite pad
    shot = M["shot_id"]["id"]                                                                       # the shot on the Shots screen
    with tempfile.TemporaryDirectory() as tmp:
        tmp, parts, t = Path(tmp), [], {}
        P = lambda: tmp / f"p{len(parts):02d}.mp4"
        # hook
        p = P(); clip(p, FILM, 44.87, 8.0, [
            f"drawtext=fontfile={FONT}:text='on-the-one':fontsize=120:fontcolor={INK}:x=110:y=h-330:shadowcolor=black@0.6:shadowx=3:shadowy=3",
            f"drawtext=fontfile={FONT}:text='{esc('Your song, as a music video. On your own GPU.')}':fontsize=52:fontcolor={MINT}"
            ":x=114:y=h-190:shadowcolor=black@0.6:shadowx=2:shadowy=2"]); parts.append(p)
        t["app"] = 8.0
        # 1 Song: the whole app, then the words lighting up on the beat, then the shot itself
        app = 0
        cap1 = "Drop in your song. It becomes a beat-locked animatic."
        p = P(); app += moment(p, song_t + 0.6, 2, None, 0.05, cap1, "1  Song"); parts.append(p)
        p = P(); app += moment(p, song_t + 2.4, 4, words, 0.10, cap1, "1  Song", pad=1.1); parts.append(p)
        p = P(); app += moment(p, song_t + 4.6, 2, R("song", "viewer"), 0.06, cap1, "1  Song", pad=1.0); parts.append(p)
        # 2 Story: a section's lyrics and beats, then typing a rewrite
        cap2 = "Story beats come from the lyrics. Rewrite any section."
        p = P(); app += moment(p, story_t + 0.1, 2, R("story", "sec"), 0.05, cap2, "2  Story", pad=1.02); parts.append(p)
        beats = round((typed_t + 0.2 - story_t - 1.3) / BEAT)          # end on the finished line, before it is cleared
        p = P(); app += moment(p, typed_t + 0.2 - beats * BEAT, beats, typing, 0.08, cap2, "2  Story", pad=1.05); parts.append(p)
        # 3 Look: the locked look's motion test
        p = P(); app += moment(p, look_t + 0.2, 4, R("look", "locked"), 0.10, "Test looks in motion, then lock one.", "3  Look", pad=1.08); parts.append(p)
        # 4 Cast: Blip's locked sheet, then the whole cast
        cap4 = "Every character gets a locked sheet."
        # the Cast screen is up from ~2 s before its mark (images are warmed) until ~1.5 s after it
        p = P(); app += moment(p, cast_t - 1.7, 3, R("cast", "blip"), 0.10, cap4, "4  Cast", pad=1.1); parts.append(p)
        p = P(); app += moment(p, cast_t + 0.05, 2, R("cast", "grid"), 0.04, cap4, "4  Cast", pad=1.02); parts.append(p)
        # 5 Shots: three keyframe options, then the pick comes alive (bar swipe keyframe -> clip)
        cap5 = "Three keyframes per shot. Pick one."
        p = P(); app += moment(p, shots_t + 0.05, 4, R("shots", "opts"), 0.10, cap5, "5  Shots", pad=1.25); parts.append(p)
        p = P(); app += swipe(p, SONG / "keyframes" / f"{shot}.png", SONG / "clips_1080" / f"{shot}.mp4", 5 * BEAT, 0.5, 1.2,
                              labels=("clip", "keyframe"), cap="The pick comes alive on its slice of the song.", step="5  Shots",
                              before_still=True); parts.append(p)
        # 6 Review: the live re-roll (captioned already; 3:10 cut to 30 s)
        parts.append(REROLL); app += 30.0
        # finish pass: original render -> 1080p refine, punched in on Blip
        p = P(); app += swipe(p, SONG / "clips" / "prechorus1-2.mp4", SONG / "clips_1080" / "prechorus1-2.mp4", 6 * BEAT, 0.7, 1.6,
                              crop="scale=1920:1056,crop=1056:594:432:203", labels=("1080p refine", "original render"),
                              cap="Finish: every shot refined to 1080p with LTX-2.5.", step="6  Finish"); parts.append(p)
        t["film"] = t["app"] + app
        # the film
        pay_a, pay_b = 113.97, 138.3   # ends on the chase into the plaid (chorus3-4), straight to the end card (Alex)
        p = P(); clip(p, FILM, pay_a, pay_b - pay_a, [f"drawbox=x=0:y=ih-150:w=iw:h=150:color={DUSK}@0.9:t=fill:enable='lt(t,4)'",
            f"drawtext=fontfile={FONT}:text='{esc('The finished film: every frame rendered locally.')}':fontsize=50:fontcolor={INK}:x=70:y=h-102:enable='lt(t,4)'",
            f"fade=t=out:st={pay_b - pay_a - 0.5}:d=0.5:color={DUSK}"])
        parts.append(p)
        end_s = 7.0
        lines = [("on-the-one", 110, INK, FONT, 330), ("Open weights only: LTX-2.5 and FLUX Klein, in ComfyUI", 46, INK, FONT, 530),
                 ("Driven by the Comfy SDK. No API nodes. Free re-rolls on your own GPU.", 40, MINT, FONT, 610),
                 ("github.com/thefullnacho/on-the-one", 44, MINT, LED, 760)]
        p = P(); run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={DUSK}:s=1920x1080:r={FPS}:d={end_s}", "-vf",
                     ",".join(f"drawtext=fontfile={f}:text='{esc(x)}':fontsize={s}:fontcolor={c}:x=(w-tw)/2:y={y}" for x, s, c, f, y in lines)
                     + ",fade=t=in:st=0:d=0.6", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", p); parts.append(p)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{q}'\n" for q in parts))
        video = tmp / "video.mp4"
        run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video)
        song_part = 3 * 2 * BEAT + 0.0   # the Song step: song at full level, then low under the rest of the app
        song_full = 8 * BEAT
        af = (f"[0:a]atrim=44.87:52.87,asetpts=N/SR/TB,afade=t=out:st=7.75:d=0.25[a0];"
              f"[0:a]atrim=0:{app},asetpts=N/SR/TB,volume='if(lt(t,{song_full}),1,0.22)':eval=frame,"
              f"afade=t=in:d=0.25,afade=t=out:st={app - 0.6}:d=0.6[a1];"
              f"[0:a]atrim={pay_a}:{pay_b + end_s},asetpts=N/SR/TB,afade=t=in:d=0.6,afade=t=out:st={pay_b - pay_a + end_s - 2.5}:d=2.5[a2];"
              f"[a0][a1][a2]concat=n=3:v=0:a=1[a]")
        run("ffmpeg", "-v", "error", "-y", "-i", FILM, "-i", video, "-filter_complex", af, "-map", "1:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", OUT)
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(OUT)],
                             capture_output=True, text=True).stdout)
    print(f"{OUT} {d:.1f}s (app section {app:.1f}s, film from {t['film']:.1f}s)")


if __name__ == "__main__":
    main()
