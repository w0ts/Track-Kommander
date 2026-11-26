#!/usr/bin/env python3
"""
Simulates Traktor sending data to Track Kommander — use this to verify the Python side.

Run Track Kommander first (in Webhook mode), then:
    python tools/traktor_test_client.py

You should see the 4 deck buttons in Track Kommander populate, and the NOW PLAYING
section update when a "deckLoaded" event is sent.

Pass a port as the first argument if your webhook port differs from 12345:
    python tools/traktor_test_client.py 12345
"""

import json
import sys
import time
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 12345
BASE = f"http://127.0.0.1:{PORT}"


def post(endpoint, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}/{endpoint}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            print(f"  POST /{endpoint}  →  {r.status} {r.read().decode()}")
    except Exception as e:
        print(f"  POST /{endpoint}  →  ERROR: {e}")


def get(endpoint):
    try:
        with urllib.request.urlopen(f"{BASE}/{endpoint}", timeout=3) as r:
            body = r.read().decode()
            print(f"  GET  /{endpoint}  →  {r.status}  {body}")
            return json.loads(body)
    except Exception as e:
        print(f"  GET  /{endpoint}  →  ERROR: {e}")
        return None


print(f"Sending fake Traktor data to Track Kommander on port {PORT}\n")

print("1. Loading track into Deck A (Daft Punk – Get Lucky)…")
post(
    "deckLoaded/A",
    {
        "title": "Get Lucky",
        "artist": "Daft Punk",
        "album": "Random Access Memories",
        "genre": "Electronic",
        "bpm": 116.0,
        "key": "9B",
        "filePath": "/Users/test/Music/Daft Punk - Get Lucky.mp3",
        "file_path": "/Users/test/Music/Daft Punk - Get Lucky.mp3",  # both variants
        "trackLength": 369.0,
    },
)
time.sleep(0.5)

print("\n2. Deck A starts playing…")
post("updateDeck/A", {"play": True, "isPlaying": True})
time.sleep(0.3)

print("\n3. Channel A goes on-air (fader up)…")
post("updateChannel/A", {"isOnAir": True, "volume": 0.9})
post("updateChannel/1", {"isOnAir": True, "volume": 0.9})  # numeric variant
time.sleep(0.3)

print("\n4. Loading track into Deck B (Leftfield – Afrika Shox)…")
post(
    "deckLoaded/B",
    {
        "title": "Afrika Shox",
        "artist": "Leftfield",
        "album": "Rhythm and Stealth",
        "genre": "Electronic",
        "bpm": 128.0,
        "key": "4A",
        "filePath": "/Users/test/Music/Leftfield - Afrika Shox.mp3",
        "file_path": "/Users/test/Music/Leftfield - Afrika Shox.mp3",
        "trackLength": 380.0,
    },
)
time.sleep(0.3)

print("\n5. Channel B is silent (off-air)…")
post("updateChannel/B", {"isOnAir": False, "volume": 0.0})
post("updateChannel/2", {"isOnAir": False, "volume": 0.0})
time.sleep(0.3)

print("\n6. Polling /command (simulating QML poll)…")
cmd = get("command")
if cmd and cmd.get("action"):
    print(f"   → command received: {cmd}")
else:
    print("   → no pending command (expected if you haven't clicked Load into Deck)")

print("\nDone. Check Track Kommander — decks A and B should show track info.")
print("Deck A should have a green on-air indicator, Deck B should be dim.")
