"""The on-the-one app: a local web UI over songs/<song>/. Standard library only.

usage: python -m on_the_one.app [--port 8700] [--songs songs]
then open http://127.0.0.1:8700

The coached flow (docs/APP_FLOW.md), one step per screen: Song (the live animatic: the song plays, the viewer shows
each shot as it comes up, every shot has a notes pad that saves as you type into songs/<song>/notes.json), Story,
Look, Cast, Shots (keyframe options to pick) and Review (watch the cut, re-roll one shot live on the GPU, export).
Picks made on the Story/Look/Cast screens go to songs/<song>/steps.json.
"""
import argparse, json, mimetypes, re, shutil, subprocess, sys, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote
from on_the_one import song_audio

STATIC = Path(__file__).parent / "static"
SONGS = Path("songs")


def song_dir(name):
    d = (SONGS / name).resolve()
    if SONGS.resolve() not in d.parents or not (d / "shots.json").exists():
        return None
    return d


def song_payload(d):
    shots = json.loads((d / "shots.json").read_text())
    direction = json.loads((d / "direction.json").read_text()) if (d / "direction.json").exists() else {}
    notes = json.loads((d / "notes.json").read_text()) if (d / "notes.json").exists() else {}
    audio = song_audio(d)
    return {
        "name": d.name, "audio": audio.name if audio else None,
        "bpm": shots["bpm"], "phase": shots["phase"], "bar": shots["bar"], "duration": shots["duration"],
        "shots": shots["shots"], "cues": shots.get("cues", []),
        "direction": direction.get("shots", {}), "theme": direction.get("theme", ""), "art": direction.get("art", {}),
        "notes": notes,
        "picks": json.loads((d / "picks.json").read_text()) if (d / "picks.json").exists() else {},
        "lines": [{"text": x["line"], "start": x["start"], "end": x["end"]} for x in shots.get("alignment", [])
                  if x.get("start") is not None and x.get("end") is not None],
        "words": [{"w": w["w"].strip(), "s": w["s"], "e": w["e"]}
                  for seg in (json.loads((d / "words.json").read_text()) if (d / "words.json").exists() else [])
                  for w in seg["words"]],
        "keyframes": sorted(p.stem for p in (d / "keyframes").glob("*.png")) if (d / "keyframes").exists() else [],
        "options": options_by_shot(d),
        "clips": sorted(p.stem for p in (d / "clips").glob("*.mp4")) if (d / "clips").exists() else [],
        "seeds": {k: v.get("seed") for k, v in direction.get("shots", {}).items()},
        "steps": read_json(d / "steps.json"),
        "look": look_tests(d),
        "cast": cast(d, direction),
        "cuts": sorted((p.name for p in d.glob("cut*.mp4")), key=lambda n: (d / n).stat().st_mtime),
        "takes": app_takes(d),
        "foley": foley_takes(d),
        "foley_picks": read_json(d / "foley_picks.json"),
        "foley_gain": {k: v["foley_gain_db"] for k, v in direction.get("shots", {}).items() if "foley_gain_db" in v},
    }


def foley_takes(d):
    """Overnight foley takes, songs/<song>/foley/<id>_<seed>.flac, grouped by shot or cue id."""
    out = {}
    for p in sorted((d / "foley").glob("*.flac")) if (d / "foley").exists() else []:
        cid, seed = p.stem.rsplit("_", 1)
        out.setdefault(cid, []).append(p.name)
    return out


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else ({} if default is None else default)


def write_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n")   # ascii escapes, like the files the runner writes
    tmp.replace(path)


def look_tests(d):
    """style_tests/<subject>_<style>[_v2].png|mp4 -> {style: {stills, motion}}; ref_<char>_<style>.png are the cast in that style."""
    out = {}
    for p in sorted((d / "style_tests").glob("*")) if (d / "style_tests").exists() else []:
        m = re.fullmatch(r"(ref_)?([a-z0-9-]+)_(3d|clay|gouache|ligne)(_v2)?\.(png|mp4)", p.name)
        if not m:
            continue
        st = out.setdefault(m.group(3), {"stills": [], "refs": [], "motion": []})
        st["motion" if m.group(5) == "mp4" else "refs" if m.group(1) else "stills"].append(f"style_tests/{p.name}")
    return out


