#!/bin/bash

export PYTHONUNBUFFERED=1

if [ -n "$1" ]; then
    exec $@
fi

python -u /app/app.py
