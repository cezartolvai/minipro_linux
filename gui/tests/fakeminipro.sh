#!/bin/sh
# This file is a part of the minipro project
# https://gitlab.com/DavidGriffith/minipro/
#
# Minipro is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# Deterministic stand-in for minipro, used by the GUI regression suite.
#
# It answers the queries the front-end asks with fixed text and writes progress
# the way the engine does, so a test can exercise the front-end without a
# programmer attached: nothing here opens a device, reads a chip or writes a
# file.  Every answer depends only on the arguments and on the switches below.
#
#   FAKE_MINIPRO_EXIT   exit code reported by every call (default 0)
#   FAKE_MINIPRO_EMPTY  listing and search calls report no chip at all
#   FAKE_MINIPRO_HANG   a programming call keeps running until it is stopped

action=""
term=""
chip=""
pending=""

while [ $# -gt 0 ]; do
    argument="$1"
    shift
    if [ -n "$pending" ]; then
        if [ "$pending" = L ]; then term="$argument"; else device="$argument"; fi
        pending=""
        continue
    fi
    case "$argument" in
        -k) action="presence" ;;
        -V) action="version" ;;
        -Q) action="supported" ;;
        -l) action="list" ;;
        -L) action="search"; pending="L" ;;
        -d) action="info"; pending="d" ;;
        -a) action="autodetect" ;;
        -r) action="read" ;;
        -w) action="write" ;;
        -m) action="verify" ;;
        -E) action="erase" ;;
        -b) action="blank" ;;
        -D) action="id" ;;
        -z) action="pin" ;;
        -t) action="hardware" ;;
    esac
done

if [ "$action" = "" ]; then
    echo "stub: no action in $*" >&2
    exit 2
fi

status="${FAKE_MINIPRO_EXIT:-0}"

case "$action" in
    presence)
        echo "[No programmer found]" >&2
        exit "${FAKE_MINIPRO_EXIT:-0}"
        ;;
    version)
        printf 'minipro version 0.7.4-stub\n' >&2
        printf 'Commit date:\t2026-01-01 00:00:00 +0000\n' >&2
        printf 'Git commit:\t0123456789abcdef0123456789abcdef0123456789\n' >&2
        printf 'Git branch:\tstub\n' >&2
        printf 'Share dir:\t/tmp/stub\n' >&2
        printf 'TL866A/CS: 1234 devices\n' >&2
        printf 'TL866II+: 234 devices\n' >&2
        printf 'T48: 34 devices\n' >&2
        printf 'T56: 4 devices\n' >&2
        printf 'T76: 5 devices\n' >&2
        exit "${FAKE_MINIPRO_EXIT:-0}"
        ;;
    supported)
        printf 'tl866a:  TL866CS/A\n'
        printf 'tl866ii: TL866II+\n' >&2
        exit "${FAKE_MINIPRO_EXIT:-0}"
        ;;
    list)
        if [ "${FAKE_MINIPRO_EMPTY}" = "1" ]; then
            exit "${FAKE_MINIPRO_EXIT:-0}"
        fi
        printf 'AT27C512R@DIP28\nW25Q16@SOIC8\neeprom28c256@DIP28\n'
        exit "${FAKE_MINIPRO_EXIT:-0}"
        ;;
    search)
        if [ "${FAKE_MINIPRO_EMPTY}" = "1" ] || \
                ! printf '%s\n' "$term" | grep -qi 'w25\|27c512'; then
            printf '0 device(s) found.\n' >&2
            exit "${FAKE_MINIPRO_EXIT:-0}"
        fi
        case "$term" in
            *27c512*) printf 'AT27C512R@DIP28\n' ;;
            *) printf 'W25Q16@SOIC8\n' ;;
        esac
        exit "${FAKE_MINIPRO_EXIT:-0}"
        ;;
    info)
        printf 'Device Name:\t%s\n' "$device" >&2
        printf 'Name:\t%s\n' "$device" >&2
        printf 'Device Type:\tEEPROM\n' >&2
        printf 'Available VPP voltages [V]:\n' >&2
        printf '12.5, 5.0\n' >&2
        printf 'Available VDD write voltages [V]:\n' >&2
        printf '12.5\n' >&2
        printf 'Available VCC verify voltages [V]:\n' >&2
        printf '5.0\n' >&2
        printf 'Available SPI clock frequencies [MHz]:\n' >&2
        printf '16, 8, 4\n' >&2
        exit "${FAKE_MINIPRO_EXIT:-0}"
        ;;
    autodetect)
        printf 'W25Q16@SOIC8\n'
        printf 'Found W25Q16@SOIC8\n' >&2
        exit "${FAKE_MINIPRO_EXIT:-0}"
        ;;
esac

# A programming action reports progress first, exactly as the engine does on
# stderr, then either finishes or stays running until it is stopped.
printf '\r\e[KReading Code...  10%%\n' >&2
printf '\r\e[KReading Code...  40%%\n' >&2

if [ "${FAKE_MINIPRO_HANG}" = "1" ]; then
    index=0
    while [ "$index" -lt 600 ]; do
        sleep 0.1
        index=$((index + 1))
    done
    exit 0
fi

printf 'Success.\n' >&2
exit "${FAKE_MINIPRO_EXIT:-0}"
