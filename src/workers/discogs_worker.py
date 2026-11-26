"""DiscogsWorker — looks the library up on Discogs, in the background.

Several threads, one allowance: Discogs grants 25 requests a minute (60 with
a token) and every thread draws on the same :class:`core.discogs_api.RateLimiter`,
so the allowance is used to the full while each thread waits on the network,
and never exceeded. Each answer is saved as it comes, so a stopped or crashed
run loses nothing and the next one carries on where this one ended. Only the
database is written; Traktor's collection is filled separately, by the user,
from the matches (core.discogs_write).
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait

from PySide6.QtCore import QThread, Signal

from core import discogs_store
from core.discogs_api import (
    ANONYMOUS_PER_MIN,
    TOKEN_PER_MIN,
    DiscogsClient,
    DiscogsStopped,
    RateLimiter,
)
from core.discogs_lookup import lookup
from core.discogs_match import DEFAULT_REGION, Query
from core.discogs_store import Lookup

log = logging.getLogger("discogs_worker")

#: Consecutive failed lookups after which the run gives up: Discogs is down,
#: the network is gone, or the token was revoked — retrying every track
#: would only burn the rate limit and fill the log.
MAX_ERRORS_IN_A_ROW = 5
#: Spotify requests per minute. A lookup is 1 to 10 of them (each likely
#: artist's search, then the track and its album); each waits its turn here
#: (lookup_track's before_request). Spotify publishes no limit.
SPOTIFY_REQUESTS_PER_MIN = 100
SPOTIFY_WAIT_429_S = 60.0
#: A 429 that asks for longer than this is a used-up quota, not a blip (one
#: run was told 85,560 s, about 24 hours): the Spotify part of the run stops.
SPOTIFY_QUOTA_STOP_S = 300.0
#: Tracks one run asks Spotify about, then it leaves the rest for the next
#: run. Spotify publishes no limit; one run of ~330 tracks in 14 minutes
#: used up the quota for a day, and the key is shared with the playlist sync
#: and every other copy of the app.
SPOTIFY_TRACKS_PER_RUN = 100


def spotify_key_set() -> bool:
    """The Spotify developer app is set up (Settings ▸ Traktor ▸ Spotify)."""
    try:
        from core import spotify_key
        from core.secrets import SPOTIFY_SECRET, get_secret

        return bool(spotify_key.client_id() and get_secret(SPOTIFY_SECRET))
    except Exception as exc:  # no config yet: no Spotify
        log.info("spotify: not set up (%s)", exc)
        return False


#: Lookups in flight at once. The limiter, not this, sets the pace; a few
#: threads are enough to hide each request's round trip.
THREADS = 4


class DiscogsWorker(QThread):
    progress = Signal(int, int, str)  # done, total, "Artist — Title"
    finished = Signal(dict)  # {"matched": n, "review": n, "none": n, "error": n}
    error = Signal(str)

    def __init__(
        self,
        todo: list[tuple[str, Query]],
        token: str = "",
        exact: bool = False,
        region: str = DEFAULT_REGION,
        parent=None,
        spotify_again: bool = False,
    ) -> None:
        super().__init__(parent)
        self._spotify_again = spotify_again
        self.todo = todo
        self._token = token
        self._exact = exact
        self._region = region
        self._stop = False
        #: Paths taken off the run before their turn (Library ▸ Spotify's
        #: right-click); set from the UI thread, read by feed().
        self.skipped: set[str] = set()
        # How far feed() has walked self.todo. An index, not an iterator: a
        # list iterator that once reached the end never sees what add()
        # appends after, and tracks added near the end of a run were lost.
        self._fed = 0
        self._spotify: RateLimiter | None = None  # paces Spotify searches, when the key is set
        self._spotify_key = False  # a Spotify key was set when the run began
        #: Tracks Discogs found doubtful or not at all: Spotify is asked about
        #: them after every Discogs lookup is through (_spotify_phase).
        self._unsettled: list[tuple[str, Query]] = []
        self._spotify_lock = threading.Lock()
        self._spotify_asked = 0  # tracks asked this run, up to SPOTIFY_TRACKS_PER_RUN
        #: Tracks Spotify still owes an answer after this run (limit or quota):
        #: the next run takes them (discogs_store.pending(spotify=True)).
        self.spotify_left = 0
        self.outcome: dict[str, int] = {"matched": 0, "review": 0, "none": 0, "error": 0}

    def stop(self) -> None:
        self._stop = True

    def add(self, todo: list[tuple[str, Query]]) -> None:
        """More tracks for the running lookup: fed after the ones waiting
        (feed() walks this list as it grows; appending is atomic)."""
        for item in todo:
            self.skipped.discard(item[0])
            self.todo.append(item)

    def skip(self, paths: set[str]) -> None:
        """Leave these out when their turn comes (one already asked runs on)."""
        self.skipped |= set(paths)

    def leftover(self) -> list[tuple[str, Query]]:
        """Tracks added too late for this run — after it had finished feeding —
        and not taken off again: the window starts another run with them."""
        return [item for item in self.todo[self._fed :] if item[0] not in self.skipped]

    def _waiting(self) -> int:
        """Tracks not handed to a thread yet, less the ones taken off."""
        return sum(1 for path, _q in self.todo[self._fed :] if path not in self.skipped)

    def run(self) -> None:
        """Run the lookups, and say it has finished however it ended — the
        window waits on ``finished`` to enable its controls again."""
        try:
            self._run()
        except Exception as exc:  # whatever it was: reported, never swallowed
            log.exception("discogs lookup failed")
            self.error.emit(f"Discogs lookup failed: {exc}")
        self.finished.emit(dict(self.outcome))

    # ── one track, on a pool thread ──────────────────────────────────────────

    def _one(self, clients: threading.local, limiter: RateLimiter, path: str, query: Query):
        client = getattr(clients, "client", None)
        if client is None:
            client = DiscogsClient(self._token, should_stop=lambda: self._stop, limiter=limiter)
            clients.client = client
        try:
            result = lookup(client, path, query, exact=self._exact, region=self._region)
        except DiscogsStopped:
            return None
        except Exception as exc:
            # An answer shaped unlike any seen: this track fails, the run goes
            # on. Saved, so it is retried after the fresh tracks rather than
            # stopping every run at the same place.
            log.exception("discogs: %s — %s", query.artist, query.title)
            result = Lookup(path, "error", error=f"unexpected: {exc}")
        discogs_store.save(result)
        if self._spotify_key and result.status in ("review", "none"):
            self._unsettled.append((path, query))  # Spotify is asked once Discogs is through
        return result

    @staticmethod
    def _settled_by_spotify(path: str, result):
        """A track Discogs could only offer for review is not sent to the
        manual review when Spotify knows it: its label or release date fills
        Traktor instead (core.tag_sources), marked as Spotify's. The Discogs
        candidates are kept with the lookup, and "Look up again" asks again."""
        from dataclasses import replace

        from core import spotify_store

        if result.status != "review" or not spotify_store.settles(path):
            return result
        log.info("discogs: %s — Spotify knows it, no review needed", path)
        return replace(result, status="spotify")

    def _ask_spotify(self, path: str, query: Query) -> None:
        """What Spotify knows of the track — release date, album, the label of
        its ℗ line — for the fields Discogs leaves empty. Once per track."""
        from core import spotify_store

        limiter = self._spotify
        try:
            if not spotify_store.should_ask(path, self._spotify_again):
                return
            if limiter is None:  # no key, or the quota stopped it
                if self._spotify_key:
                    with self._spotify_lock:
                        self.spotify_left += 1
                return
            with self._spotify_lock:
                if self._spotify_asked >= SPOTIFY_TRACKS_PER_RUN:
                    self.spotify_left += 1
                    return
                self._spotify_asked += 1
            from core.spotify_api import lookup_track

            details = lookup_track(
                query.artist,
                query.title,
                query.seconds or 0.0,
                before_request=lambda: limiter.acquire(lambda: self._stop),
            )
        except Exception as exc:  # SpotifyError (key, HTTP, network, stopped) or a bad answer
            if self._stop:
                return  # Stop pressed while a request waited its turn
            if getattr(exc, "code", 0) == 429 or "429" in str(exc):
                wait = getattr(exc, "retry_after", 0.0) or SPOTIFY_WAIT_429_S
                if wait > SPOTIFY_QUOTA_STOP_S:
                    self._spotify = None  # every thread sees it and stops asking
                    log.warning(
                        "spotify: quota used up (asked to wait %.1f h) — "
                        "not asking Spotify for the rest of this run",
                        wait / 3600,
                    )
                    return
                limiter.pause(wait)
            log.warning("spotify: %s — %s: %s", query.artist, query.title, exc)
            return
        if details is None:
            spotify_store.note_tried(path, "no_match")
        else:
            spotify_store.save_search(path, details)

    def _spotify_phase(self) -> None:
        """After Discogs: ask Spotify about the tracks it found doubtful or
        not at all (at most SPOTIFY_TRACKS_PER_RUN of them). A doubtful track
        Spotify is sure of is settled, so it leaves the review; one it is not
        sure of waits in the review with Spotify's guess."""
        todo = list(self._unsettled)
        log.info("spotify: %d track(s) Discogs could not settle", len(todo))

        def one(item: tuple[str, Query]) -> None:
            path, query = item
            if self._stop:
                return
            self._ask_spotify(path, query)
            stored = discogs_store.get(path)
            if stored is None or stored.status != "review":
                return
            settled = self._settled_by_spotify(path, stored)
            if settled.status != stored.status:
                discogs_store.save(settled)
                with self._spotify_lock:
                    self.outcome["review"] = max(0, self.outcome.get("review", 0) - 1)
                    self.outcome[settled.status] = self.outcome.get(settled.status, 0) + 1

        done = 0
        with ThreadPoolExecutor(max_workers=THREADS, thread_name_prefix="spotify") as pool:
            futures = {pool.submit(one, item): item for item in todo}
            for future in as_completed(futures):
                done += 1
                _path, query = futures[future]
                self.progress.emit(done, len(todo), f"Spotify · {query.artist} — {query.title}")
                exc = future.exception()
                if exc is not None:
                    log.warning("spotify: %s — %s: %s", query.artist, query.title, exc)

    # ── the run ──────────────────────────────────────────────────────────────

    def _run(self) -> None:
        total = len(self.todo)
        limiter = RateLimiter(TOKEN_PER_MIN if self._token else ANONYMOUS_PER_MIN)
        log.info(
            "discogs: looking up %d tracks, %d threads, %d/min (%s)",
            total,
            THREADS,
            limiter.per_minute,
            "token" if self._token else "no token",
        )
        if spotify_key_set():
            self._spotify = RateLimiter(SPOTIFY_REQUESTS_PER_MIN)
            self._spotify_key = True
            log.info(
                "spotify: searching tracks without Spotify data too (up to %d tracks this run)",
                SPOTIFY_TRACKS_PER_RUN,
            )
        clients = threading.local()
        running: dict[Future, tuple[str, Query]] = {}
        done = 0
        errors_in_a_row = 0
        with ThreadPoolExecutor(max_workers=THREADS, thread_name_prefix="discogs") as pool:

            def feed() -> None:
                while len(running) < THREADS and not self._stop:
                    if self._fed >= len(self.todo):
                        return
                    item = self.todo[self._fed]
                    self._fed += 1
                    if item[0] in self.skipped:
                        continue
                    running[pool.submit(self._one, clients, limiter, *item)] = item

            feed()
            # Running, or tracks added and still waiting for a thread.
            while running or (not self._stop and self._waiting()):
                if not running:
                    feed()
                    continue
                finished, _pending = wait(running, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in finished:
                    _path, query = running.pop(future)
                    result = future.result()
                    if result is None:  # stopped while waiting its turn
                        continue
                    done += 1
                    self.outcome[result.status] = self.outcome.get(result.status, 0) + 1
                    total = done + len(running) + self._waiting()  # grows with add()
                    self.progress.emit(done, total, f"{query.artist} — {query.title}")
                    if result.status != "error":
                        errors_in_a_row = 0
                        continue
                    # Network, server, token or rate limit: a lookup error is
                    # always one that may pass (permanent ones are "none").
                    errors_in_a_row += 1
                    log.warning("discogs: %s — %s: %s", query.artist, query.title, result.error)
                    if errors_in_a_row >= MAX_ERRORS_IN_A_ROW and not self._stop:
                        self.error.emit(f"Discogs lookups keep failing ({result.error}); stopped.")
                        self._stop = True
                feed()
        if self._spotify_key and self._unsettled and not self._stop:
            self._spotify_phase()
        if self.spotify_left:
            log.info(
                "spotify: %d track(s) asked this run, %d more wait for the next run",
                self._spotify_asked,
                self.spotify_left,
            )
        self.progress.emit(done, total, "")
