#!/bin/bash
LOG_FILE="/dev/shm/slideshow.log"
echo "=============================================================================="
echo "🍓 Raspberry Pi Google Photos Slideshow - RAM Log Viewer (/dev/shm/slideshow.log)"
echo "=============================================================================="
PID=$(pgrep -f "python3.*main.py|python3.*slideshow.py" || true)
if [ -n "$PID" ]; then
    echo "🟢 Slideshow Process: RUNNING (PID: $PID)"
else
    echo "🔴 Slideshow Process: NOT RUNNING"
fi
echo "------------------------------------------------------------------------------"
if [ -f "$LOG_FILE" ]; then
    SIZE=$(du -h "$LOG_FILE" | cut -f1)
    echo "📄 RAM Log File: $LOG_FILE ($SIZE in tmpfs memory)"
    tail -n 35 "$LOG_FILE"
    echo "------------------------------------------------------------------------------"
    echo "👉 Stream live logs with: tail -f $LOG_FILE"
else
    echo "⚠️ RAM log file not found at $LOG_FILE"
fi
echo "=============================================================================="
