"""Send one line to libtk's command channel (127.0.0.1:8767) inside Traktor and print the reply.

python tkcmd.py ping | "band probe" | "prop find" | "prop list app.traktor.decks.1." | "prop get <path>"
"""

from __future__ import annotations

import socket
import sys


def main() -> None:
    line = " ".join(sys.argv[1:]) or "ping"
    with socket.create_connection(("127.0.0.1", 8767), timeout=12) as s:
        s.sendall(line.encode() + b"\n")
        print(s.recv(65536).decode(errors="replace").rstrip())


if __name__ == "__main__":
    main()
