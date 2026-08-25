"""Start an SSH tunnel to localhost.run, capture URL, and daemonize."""
import subprocess
import re
import sys
import time
import os
import signal

# Kill old tunnels
os.system("pkill -f 'nokey@localhost.run' 2>/dev/null")
time.sleep(1)

proc = subprocess.Popen(
    ["ssh", "-o", "StrictHostKeyChecking=no",
     "-o", "ServerAliveInterval=60",
     "-o", "ConnectTimeout=15",
     "-N", "-R", "80:localhost:6006",
     "nokey@localhost.run"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    start_new_session=True,
)

url = None
start = time.time()
buffer = ""

# Read character by character to avoid line buffering issues
import select
import os as _os
fd = proc.stderr.fileno()
import fcntl
fl = fcntl.fcntl(fd, fcntl.F_GETFL)
fcntl.fcntl(fd, fcntl.F_SETFL, fl | _os.O_NONBLOCK)

while time.time() - start < 30:
    try:
        r, _, _ = select.select([proc.stderr], [], [], 0.5)
        if r:
            chunk = proc.stderr.read(4096)
            if chunk:
                buffer += chunk
                # Check for URL
                m = re.search(r'(https?://[a-zA-Z0-9.-]+\.lhr\.life)', buffer)
                if m:
                    url = m.group(1)
                    break
        if proc.poll() is not None:
            break
    except Exception:
        time.sleep(0.5)

if url:
    with open("/tmp/tunnel_url.txt", "w") as f:
        f.write(url + "\n")
    with open("/tmp/tunnel_pid.txt", "w") as f:
        f.write(str(proc.pid) + "\n")
    print(f"Tunnel started: {url}")
    print(f"PID: {proc.pid}")
    sys.exit(0)
else:
    print(f"No URL captured. Buffer: {buffer[:500]}")
    proc.kill()
    sys.exit(1)
