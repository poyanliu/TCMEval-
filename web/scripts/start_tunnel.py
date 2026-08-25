"""Start a serveo SSH tunnel and extract the URL."""
import subprocess
import time
import os
import sys

os.system("pkill -f serveo 2>/dev/null")
time.sleep(1)

cmd = [
    "ssh", "-o", "StrictHostKeyChecking=no",
    "-o", "ServerAliveInterval=60",
    "-o", "ServerAliveCountMax=3",
    "-o", "ExitOnForwardFailure=yes",
    "-N", "-R", "80:localhost:6006",
    "serveo.net"
]

proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)

time.sleep(8)

# Read whatever output is available so far
import select
ready, _, _ = select.select([proc.stdout], [], [], 2)
if ready:
    output = proc.stdout.read(4096).decode("utf-8", errors="replace")
else:
    output = "(no output yet)"

import re
url_match = re.search(r'https://[^\s]+serveo[^\s]+', output)
if url_match:
    url = url_match.group().rstrip('.')
    with open("/tmp/serveo_url.txt", "w") as f:
        f.write(url)
    print(f"TUNNEL_URL={url}")
    print("Tunnel started successfully.")
else:
    print(f"Raw output: {output[:500]}")
    print("Could not extract URL, but tunnel may still be starting...")
