# Sharing the GPU box

Two people's sessions render on this machine. Two rules keep them from colliding:

1. **ComfyUI work goes to the one shared server** (port 8188 on this box, reachable over the tailnet). ComfyUI queues every
   job in order, so renders from different people line up instead of fighting over VRAM. Don't start a second ComfyUI.
2. **Anything long or non-Comfy takes the lock first:** `tools/gpu_lock.sh acquire <who> <what> <minutes>`, release with
   `tools/gpu_lock.sh release <who>`, check with `tools/gpu_lock.sh status`. The lock is one line in
   `/var/tmp/gpu-box.lock` (any user can read it); an expired lock counts as free. Exit code 75 means busy.

on-the-one's render scripts take the lock as `on-the-one` and release it on exit, even if they crash.
