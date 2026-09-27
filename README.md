<!--
  This file is the public README. The release workflow publishes it as
  README.md of github.com/hdavid/Track-Kommander, together with LICENSE,
  THIRD_PARTY.md, licenses/, docs/ and the FFmpeg LGPL build scripts.
  Edit it here, in the private repo - the public repo is overwritten on
  every release and never edited by hand.
-->
# Track Kommander

**A companion for Traktor Pro 3 / 4**

**Find the next track.** It analyses your library, watches what is playing,
and puts the tracks that actually fit the current deck in front of you — then
loads the one you pick, with the transition already set up.

**Take that library to the club.** It writes a USB stick CDJs read on their
own — beat grids, hotcues, waveforms, playlists — straight from your Traktor
collection. No Rekordbox in the middle, no re-analysing everything in someone
else's software.

Runs on macOS and Windows, beside Traktor, on your own machine. Nothing is
uploaded anywhere.

![Track Kommander beside Traktor: the playing deck, the matching axes and the ranked next tracks](docs/screenshot.png)

---

## What it does

### Finding the next track

* **Analyses your collection** — BPM, musical key, and a set of perceptual
  features (energy, mood, vocal/instrumental, style) from
  [CLAP](https://huggingface.co/laion/clap-htsat-fused), an audio model that
  runs locally. Your files never leave the machine.
* **Watches the deck** — a small QML bridge inside Traktor reports what is
  playing, which deck is on air, and where the faders are.
* **Ranks the rest of your library against it** on the axes you care about:
  tempo, harmonic compatibility, energy, style, bassline and structure, and
  whatever else you weight in **Settings → Matching**.
* **Finds tracks that fit once transposed** — a semitone or three on Traktor's
  Key knob opens up matches the wheel alone would never offer. The shift is
  shown beside the key, and set on the deck when you load.
* **Loads the track you choose** onto any deck, by synthesizing a real drag or
  by driving Traktor's browser — and then, optionally, silences the channel,
  switches the headphone cue, beat-syncs, plays, and ramps the fader back up.
* **Auto DJ** — does all of that by itself before the current track runs out.
* **Drives from an S4 MK2** — the browse encoder and the CH1–CH4 CUE buttons
  navigate and load, so you never touch the laptop. Every control is
  rebindable in **Settings → Controls**.
* **Shows you your library** — feature distributions, spread and correlations
  across everything you have analysed (⌘⇧S / Ctrl+Shift+S).

### Taking it to the club

* **A USB stick a player reads on its own** — `PIONEER/export.pdb` and the
  per-track `.DAT`/`.EXT` analysis, written directly from `collection.nml`.
  Rekordbox is not needed to produce it and does not have to be installed.
* **Your Traktor work travels with it** — beat grids, hotcues and loops, BPM,
  musical key, ratings, and your playlists (smart ones evaluated at export
  time).
* **Rekordbox reads the stick as it is** — it appears under *Devices*, with
  waveforms and cues, and tracks drag from there into a collection.
* **Traktor playlists ride along** — `Traktor/<name>.nml`, each track carrying
  its cues and grid exactly as your collection has them. *Import Playlist* in
  Traktor on any machine.
* **Add or replace**, resumable, with progress and time remaining for every
  phase. Stopping leaves a stick that still plays; running it again adds the
  rest and re-decodes nothing.
* Audio is filed by artist and album and folded to ASCII, which is what older
  players expect.

> **Not yet confirmed on real hardware.** Every byte matches what Rekordbox
> writes wherever that could be checked, and Rekordbox itself accepts the
> analysis — necessary, but not the same as a CDJ loading it. Use a spare stick
> until you have tried one. Details in
> [docs/rekordbox-export.md](docs/rekordbox-export.md).

### Housekeeping

* **Detects missing hotcues** across the collection and writes them back into
  `collection.nml` (with a backup).
* **Fixes wrong beat grids from the mix.** Nudge a track into place by ear,
  right-click the deck, and the offset Traktor's phase meter is showing is
  queued — written to the grid marker later, when Traktor is closed.

---

## Install

Download the latest build from
[Releases](https://github.com/hdavid/Track-Kommander/releases):

| | |
|---|---|
| **macOS** | `Track Kommander.dmg` — signed and notarized. Drag to Applications. |
| **Windows** | `TrackKommanderSetup.msi` — a normal Next/Next/Install wizard. |

Roughly 300 MB to download and 700 MB installed: most of that is the audio
model, which ships inside the app. There is **no separate model download on
first run**.

### What you need

| | |
|---|---|
| **OS** | macOS 13 or newer, or Windows 10/11 (64-bit). |
| **Traktor** | Traktor Pro 4 or 3, installed and set up. |
| **Disk** | ~1 GB for the app, plus a few hundred MB of analysis database for a large library. |
| **Hardware** | Optional. A Kontrol S4 MK2 can drive the matcher. |

---

## First run

Only step 1 is needed to export to a USB stick: that reads `collection.nml`
and your audio files and nothing else — no scan, no bridge, no permissions.
Steps 2 to 5 are for the matcher, which has to analyse the library and drive
Traktor.

1. **Point it at your collection.** It looks for Traktor's `collection.nml`
   in the usual place; if you keep several, pick one in **Settings**.
2. **Scan the library** — **Library → Scan**. Analysis is incremental and
   runs in the background; a large collection takes a while the first time and
   nothing afterwards.
3. **macOS permissions.** When prompted, or in **System Settings → Privacy &
   Security**, enable:
   * **Accessibility** — to type into Traktor's search field and to simulate
     the drag onto a deck.
   * **Screen Recording** — for *Calibrate from screenshot*, which captures
     the Traktor window so you can click each deck's drop zone.

   The Settings tab shows live status for both.
4. **Install the Traktor bridge** (recommended). Without it, Track Kommander
   can still load tracks, but it cannot see deck state and cannot run the
   post-load automation. Quit Traktor, then **Settings → Traktor → Install
   into Traktor…**. It backs Traktor's D2 folder up, verifies the copy, and
   only then adds its own module — nothing Native Instruments ships is
   replaced, so a real Kontrol D2 keeps working. **Restore** undoes it.

   You do not have to take the backup on trust: Traktor's own files are
   archived as `original-files.zip` and left in the folder that was changed,
   so you can open it and see exactly what was there before.
   `Traktor/D2/INSTALL.txt` is the manual fallback if that fails.

   Traktor loads the mapping only when a **Kontrol D2** is present in
   Preferences → Controller Manager (the hardware itself is not required).
   Traktor reads the mapping once, at startup, so restart it after installing.

   > Installing the bridge writes into Traktor's own application folder. The
   > originals are backed up first and can be restored from the app. On macOS
   > that means writing inside a code-signed bundle, which invalidates
   > Traktor's signature — Traktor runs fine afterwards, but you should know
   > it is happening before you agree to the prompt.

5. **Calibrate the deck drop zones**, once per Traktor layout: **Settings →
   Calibrate from screenshot**, then click each of the four deck drop zones
   (A, B, C, D) and press Enter. The clicks are stored relative to the Traktor
   window, so they survive it being moved or resized.

---

## Matching and loading

### The matching dimensions

Every result is scored on the dimensions you have switched on. Each one is a
card on the Now Playing tab:

* **Weight** — how much the dimension counts in the score. 0 turns it off.
* **Target** — for the scalar dimensions, the value you are aiming at. By
  default it *follows the current track*, so you get more of the same; drag it
  to hold a value of your own ("more energy than this"), click ⟲ to follow the
  track again.
* BPM, Key, Genre, Style and Similarity are *relational*: they compare each
  candidate with the current track, so they have a weight but no target.

Hover any card or column header in the app for the same explanation. ★ marks
the dimensions on out of the box.

Which ones to use? The **▦** button (⌘⇧S / Ctrl+Shift+S) opens library
statistics, where you switch dimensions on and off. It shows, for your own
crate, how much each dimension varies (a dimension where every track scores
the same cannot separate them) and how pairs correlate (two dimensions that
rise and fall together measure the same thing, and matching on both counts it
twice). **Auto-pick** keeps the most varied dimension of each correlated group
and drops the rest.

#### From Traktor

Read from your collection — nothing to analyse.

| Dimension | Low ←→ high | What it measures |
|---|---|---|
| **BPM / Tempo** ★ | slower ←→ faster | Traktor's BPM, compared with the current track. Full score within a few percent, then falling off. Half and double tempo count too — a 70 BPM track can follow a 140 one. |
| **Key (harmonic)** ★ | distant ←→ in key | Camelot closeness to the current track. Click the card to cycle Key → Key-T → off: Key-T also accepts tracks that land in key once Traktor's Key knob moves, up to three semitones. A semitone is seven places round the wheel, because the wheel is the circle of fifths. Each semitone costs a little, so a track that already fits still wins, and the column shows the shift. |
| **Rating** ★ | any ←→ 5★ | Your star rating in Traktor. Starts set to 5★, so better-rated tracks rank higher; move it to aim elsewhere. |
| **Genre** ★ | any genre ←→ same genre | Traktor's genre tag from the file's metadata. Matched on shared words (rarer tags count for more). |

#### Sound and mood — the CLAP audio model

CLAP listens to the track and compares it with written descriptions ("calm
relaxed peaceful chill music"…). Most of these are the probability that a
description fits, from 0 to 1.

| Dimension | Low ←→ high | What it measures |
|---|---|---|
| **Party / Energy** ★ | chill ←→ peak | How much the CLAP audio model hears 'energetic party music, club banger, festival' rather than its opposite. A probability, 0 to 1. |
| **Mood / Valence** ★ | dark ←→ uplifting | How positive the track feels, from dark and sad to euphoric. The CLAP model places it on a 1–9 scale between described anchor moods. |
| **Similarity** ★ | different ←→ sounds like current | Overall 'sounds like' from the CLAP audio embedding (cosine distance). Bundles timbre, genre and mood into one learned vector. |
| **Style** | any style ←→ same style | Genre detected from the audio by the CLAP model (not the file's tag). Matched on the detected genre mix. |
| **Happy** | somber ←→ joyful | Probability the CLAP model hears happy, cheerful, uplifting music. Close to Valence, as a yes/no question. |
| **Relaxed** | tense ←→ calm | Probability the CLAP model hears calm, peaceful, chill music. |
| **Aggressive** | smooth ←→ intense | Probability the CLAP model hears hard, heavy, intense music — industrial and hard techno end. |
| **Arousal / Drive** | low ←→ high energy | How much drive the track has, from sleepy to pounding. The CLAP model places it on a 1–9 scale between described energy levels. Close to Party, without the club framing. |
| **Vocal presence** | instrumental ←→ vocal | Probability the CLAP model hears a lead voice or singing. Low for instrumentals. |
| **Electronic** | acoustic ←→ synth | Probability the CLAP model hears synths and drum machines rather than acoustic instruments. |

#### Measured from the signal

Plain measurements of the audio: where the energy sits in the spectrum and how
much it moves.

| Dimension | Low ←→ high | What it measures |
|---|---|---|
| **Melodic / Rhythmic** ★ | percussive ←→ melodic | Share of the sound that is tonal (chords, leads, pads) rather than drums, measured by splitting the audio into its harmonic and percussive parts over 30 s from the middle of the track. |
| **Brightness** | warm ←→ bright | Where the sound's weight sits in the spectrum — its centre of mass. Low for warm, dark mixes; high for bright, trebly ones. |
| **HF Content** | dull ←→ crisp | Mean spectral energy above 5 kHz. Complements Brightness: Brightness = spectral centre of mass; HFC = raw high-band energy (hi-hats, attacks, air). |
| **Bass Energy** | thin ←→ bass-heavy | Mean spectral energy below 200 Hz — absolute bass level. |
| **Mid Energy** | hollow ←→ full-bodied | Mean spectral energy 200 Hz – 5 kHz. Covers the body, presence, and harmonic content of most instruments. |
| **Bassiness** | top-heavy ←→ bass-heavy | Fraction of total spectral energy below 200 Hz. Loudness-normalised — two tracks with different levels but the same tonal balance score equally. |
| **Bass Dynamics** | steady bass ←→ pumping | RMS variation in the bass band (< 200 Hz). High = pulsing/pumping kick; low = sustained or absent bass. |
| **Mid Dynamics** | sustained ←→ expressive | RMS variation in the body band (200 Hz – 5 kHz). High = large builds/drops or call-response arrangement. |
| **Hi Dynamics** | smooth highs ←→ percussive | RMS variation in the air band (> 5 kHz). High = busy hi-hats or prominent transients; low = filtered/smooth top. |
| **Danceability** | loose ←→ driving | How regular the pulse is — the peak of the onset pattern's self-similarity. Nearly every four-to-the-floor track scores high, so it separates little within one genre; Onset density says more. |
| **Dynamics** | flat ←→ dynamic | How much the overall level moves over the track (spread of its RMS energy). High for big breakdowns and drops, low for a track at one level throughout. |
| **Loudness** | quiet ←→ loud | Mean RMS energy — the track's average perceived level. Distinct from Dynamics (RMS variation). Useful for gain-matching awareness. |

#### Shape — how the track is built

Measured on Traktor's bar grid: how the track repeats, changes and moves its
bassline.

| Dimension | Low ←→ high | What it measures |
|---|---|---|
| **Loop** | through-composed ←→ tight loop | How closely a four-bar phrase resembles the next one. High for a groove that repeats, low for a track that keeps moving. |
| **Sameness** | goes somewhere ←→ stays put | How much the track sounds the same 32 bars later. High for a tool that runs unchanged, low for one built of distinct sections. |
| **Evolution** | static ←→ travels | How far the texture moves from one 16-bar block to the next. |
| **Onset density** | sparse ←→ busy | Note and drum onsets per bar — how busy the track is. Distinct from Danceability, which is the peak of the onset autocorrelation: that says how regular the pulse is and reads 0.83–0.95 for every techno record, while this spans a factor of two across the same crate. |
| **Breaks** | none ←→ many | Section boundaries per minute — breaks, drops and build-ups, found on the bar grid. |
| **Bass density** | sustained ←→ rolling | Bass notes per bar. Low for a held sub, high for a rolling sixteenth line. |
| **Bass range** | one note ←→ travels | Semitones the bassline covers. Near zero for the single-note bass under cold stab techno. |
| **Bounce** | flat ←→ bouncy | A bassline that is busy and travelling at once — density times range. Either on its own is a rolling one-note bass or a sparse melodic one; together they are what a DJ hears as bounce. First cut: it ranks two known-bouncy tracks top, but also puts a melodic wide-ranging bassline above them, so treat it as a candidate rather than a settled measure. |

### Loading a track

Right-click a result row → **Load into Deck A/B/C/D**, or use the S4 MK2's
load buttons. Two delivery methods, in **Settings → Load method**:

* **Drag & Drop** *(default)* — synthesizes a real drag from the result row
  onto the deck. Fast, accurate, no search ambiguity.
* **Search + QML** — types the path into Traktor's browser and asks the bridge
  to load the top result.

With automation on it always uses the QML path, because that is what makes the
post-load steps fire.

### Automation modes

Cycle the automation button at the top of the Now Playing tab:

| Mode | What happens after a load |
|---|---|
| Off | Nothing — just loads the track. |
| Play + Cue | Silence the channel fader, switch the headphone cue, beat-sync, play. |
| AutoMix | The above, plus a volume ramp over the configured number of bars. |
| Auto DJ | Picks and loads the next match before the current track ends, then runs the full transition. |

### Other things worth knowing

* Double-click the album-art icon to snap the window over Traktor's browser
  area; double-click again to restore.
* **Settings → Traktor → Turn Sync on for every deck** — Traktor has no "sync
  all" switch, so Track Kommander presses it on A–D when the bridge connects
  and after every load.
* **Library → Hotcues → Detect Hotcues** — finds tracks that have a beat grid
  but no cues and writes cues for them. Close Traktor first; a `.bak` is
  written automatically.
* **Fix phase** (right-click a deck) — with the tempos locked, whatever the
  phase meter still shows after you have nudged a track into place is the
  error in its beat grid. The menu names it in milliseconds and in beats, and
  queues it; **Library → Hotcues → Beat grid fixes** applies them one at a
  time or all at once, once Traktor is closed.
* **Settings → Controls** — rebind every keyboard shortcut and every S4
  control, including the keys forwarded through to Traktor.
* **Settings → General → Check for new versions at startup** *(on by
  default)* — once per launch, asks GitHub whether a newer release is out and
  says so above the results, with a Download button. Nothing about you or your
  library is sent. **Settings → About** links to the downloads and these docs.

---

## Exporting to Rekordbox and CDJs

**Library → Export.** Tick the playlists you want — ticking nothing exports
the whole collection — choose the drive, and **Export to USB…**. The
destination and your selection are remembered, so the next export is a couple
of clicks.

What lands on the stick:

```
Contents/<artist>/<album>/<audio>          the files themselves
PIONEER/rekordbox/export.pdb               the track and playlist database
PIONEER/USBANLZ/…/ANLZ0000.DAT             beat grid, cues A–C, waveform
                 /ANLZ0000.EXT             cues D–H, colour waveform
PLAYLISTS/<name>.m3u8                      plain playlists, for everything else
Traktor/<name>.nml                         playlists for Traktor's Import Playlist
```

Worth knowing:

* **Add or replace.** *Add* merges: what is already there stays, a track
  exported again is refreshed, a playlist of the same name is replaced.
  *Replace* rebuilds the stick from scratch and **deletes** audio that is no
  longer listed — the confirmation tells you how many tracks and how many GB
  before anything happens.
* **Stop is safe.** It finishes the track being written. What is on the stick
  stays playable, and running the export again picks up where it left off
  without decoding anything twice.
* **Eject properly.** The database is written last and has to be flushed.
* **Timing offset.** Traktor and Rekordbox do not always agree on where t=0
  sits in an MP3, so grids can land a few ms apart. Export, check one track,
  adjust if needed.

> **The player side has not been confirmed on real hardware yet.** Use a spare
> stick until you have tried one on a CDJ.

[docs/rekordbox-export.md](docs/rekordbox-export.md) has the full account —
what is written for which players (both `export.pdb` and the OneLibrary
`exportLibrary.db` the 2023+ players require), what is deliberately not
(`PSSI` phrase data, so the CDJ-3000 phrase view will be empty), and how the
blank database template is generated.

---


## Troubleshooting

* **"Drag falls back to search"** — the log shows `drag_to_deck: rejecting
  drag` with a non-Traktor owner at the destination. Re-run **Calibrate from
  screenshot**; stale calibration is discarded automatically once it falls
  outside Traktor's current bounds.
* **Automation does not fire** — check that Settings → Automation is not
  "Off", and that the Traktor connection indicator is green (bridge installed,
  Traktor running).
* **Logs**
  * macOS: `~/Library/Application Support/Track Kommander/logs/log.log`
    (plus `crash.log` for native crashes)
  * Windows: `%APPDATA%\Track Kommander\logs\log.log`
  * INFO by default. For DEBUG, tick **Settings → General → Verbose
    logging** (takes effect at once) or set `TK_LOG_LEVEL=DEBUG` in the
    environment, which overrides the checkbox.

---

## Contributing

Bug reports and feature requests go to the
[issue tracker](https://github.com/hdavid/Track-Kommander/issues). Please
attach the log (see Troubleshooting) and say which OS and Traktor version
you are on. Track Kommander is distributed as ready-built installers; the
source code is not published here.

---

## Licence and attribution

Track Kommander is **free to use, but not open source**. The installers are
distributed under the terms in [LICENSE](LICENSE): install it on as many of
your own machines as you like and use it for paid gigs, but do not
redistribute, modify or reverse engineer it. Copyright © 2026 Henri David.

It is built on open-source components, which keep their own licences: Qt
(PySide6), FFmpeg, libsndfile and soxr under the LGPL, the rest permissive.
FFmpeg is an audio-only build free of GPL components
(`scripts/build_ffmpeg_lgpl.sh`), and its complete source ships inside the
app. [THIRD_PARTY.md](THIRD_PARTY.md) walks through the whole audit — every
dependency, every licence, and which machine-learning models are bundled and
why the non-commercial ones are not. The full texts ship inside the binaries
under `licenses/`, reachable from **Settings → About**.

### Not affiliated with Native Instruments

Track Kommander is an independent project. It is **not affiliated with,
endorsed by, or sponsored by Native Instruments GmbH**. "Traktor", "Traktor
Pro" and "Traktor Kontrol" are their trademarks, used here only to describe
the software this tool works with. "Rekordbox" and "CDJ" are trademarks of
AlphaTheta Corporation.

No Native Instruments code is included — the QML bridge the app installs was
written for this project against Traktor's public controller API.
