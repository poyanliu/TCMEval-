#!/bin/bash
# Wrapper script to start SSH tunnel and log the URL

LOG=/tmp/tunnel_url.txt

# Keep trying to establish tunnel
while true; do
    echo "=== Tunnel started at $(date) ===" >> "$LOG"
    ssh -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=60 \
        -o ServerAliveCountMax=3 \
        -o ConnectTimeout=10 \
        -N -R 80:localhost:6006 \
        nokey@localhost.run 2>> "$LOG"
    echo "=== Tunnel disconnected at $(date), reconnecting in 5s... ===" >> "$LOG"
    sleep 5
done