def cast(d, direction):
    """Each character's locked sheet(s) plus the options it was picked from (character/art/<prefix>_*, character/<x>_options/)."""
    out, used = [], set()
    for name, info in direction.get("characters", {}).items():
        ref = info.get("ref")
        refs = sorted({r for v in ref.values() for r in v}) if isinstance(ref, dict) else (ref or [])
        if not refs:
            continue
        prefix = Path(refs[0]).stem.replace("_gouache", "")
        opts = sorted(f"character/art/{p.name}" for p in (d / "character" / "art").glob(f"{prefix}_*.png"))
        for od in sorted((d / "character").glob("*_options")):
            if prefix.startswith(od.name[:-len("_options")]) and od not in used:
                used.add(od)
                opts += sorted(f"character/{od.name}/{p.name}" for p in od.glob("*.png"))
        out.append({"name": name, "look": info.get("look", ""), "refs": refs, "options": opts, "note": info.get("note", "")})
    return out


def app_takes(d):
    """Live re-roll results: takes/app/<shot>_<seed>.mp4, plus each shot's job status."""
    out, base = {}, d / "takes" / "app"
    for p in sorted(base.glob("*.mp4")) if base.exists() else []:
        sid, seed = p.stem.rsplit("_", 1)
        if seed.isdigit():
            out.setdefault(sid, {"seeds": [], "status": None})["seeds"].append(int(seed))
    for p in sorted(base.glob("*.status.json")) if base.exists() else []:
        out.setdefault(p.name[:-len(".status.json")], {"seeds": [], "status": None})["status"] = read_json(p)
    return out


