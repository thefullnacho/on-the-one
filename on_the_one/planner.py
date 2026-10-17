"""Shot planner: lyric sheet + Whisper word timings + beat grid -> a beat-locked shot list.

Deterministic. The lyric sheet is the source of truth for words and sections; Whisper only
supplies timings. Section boundaries snap to bars, shots are whole bars, and every shot gets a
render length LTX accepts (1 + 8n frames) that covers its slot.

usage: python -m on_the_one.planner songs/<song> [--max-shot 5.0] [--fps 24]
reads  songs/<song>/{lyrics.txt, words.json, grid.json, cuts.json (optional: {"Intro": [2.05, 7.07]})}
writes songs/<song>/shots.json
"""
import argparse, difflib, json, math, re
from pathlib import Path

SECTION = re.compile(r"^\[([^\]:]+)(?::\s*([^\]]*))?\]\s*$")
STAGE = re.compile(r"^\(.*\)$")          # "(Beat drops)", "(Phone lock click)": direction, not sung


def norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


def parse_sheet(text):
    """-> list of sections {name, note, lines:[{text, words:[norm]}]}"""
    sections = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = SECTION.match(line)
        if m:
            if m.group(1).strip().lower() == "end":
                break
            sections.append({"name": m.group(1).strip(), "note": (m.group(2) or "").strip(), "lines": [], "cues": []})
            continue
        if not sections:
            continue
        if STAGE.match(line):
            # stage directions are foley/transition cues; remember where in the section they sit
            sections[-1]["cues"].append({"text": line.strip("()"), "after_line": len(sections[-1]["lines"])})
            continue
        # spoken asides inside a line, e.g. "(Spoken, close-mic) Breathe out." keep the sung part
        sung = re.sub(r"\([^)]*\)", " ", line)
        words = [norm(w) for w in re.split(r"[\s—\-]+", sung) if norm(w)]
        if words:
            sections[-1]["lines"].append({"text": line, "words": words})
    return sections


def whisper_words(segments):
    out = []
    for seg in segments:
        for w in seg["words"]:
            for part in re.split(r"[\s—\-]+", w["w"]):
                if norm(part):
                    out.append({"w": norm(part), "s": w["s"], "e": w["e"]})
    return out


def align(sections, heard):
    """Give every sheet line a start/end from the Whisper words it matches (sheet wins on spelling)."""
    sheet = [(si, li, w) for si, s in enumerate(sections) for li, l in enumerate(s["lines"]) for w in l["words"]]
    sm = difflib.SequenceMatcher(a=[t[2] for t in sheet], b=[h["w"] for h in heard], autojunk=False)
    time_of = {}
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            time_of[a + k] = heard[b + k]
    idx = 0
    for si, s in enumerate(sections):
        for l in s["lines"]:
            hits = [time_of[i] for i in range(idx, idx + len(l["words"])) if i in time_of]
            l["matched"] = f"{len(hits)}/{len(l['words'])}"
            l["start"] = hits[0]["s"] if hits else None
            l["end"] = hits[-1]["e"] if hits else None
            idx += len(l["words"])
    return sections


