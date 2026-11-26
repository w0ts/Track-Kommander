"""Smoke test of a trimmed kit (trim_kit.py): Qt window + AVFoundation media, librosa, CLAP in onnxruntime, every TK module.

Run with the kit's own python, never with -I (it would ignore PYTHONDONTWRITEBYTECODE and write caches into the kit):
  PYTHONHOME=<kit>/python PYTHONDONTWRITEBYTECODE=1 NUMBA_CACHE_DIR=<scratch> <kit>/python/bin/python3.13 smoke_kit.py <kit>
"""

import importlib
import os
import pkgutil
import sys
import time

kit = sys.argv[1]
WINDOWS = sys.platform == "win32"
site = kit + ("/.venv/Lib/site-packages" if WINDOWS else "/.venv/lib/python3.13/site-packages")
sys.path[:0] = [site, kit + "/src"]
# The system's own media backend (the kit has no Qt ffmpeg); it needs the real platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if WINDOWS else "cocoa")
os.environ["QT_MEDIA_BACKEND"] = "windows" if WINDOWS else "darwin"
SOUND = r"C:\Windows\Media\chimes.wav" if WINDOWS else "/System/Library/Sounds/Hero.aiff"
t0 = time.time()

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: E402
from PySide6.QtNetwork import QNetworkAccessManager  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication([])
QLabel("ok")  # never shown: the build must not take the screen
QNetworkAccessManager()
player = QMediaPlayer()
audio = QAudioOutput()  # kept: freed at once, Windows' backend says "Unable to play"
audio.setVolume(0)
player.setAudioOutput(audio)
player.setSource(QUrl.fromLocalFile(SOUND))
for _ in range(150):
    app.processEvents()
    time.sleep(0.02)
media = player.mediaStatus().name
print("qt ok; media status:", media, "error:", player.errorString() or "-")
problems = [] if media == "LoadedMedia" else [f"media did not load: {media}"]

import librosa  # noqa: E402
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

y = np.sin(np.linspace(0, 2000, 22050 * 3)).astype(np.float32)
print("librosa tempo:", librosa.beat.beat_track(y=y, sr=22050)[0])
sess = ort.InferenceSession(kit + "/clap_model/clap_audio.onnx", providers=["CPUExecutionProvider"])
print("onnx inputs:", [i.name for i in sess.get_inputs()])

failed = []
for root in ("core", "workers", "ui"):
    pkg = importlib.import_module(root)
    for m in pkgutil.walk_packages(pkg.__path__, root + "."):
        last = m.name.rsplit(".", 1)[-1]
        windows_only = last.startswith("win_") or last.endswith("_win")
        mac_only = last.startswith("mac_") or last.endswith("_mac")
        if (windows_only and not WINDOWS) or (mac_only and WINDOWS):
            continue  # the other system's modules
        try:
            importlib.import_module(m.name)
        except Exception as exc:  # report every module that cannot import in the kit
            failed.append(f"{m.name}: {type(exc).__name__}: {exc}")
print("TK modules failing:", len(failed))
for f in failed:
    print("  ", f)
print(f"done in {time.time() - t0:.1f} s")
problems += failed
if problems:
    print("SMOKE FAILED:", "; ".join(problems))
    sys.exit(1)
print("smoke ok")
