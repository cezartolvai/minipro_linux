#!/bin/sh
# Ignores SIGTERM, like a minipro call stuck inside a USB transaction.  The
# record on stderr tells a test the handler is installed, so nothing races it.
trap '' TERM
echo ready >&2
sleep 120
