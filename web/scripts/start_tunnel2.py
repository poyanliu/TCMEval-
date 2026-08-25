"""Start an SSH tunnel to localhost.run and print the public URL."""
import subprocess
import re
import sys
import time
import os

# Kill old tunnels
os.system("pkill -f 'nokey@localhost.run' 2>/dev/null")
os.system("screen -S tunnel -X quit 2>/dev/null")
time.sleep(1)

proc = subprocess.Popen(
    ["ssh", "-o", "StrictHostKeyChecking=no",
     "-o", "ServerAliveInterval=60",
     "-o", "ConnectTimeout=15",
     "-N", "-R", "80:localhost:6006",
     "nokey@localhost.run"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    bufsize=1,
    universal_newlines=True,
    start_new_session=True,
)

# Read stderr line by line until we get the URL
url = None
start = time.time()
for line in proc.stderr:
    line = line.rstrip()
    print(line, flush=True)
    m = re.search(r'(https?://[a-zA-Z0-9.-]+\.lhr\.life)', line)
    if m:
        url = m.group(1)
        break
    if time.time() - start > 20:
        print("Timeout waiting for URL", flush=True)
        break

if url:
    with open("/tmp/tunnel_url.txt", "w") as f:
        f.write(url + "\n")
    print(f"\nPUBLIC_URL={url}", flush=True)
else:
    print("Could not get tunnel URL", flush=True)
    proc.kill()
    sys.exit(1)
