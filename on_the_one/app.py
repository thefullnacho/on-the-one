"""The on-the-one app: a local web UI over songs/<song>/. Standard library only.

usage: python -m on_the_one.app [--port 8700] [--songs songs]
then open http://127.0.0.1:8700

Screen one is the live animatic: the song plays, the viewer shows each shot as it comes up (clip, keyframe or
placeholder card), and every shot has a notes pad that saves as you type (songs/<song>/notes.json).
"""
import argparse, json, mimetypes, re, shutil
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

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
    audio = next((p.name for p in sorted(d.glob("audio.*"))), None)
    return {
        "name": d.name, "audio": audio,
        "bpm": shots["bpm"], "phase": shots["phase"], "bar": shots["bar"], "duration": shots["duration"],
        "shots": shots["shots"], "cues": shots.get("cues", []),
        "direction": direction.get("shots", {}), "theme": direction.get("theme", ""),
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
    }


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
        self.send_header("Cache-Control", "no-cache")
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

    def do_POST(self):
        """Pick a keyframe option: copies keyframes/options/<shot>_<seed>.png to keyframes/<shot>.png."""
        m = re.match(r"^/api/song/([^/]+)/pick/([a-z0-9-]+)$", unquote(self.path))
        d = song_dir(m.group(1)) if m else None
        if not d:
            return self.send_json({"error": "not found"}, 404)
        seed = str(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}").get("seed", ""))
        src = d / "keyframes" / "options" / f"{m.group(2)}_{seed}.png"
        if not re.fullmatch(r"\d+", seed) or not src.exists():
            return self.send_json({"error": "no such option"}, 404)
        shutil.copyfile(src, d / "keyframes" / f"{m.group(2)}.png")
        picks = d / "picks.json"
        data = json.loads(picks.read_text()) if picks.exists() else {}
        data[m.group(2)] = int(seed)
        picks.write_text(json.dumps(data, indent=1))
        self.send_json({"picked": m.group(2), "seed": int(seed)})

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