def plan(sections, grid, duration, max_shot=5.0, fps=24, overrides=None):
    beat = 60.0 / grid["bpm"]
    bar = 4 * beat
    phase = grid["phase"]

    def snap_bar(t):
        return phase + round((t - phase) / bar) * bar

    # section starts: first timed line, snapped to the nearest bar; the first section starts at 0
    starts = []
    for i, s in enumerate(sections):
        t = next((l["start"] for l in s["lines"] if l["start"] is not None), None)
        starts.append(0.0 if i == 0 else (snap_bar(t) if t is not None else None))
    # untimed sections (no matched lyrics) inherit the previous end; keep starts monotonic
    for i in range(1, len(starts)):
        if starts[i] is None or starts[i] <= starts[i - 1]:
            starts[i] = starts[i - 1] + bar
    bounds = starts + [duration]

    bars_per_shot = max(1, int(max_shot // bar))
    shots = []
    for i, s in enumerate(sections):
        a, b = bounds[i], bounds[i + 1]
        # cut points on every bars_per_shot-th bar line inside the section
        cuts, t = [a], (snap_bar(a) if i else phase) + bars_per_shot * bar
        while t < b - bar / 2:
            cuts.append(t)
            t += bars_per_shot * bar
        cuts.append(b)
        if overrides and s["name"] in overrides:
            # director's cuts: times inside the section, snapped to the nearest beat (not just bars)
            inner = sorted(phase + round((t - phase) / beat) * beat for t in overrides[s["name"]])
            cuts = [a] + [t for t in inner if a < t < b] + [b]
        for k in range(len(cuts) - 1):
            s0, s1 = cuts[k], cuts[k + 1]
            n = math.ceil(((s1 - s0) * fps - 1) / 8)
            # a line belongs to the shot holding its midpoint: vocals often start on a pickup before the bar
            shot_lines = [l["text"] for l in s["lines"]
                          if l["start"] is not None and s0 <= (l["start"] + l["end"]) / 2 < s1]
            shots.append({
                "id": f"s{len(shots) + 1:02d}",
                "section": s["name"], "section_note": s["note"], "part": f"{k + 1}/{len(cuts) - 1}",
                "start": round(s0, 3), "end": round(s1, 3), "duration": round(s1 - s0, 3),
                "bars": round((s1 - s0) / bar, 2),
                "render_frames": 1 + 8 * n, "render_seconds": round((1 + 8 * n) / fps, 3),
                "lines": shot_lines,
            })
    return {"bpm": grid["bpm"], "phase": phase, "bar": round(bar, 4), "fps": fps,
            "duration": duration, "shots": shots}


def place_cues(sections, out):
    """Time each stage direction: in the gap between the line before it and the line after it."""
    flat = [l for s in sections for l in s["lines"]]
    starts = []                              # section start times in order, from the shot list
    for sh in out["shots"]:
        if sh["part"].startswith("1/"):
            starts.append(sh["start"])
    starts.append(out["duration"])
    bar, phase, cues, seen = out["bar"], out["phase"], [], 0
    for si, s in enumerate(sections):
        for c in s["cues"]:
            k = seen + c["after_line"]
            prev_end = next((l["end"] for l in reversed(flat[:k]) if l["end"] is not None), None)
            next_start = next((l["start"] for l in flat[k:] if l["start"] is not None), None)
            if c["after_line"] == 0:
                prev_end = starts[si]
            if c["after_line"] == len(s["lines"]):
                next_start = starts[si + 1]
            t = prev_end if next_start is None else next_start if prev_end is None else (prev_end + next_start) / 2
            nxt_bar = phase + math.ceil((t - phase) / bar) * bar
            cues.append({"id": f"c{len(cues) + 1:02d}", "text": c["text"], "section": s["name"],
                         "time": round(t, 3), "next_bar": round(nxt_bar, 3),
                         "gap": [round(prev_end, 3) if prev_end is not None else None,
                                 round(next_start, 3) if next_start is not None else None]})
        seen += len(s["lines"])
    return cues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir")
    ap.add_argument("--max-shot", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--duration", type=float, default=None, help="song length in s (default: last word + 2 s)")
    a = ap.parse_args()
    d = Path(a.song_dir)
    sections = parse_sheet((d / "lyrics.txt").read_text())
    heard = whisper_words(json.loads((d / "words.json").read_text()))
    grid = json.loads((d / "grid.json").read_text())
    align(sections, heard)
    duration = a.duration or heard[-1]["e"] + 2.0
    cuts = json.loads((d / "cuts.json").read_text()) if (d / "cuts.json").exists() else None
    out = plan(sections, grid, duration, a.max_shot, a.fps, cuts)
    out["cues"] = place_cues(sections, out)
    out["alignment"] = [{"section": s["name"], "line": l["text"], "matched": l["matched"],
                         "start": l["start"]} for s in sections for l in s["lines"]]
    (d / "shots.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    for sh in out["shots"]:
        print(f"{sh['id']} {sh['start']:7.2f}-{sh['end']:7.2f} {sh['bars']:4.1f} bars  {sh['section']:<12} {sh['part']:>4}  "
              f"{(sh['lines'][0][:48] if sh['lines'] else '')}")
    for c in out["cues"]:
        print(f"cue {c['id']} {c['time']:7.2f}s (next bar {c['next_bar']:7.2f})  {c['section']:<12} {c['text']}")
    weak = [x for x in out["alignment"] if x["start"] is None]
    print(f"{len(out['shots'])} shots; {len(weak)} unaligned lines" + (f": {[w['line'][:30] for w in weak]}" if weak else ""))


if __name__ == "__main__":
    main()
