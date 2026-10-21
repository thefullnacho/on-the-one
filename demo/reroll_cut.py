"""Cut the live re-roll screen recording (3:10 of mostly waiting) down to the moments that change, with captions and a
real-time clock so the jumps read as compression, not a trick.

usage: python3 demo/reroll_cut.py "<recording.webm>" demo/reroll_live.mp4
"""
import subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent
FONT = HERE / "fonts" / "Bricolage.ttf"
LED = HERE / "fonts" / "DotGothic16.ttf"

# the re-roll panel in the 1911x862 recording (title, clip, button, status, takes): after the opening wide shot it
# fills the right of the frame, scaled up, with the caption large on the left
PANEL = "crop=451:640:1460:50"
# (source start, source end, caption): times read off the recording's status line and takes row
SEGMENTS = [
    (0.0, 3.0, "Pick a shot. Re-roll it."),
    (3.75, 5.75, "ComfyUI starts on your own GPU"),
    (11.0, 13.0, "Rendering take 1 of 3, locally"),
    (77.0, 80.0, "Take 1"),
    (122.25, 124.75, "Take 2"),
    (167.25, 171.0, "Take 3. Three options, no credits spent."),
    (176.0, 182.25, "Hover to watch each take"),
    (182.5, 190.0, "Use this. It's in the cut."),
]


def esc(s):
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace(",", "\\,")


def main(src, out):
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for k, (a, b, cap) in enumerate(SEGMENTS):
            clock = (f"real time %{{eif\\:trunc(({a}+t)/60)\\:d}}\\:%{{eif\\:mod(trunc({a}+t)\\,60)\\:d\\:2}}")
            if k == 0:
                frame = ["scale=1920:-2", "pad=1920:1080:0:40:color=0x121c33"]
                text = [f"drawtext=fontfile={FONT}:text='{esc(cap)}':fontsize=46:fontcolor=0xe4eaf6:x=60:y=h-120",
                        f"drawtext=fontfile={LED}:text='{clock}':fontsize=34:fontcolor=0x8ef0c0:x=w-tw-60:y=h-112"]
            else:
                frame = [PANEL, "scale=-2:900:flags=lanczos", "pad=1920:1080:1920-iw-140:(oh-ih)/2:color=0x121c33"]
                text = [f"drawtext=fontfile={FONT}:text='{esc(cap)}':fontsize=64:fontcolor=0xe4eaf6:x=140:y=430",
                        f"drawtext=fontfile={LED}:text='{clock}':fontsize=40:fontcolor=0x8ef0c0:x=140:y=530"]
            vf = ",".join(["fps=30", *frame, *text])
            p = Path(tmp) / f"p{k:02d}.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(a), "-t", str(b - a), "-i", src, "-an", "-vf", vf,
                            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", str(p)], check=True)
            parts.append(p)
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                        "-movflags", "+faststart", out], check=True)
    print(out, f"{sum(b - a for a, b, _ in SEGMENTS):.1f}s")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
