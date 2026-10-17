#!/bin/sh
# Ignores SIGTERM, like a minipro call stuck inside a USB transaction.  The
# record on stderr tells a test the handler is installed, so nothing races it.
# The wait is sliced into short sleeps instead of being one long sleep: each
# sleep still runs as a short child of this shell, but it is gone within a
# tenth of a second, so an interrupted run cannot leave a long child behind.
for argument in "$@"; do
    if [ "$argument" = "-k" ]; then
        echo "[No programmer found]" >&2
        exit 0
    fi
done

trap '' TERM INT
echo ready >&2
index=0
while [ "$index" -lt 300 ]; do
    sleep 0.1
    index=$((index + 1))
done
exit 0