def options_by_shot(d):
    out = {}
    for p in sorted((d / "keyframes" / "options").glob("*.png")) if (d / "keyframes" / "options").exists() else []:
        sid, seed = p.stem.rsplit("_", 1)
        out.setdefault(sid, []).append(seed)
    return {k: sorted(v, key=int) for k, v in out.items()}   # numeric, so option numbers match the contact sheets


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        """Static/media file with HTTP Range, so the browser can seek audio and video."""
        if not path.is_file():
            return self.send_json({"error": "not found"}, 404)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        start, end = 0, size - 1
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1)); end = int(rng.group(2)) if rng.group(2) else size - 1
            else:
                start = size - int(rng.group(2))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")   # re-rolls reuse filenames; never show a stale image
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def do_GET(self):
        p = unquote(self.path.split("?")[0])
        if p in ("/", "/index.html"):
            return self.send_file(STATIC / "index.html")
        if p == "/api/songs":
            return self.send_json(sorted(d.name for d in SONGS.iterdir() if (d / "shots.json").exists()))
        m = re.match(r"^/api/song/([^/]+)$", p)
        if m:
            d = song_dir(m.group(1))
            return self.send_json(song_payload(d)) if d else self.send_json({"error": "no such song"}, 404)
        m = re.match(r"^/media/([^/]+)/(.+)$", p)
        if m:
            d = song_dir(m.group(1))
            f = (d / m.group(2)).resolve() if d else None
            if not f or d not in f.parents:
                return self.send_json({"error": "not found"}, 404)
            return self.send_file(f)
        self.send_json({"error": "not found"}, 404)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_POST(self):
        m = re.match(r"^/api/song/([^/]+)/(pick|reroll|clip|step|export|foley)(?:/([a-z0-9-]+))?$", unquote(self.path))
        d = song_dir(m.group(1)) if m else None
        if not d:
            return self.send_json({"error": "not found"}, 404)
        action, shot = m.group(2), m.group(3)
        if action == "pick" and shot:
            return self.pick_keyframe(d, shot)
        if action == "reroll" and shot:
            return self.reroll(d, shot)
        if action == "clip" and shot:
            return self.use_take(d, shot)
        if action == "foley" and shot:
            b = self.body()
            if "gain" in b:                            # level nudge in dB, kept in the direction (foleymix reads it)
                direction = read_json(d / "direction.json")
                if shot not in direction.get("shots", {}):
                    return self.send_json({"error": "no such shot"}, 404)
                g = max(-18.0, min(12.0, float(b["gain"])))
                direction["shots"][shot]["foley_gain_db"] = g
                write_json(d / "direction.json", direction)
                return self.send_json({"foley": shot, "gain": g})
            take = str(b.get("take", ""))   # a file name from foley/, or "" for no foley on this shot
            if take and not (re.fullmatch(rf"{re.escape(shot)}_\d+\.flac", take) and (d / "foley" / take).exists()):
                return self.send_json({"error": "no such take"}, 404)
            picks = read_json(d / "foley_picks.json")
            picks[shot] = take
            write_json(d / "foley_picks.json", picks)
            return self.send_json({"foley": shot, "take": take})
        if action == "step":
            b = self.body()
            if b.get("key") not in ("story", "look", "cast"):
                return self.send_json({"error": "unknown step"}, 400)
            steps = read_json(d / "steps.json")
            steps[b["key"]] = b.get("value")
            write_json(d / "steps.json", steps)
            return self.send_json({"saved": b["key"]})
        if action == "export":
            out = d / "cut.mp4"
            r = subprocess.run([sys.executable, "-m", "on_the_one.conform", str(d), "--out", str(out)],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
            if r.returncode != 0:
                return self.send_json({"error": (r.stderr or r.stdout).strip()[-400:]}, 500)
            return self.send_json({"cut": out.name, "log": r.stdout.strip().splitlines()[-1:]})
        self.send_json({"error": "not found"}, 404)

    def reroll(self, d, shot):
        """Start a live re-roll of one shot (3 new seeds into takes/app/); the page polls the song payload for status."""
        if shot not in read_json(d / "direction.json").get("shots", {}):
            return self.send_json({"error": "no such shot"}, 404)
        st = read_json(d / "takes" / "app" / f"{shot}.status.json")
        if st.get("state") not in (None, "done", "failed", "busy") and time.time() - st.get("t", 0) < 600:
            return self.send_json({"error": "already re-rolling"}, 409)
        (d / "takes" / "app").mkdir(parents=True, exist_ok=True)
        with open(d / "takes" / "app" / f"{shot}.log", "a") as log:
            subprocess.Popen([sys.executable, "-m", "on_the_one.reroll", str(d), shot], cwd=Path(__file__).resolve().parents[1],
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        write_json(d / "takes" / "app" / f"{shot}.status.json", {"shot": shot, "state": "queued", "t": time.time()})
        self.send_json({"rerolling": shot})

    def use_take(self, d, shot):
        """Use a re-rolled take: takes/app/<shot>_<seed>.mp4 becomes clips/<shot>.mp4 (the old clip is kept in takes/app/)."""
        seed = str(self.body().get("seed", ""))
        src = d / "takes" / "app" / f"{shot}_{seed}.mp4"
        if not re.fullmatch(r"\d+", seed) or not src.exists():
            return self.send_json({"error": "no such take"}, 404)
        direction = read_json(d / "direction.json")
        clip = d / "clips" / f"{shot}.mp4"
        old = direction["shots"][shot].get("seed")
        if clip.exists() and old is not None and not (d / "takes" / "app" / f"{shot}_{old}.mp4").exists():
            shutil.copyfile(clip, d / "takes" / "app" / f"{shot}_{old}.mp4")
        shutil.copyfile(src, clip)
        direction["shots"][shot]["seed"] = int(seed)
        write_json(d / "direction.json", direction)
        self.send_json({"clip": shot, "seed": int(seed)})

    def pick_keyframe(self, d, shot):
        """Pick a keyframe option: copies keyframes/options/<shot>_<seed>.png to keyframes/<shot>.png."""
        seed = str(self.body().get("seed", ""))
        src = d / "keyframes" / "options" / f"{shot}_{seed}.png"
        if not re.fullmatch(r"\d+", seed) or not src.exists():
            return self.send_json({"error": "no such option"}, 404)
        shutil.copyfile(src, d / "keyframes" / f"{shot}.png")
        picks = d / "picks.json"
        data = json.loads(picks.read_text()) if picks.exists() else {}
        data[shot] = int(seed)
        picks.write_text(json.dumps(data, indent=1))
        self.send_json({"picked": shot, "seed": int(seed)})

    def do_PUT(self):
        m = re.match(r"^/api/song/([^/]+)/notes/([a-z0-9-]+)$", unquote(self.path))
        d = song_dir(m.group(1)) if m else None
        if not d:
            return self.send_json({"error": "not found"}, 404)
        text = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}").get("text", "")
        path = d / "notes.json"
        notes = json.loads(path.read_text()) if path.exists() else {}
        if text.strip():
            notes[m.group(2)] = text
        else:
            notes.pop(m.group(2), None)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(notes, indent=1, ensure_ascii=False))
        tmp.replace(path)
        self.send_json({"saved": m.group(2)})


def main():
    global SONGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--songs", default="songs")
    a = ap.parse_args()
    SONGS = Path(a.songs)
    print(f"on-the-one: http://127.0.0.1:{a.port}")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
