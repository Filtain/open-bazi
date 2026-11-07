#!/bin/sh
cd "$(dirname "$0")" || exit 1
if command -v python3.12 >/dev/null 2>&1; then
    python3.12 start.py
else
    python3 start.py
fi
