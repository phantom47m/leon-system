#!/usr/bin/env python3
"""Quick WhatsApp bridge test — sends a message to your phone."""
import json
import urllib.request

url = "http://127.0.0.1:3001/send"
data = json.dumps({"number": "17275427167", "message": "Leon is alive. WhatsApp bridge working."}).encode()

try:
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = resp.read().decode()
        print(f"Status: {resp.status}")
        print(f"Response: {result}")
except urllib.error.URLError as e:
    print(f"Connection failed: {e.reason}")
except Exception as e:
    print(f"Error: {e}")

# Also check health
try:
    with urllib.request.urlopen("http://127.0.0.1:3001/health", timeout=5) as resp:
        print(f"Health: {resp.read().decode()}")
except Exception as e:
    print(f"Health check failed: {e}")
