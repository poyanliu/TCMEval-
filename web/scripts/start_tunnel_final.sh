#!/bin/bash
# Kill old tunnels
pkill -f "serveo.net" 2>/dev/null
sleep 1

# Start tunnel detached
(
  script -q -c "ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=60 -o ConnectTimeout=10 -N -R 80:localhost:6006 serveo.net" /tmp/serveo_url.txt 2>/dev/null
) &

# Wait for URL
for i in $(seq 1 20); do
  sleep 2
  URL=$(grep -oP 'https://[^\s]+serveo[^\s]+' /tmp/serveo_url.txt 2>/dev/null | head -1)
  if [ -n "$URL" ]; then
    echo "URL: $URL"
    echo "$URL" > /tmp/active_url.txt
    exit 0
  fi
done
echo "TIMEOUT"
exit 1
