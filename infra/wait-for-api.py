"""Bounded readiness polling for CI and local scripts."""

import argparse
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", default="http://127.0.0.1:8000/api/health/ready")
args = parser.parse_args()
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(args.url, timeout=2) as response:
            if response.status == 200:
                print("API ready")
                break
    except (urllib.error.URLError, TimeoutError):
        pass
    time.sleep(1)
else:
    raise SystemExit("API readiness timed out")
