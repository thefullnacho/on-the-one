"""Live re-roll of one shot's clip, launched by the app: N new seeds into takes/app/, the cut is not touched.

usage: python -m on_the_one.reroll songs/<song> <shot> [--n 3]

Shared box: takes the GPU lock (tools/gpu_lock.sh) and keeps everything on the GPU in ON_THE_ONE_GPU (a CUDA uuid
or index, default 0). If ComfyUI is already up it renders there and leaves it running; otherwise it starts ComfyUI
and the API proxy as transient units, with Hestia's Ollama paused, and puts things back when done.
Progress goes to takes/app/<shot>.status.json, which the app polls.
"""
import argparse, json, os, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMFY = Path.home() / "comfy-spike"
LOCK = ROOT / "tools" / "gpu_lock.sh"


def up(url):
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except Exception:
        return False


def wait(url, secs=300):
    t0 = time.time()
    while not up(url):
        if time.time() - t0 > secs:
            raise SystemExit(f"timed out waiting for {url}")
        time.sleep(2)


def user_systemctl(*args):
    subprocess.run(["systemctl", "--user", *args], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir")
    ap.add_argument("shot")
    ap.add_argument("--n", type=int, default=3)
    a = ap.parse_args()
    d = Path(a.song_dir).resolve()
    out_dir = d / "takes" / "app"
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / f"{a.shot}.status.json"
    direction = json.loads((d / "direction.json").read_text())
    base = direction["shots"][a.shot].get("seed", 1) % 1000
    taken = {int(p.stem.rsplit("_", 1)[1]) for p in out_dir.glob(f"{a.shot}_*.mp4")}
    seeds, k = [], 6
    while len(seeds) < a.n:                      # fresh seeds every time: 6xxx, 7xxx, ... never one already on disk
        if k * 1000 + base not in taken:
            seeds.append(k * 1000 + base)
        k += 1

    def status(state, **kw):
        status_path.write_text(json.dumps({"shot": a.shot, "state": state, "seeds": seeds, "t": time.time(), **kw}))

    status("waiting for the GPU")
    if subprocess.run([str(LOCK), "acquire", "on-the-one", f"app-reroll-{a.shot}", "30"], cwd=ROOT).returncode != 0:
        busy = subprocess.run([str(LOCK), "status"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        status("busy", error=f"The GPU is in use: {busy}")
        sys.exit(75)
    started = False
    try:
        if not up("http://127.0.0.1:8189/api/v2/health"):
            status("starting ComfyUI")
            started = True
            user_systemctl("stop", "hestia-ollama")
            user_systemctl("reset-failed", "comfy-spike", "comfy-api-proxy")
            gpu = os.environ.get("ON_THE_ONE_GPU", "0")
            subprocess.run(["systemd-run", "--user", "--unit=comfy-spike", "--setenv=CUDA_DEVICE_ORDER=PCI_BUS_ID",
                            f"--setenv=CUDA_VISIBLE_DEVICES={gpu}",   # index = nvidia-smi's; 0 is the 5080 on this box
                            f"--working-directory={COMFY / 'ComfyUI'}", str(COMFY / "ComfyUI/venv/bin/python"), "main.py",
                            "--listen", "127.0.0.1", "--port", "8188"], check=True, stdout=subprocess.DEVNULL)
            wait("http://127.0.0.1:8188/system_stats")
            subprocess.run(["systemd-run", "--user", "--unit=comfy-api-proxy", str(COMFY / "tools/.venv/bin/comfy-api-proxy"),
                            "--comfyui", "http://127.0.0.1:8188", "--port", "8189",
                            "--comfyui-base-dir", str(COMFY / "ComfyUI")], check=True, stdout=subprocess.DEVNULL)
            wait("http://127.0.0.1:8189/api/v2/health")
        done = []
        for seed in seeds:
            status("rendering", done=done, current=seed)
            r = subprocess.run([sys.executable, "-u", "-m", "on_the_one.render", str(d), "clips", a.shot,
                                "--seeds", str(seed), "--out-dir", str(out_dir)], cwd=ROOT)
            if r.returncode == 0:
                done.append(seed)
        status("done" if done else "failed", done=done)
    except BaseException as e:
        status("failed", error=str(e) or type(e).__name__)
        raise
    finally:
        if started:
            user_systemctl("stop", "comfy-api-proxy", "comfy-spike")
            user_systemctl("start", "hestia-ollama")
        subprocess.run([str(LOCK), "release", "on-the-one"], cwd=ROOT)


if __name__ == "__main__":
    main()
