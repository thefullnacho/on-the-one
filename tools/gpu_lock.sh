#!/bin/bash
# GPU lock for a shared box: one line in /var/tmp/gpu-box.lock says who holds the GPUs, for what, and until when.
# Any session (any user, any tool) can read it; a lock past its expiry counts as free.
#
#   gpu_lock.sh status
#   gpu_lock.sh acquire <who> <what> <minutes>     # exit 0 = yours, exit 75 = busy (prints the holder)
#   gpu_lock.sh release <who>                      # only the holder's name releases it
#
# ComfyUI jobs submitted to the one shared server (port 8188) already queue in order; this is for everything else,
# and for long batches that should not interleave with someone else's work.
LOCK=/var/tmp/gpu-box.lock
cmd=$1; shift
now=$(date +%s)

holder() {   # prints the live holder line, or nothing if free/expired
    [ -s "$LOCK" ] || return 0
    IFS='|' read -r who what until < "$LOCK"
    [ "${until:-0}" -gt "$now" ] && echo "$who|$what|$until"
}

case "$cmd" in
  status)
    h=$(holder)
    if [ -n "$h" ]; then IFS='|' read -r who what until <<< "$h"; echo "BUSY: $who ($what) until $(date -d @"$until" '+%a %H:%M')"
    else echo "FREE"; fi ;;
  acquire)
    who=$1; what=$2; mins=${3:-120}
    exec 9>>"$LOCK.flock"; flock 9                           # atomic check-and-write
    h=$(holder)
    if [ -n "$h" ] && [ "${h%%|*}" != "$who" ]; then
        IFS='|' read -r hw hwhat huntil <<< "$h"
        echo "GPU busy: $hw ($hwhat) until $(date -d @"$huntil" '+%a %H:%M')" >&2; exit 75
    fi
    echo "$who|$what|$((now + mins * 60))" > "$LOCK"; chmod 666 "$LOCK" "$LOCK.flock" 2>/dev/null
    echo "GPU locked by $who: $what, $mins min" ;;
  release)
    who=$1; exec 9>>"$LOCK.flock"; flock 9
    h=$(holder)
    if [ -z "$h" ] || [ "${h%%|*}" = "$who" ]; then : > "$LOCK"; echo "GPU released by $who"
    else echo "not released: held by ${h%%|*}" >&2; exit 1; fi ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
