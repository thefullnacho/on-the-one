#!/bin/bash
# on-the-one finish pass (overnight, unattended): foley takes, then every clip to 1080p, then the 1080p master cut.
# usage: tools/finish.sh [song]        (default green_flag)    log: logs/finish_<date>.log
# STOP_AT (default 08:00): no new upscale starts after it, so the box is free in the morning; the next run picks up
# where this one stopped (finished shots are skipped). A 113-frame clip takes ~18 min on the 5080.
# Shared box: takes the GPU lock, renders on the 5080 only (CUDA index 0, PCI order), pauses Hestia's Ollama and
# gives it back at the end, even if a step fails. The 4060 Ti and Hestia's voice units are never touched.
SONG=${1:-green_flag}
STOP_AT=${STOP_AT:-08:00}
stop_epoch=$(date -d "$STOP_AT" +%s); [ "$stop_epoch" -le "$(date +%s)" ] && stop_epoch=$(date -d "tomorrow $STOP_AT" +%s)
cd /home/alex/on-the-one
mkdir -p logs
exec >> "logs/finish_$(date +%F).log" 2>&1
echo "== finish $SONG start $(date)"
tools/gpu_lock.sh acquire on-the-one "finish-$SONG" 600 || { echo "not started: $(tools/gpu_lock.sh status)"; exit 75; }
cleanup() {
  systemctl --user stop comfy-api-proxy comfy-spike
  systemctl --user start hestia-ollama
  tools/gpu_lock.sh release on-the-one
  echo "== finish end $(date); hestia-ollama $(systemctl --user is-active hestia-ollama)"
}
trap cleanup EXIT
systemctl --user stop hestia-ollama
systemctl --user reset-failed comfy-spike comfy-api-proxy 2>/dev/null
if ! curl -sf http://127.0.0.1:8188/system_stats >/dev/null; then
  systemd-run --user --unit=comfy-spike --setenv=CUDA_DEVICE_ORDER=PCI_BUS_ID --setenv=CUDA_VISIBLE_DEVICES=0 \
    --working-directory=/home/alex/comfy-spike/ComfyUI /home/alex/comfy-spike/ComfyUI/venv/bin/python main.py --listen 127.0.0.1 --port 8188
  until curl -sf http://127.0.0.1:8188/system_stats >/dev/null; do sleep 2; done
fi
if ! curl -sf http://127.0.0.1:8189/api/v2/health >/dev/null; then
  systemd-run --user --unit=comfy-api-proxy /home/alex/comfy-spike/tools/.venv/bin/comfy-api-proxy --comfyui http://127.0.0.1:8188 \
    --port 8189 --comfyui-base-dir /home/alex/comfy-spike/ComfyUI
  until curl -sf http://127.0.0.1:8189/api/v2/health >/dev/null; do sleep 2; done
fi
PY=.venv/bin/python
echo "-- foley $(date)";  $PY -u -m on_the_one.foley "songs/$SONG" --takes 2;  echo "foley exit $?"
# one shot at a time, so a failure (an OOM) skips that shot instead of ending the night; done shots are skipped on re-run
echo "-- upscale $(date)"
for shot in $($PY -c "import json; print(' '.join(s['id'] for s in json.load(open('songs/$SONG/shots.json'))['shots']))"); do
  if [ "$(date +%s)" -ge "$stop_epoch" ]; then echo "cutoff $STOP_AT reached, stopping before $shot"; break; fi
  $PY -u -m on_the_one.upscale "songs/$SONG" "$shot" || echo "upscale $shot FAILED"
done
echo "-- master $(date)"
$PY -m on_the_one.conform "songs/$SONG" --clips clips_1080 --size 1920x1080 --out "songs/$SONG/cut_master_1080.mp4"
echo "master exit $?; 1080p clips: $(ls songs/$SONG/clips_1080/*.mp4 2>/dev/null | wc -l)"
