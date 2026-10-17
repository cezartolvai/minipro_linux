#!/bin/sh
# Stand-in for minipro.  A presence check answers at once, every other call
# writes progress records to stderr the way the engine does and keeps running
# until it is terminated.
for argument in "$@"; do
    if [ "$argument" = "-k" ]; then
        echo "[No programmer found]" >&2
        exit 0
    fi
done

trap 'echo "Aborted." >&2; exit 143' TERM

index=0
while [ $index -lt 200 ]; do
    printf '\r\e[KReading Code...  %d%%\n' "$index" >&2
    index=$((index + 1))
    sleep 0.05
done

echo "Success." >&2
exit 0
