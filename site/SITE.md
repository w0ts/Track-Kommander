<!--
  This file is the user site, https://w0ts.github.io/Track-Kommander/.
  scripts/public_site.py renders it into the site's index.html, install.html,
  matching.html and export.html. The repo's README is README.public.md
  (developers and licences). User-facing changes go here.
-->
# Track Kommander

**Inside Traktor Pro 4**

**Find the next track, in Traktor itself.** Track Kommander analyses your
library, watches the deck you choose, and lists the tracks that actually fit it
right in Traktor's own browser, ranked, with their score beside them. Load them
the way you always do.

**Take that library to the club.** It writes a USB stick CDJs read on their
own — beat grids, hotcues, waveforms, playlists — straight from your Traktor
collection. No Rekordbox in the middle.

macOS (Apple Silicon) and Windows, on your own machine. Nothing is uploaded
anywhere. Free software, under the GPL.

![Track Kommander inside Traktor: its band above the browser, the matching cards and the ranked tracks in Traktor's list](docs/screenshot.png)

---

## What it does

### Inside Traktor

* **A band of its own above Traktor's browser** — which deck to match (A–D),
  the matching cards, automation, and its pages: Hotcues, Tagging, Export,
  Stats, Settings.
* **A "Track Kommander" entry in Traktor's tree.** Click it and Traktor's list
  shows the best matches for the chosen deck, best first, with **SCORE**,
  **BPM MATCH** and **KEY MATCH** columns. They are Traktor's own rows: drag
  them to a deck, load them with your controller, sort them, as with any
  playlist.
* **No deck chosen?** The list shows a different random selection of your
  library each time Traktor starts, so a quiet moment turns up tracks you had
  forgotten.
* **Your Traktor is not changed.** Track Kommander runs in a copy of it
  ("Traktor Pro 4 TK") on macOS, or is started together with it on Windows.
  Your normal Traktor Pro 4 starts as before.

### Finding the next track

* **Analyses your collection** — BPM, musical key, and a set of perceptual
  features (energy, mood, vocal/instrumental, style) from
  [CLAP](https://huggingface.co/laion/clap-htsat-fused), an audio model that
  runs locally. New tracks are analysed while Traktor is idle. Your files never
  leave the machine.
* **Ranks the rest of your library against the deck** on the dimensions you
  care about: tempo, harmonic compatibility, energy, style, bassline and
  structure, and whatever else you weight on the cards.
* **Finds tracks that fit once transposed** — a semitone or three on Traktor's
  Key knob opens up matches the wheel alone would never offer.
* **Automation** — the band's AUTO button: after a load, cue and sync the new
  track, ramp it in, or let Auto DJ pick and load the next one before the
  current track runs out.
* **Shows you your library** — feature distributions, spread and correlations
  across everything analysed (the Stats button).

### Taking it to the club

* **A USB stick a player reads on its own** — `PIONEER/export.pdb` and the
  per-track `.DAT`/`.EXT` analysis, written directly from your Traktor
  collection. Rekordbox is not needed to produce it.
* **Your Traktor work travels with it** — beat grids, hotcues and loops, BPM,
  musical key, ratings, and your playlists.
* Details on the [Export](export.html) page.

### Housekeeping

* **Detects missing hotcues** across the collection and adds them.
* **Fills missing genre, label, catalog number and release date from
  Discogs** (and Spotify, with a key of your own). Tracks are looked up in the
  background; the empty fields in Traktor are filled only when you say so,
  after a preview.
* **Date playlists** — in Traktor's tree, by when tracks were imported and when
  they were released, kept up to date by Track Kommander.

---

## Install

Track Kommander runs inside **Traktor Pro 4.5.1** (build 21). Other Traktor
versions are refused: Track Kommander has to know Traktor's exact build, so
each Traktor update needs a Track Kommander release made for it.

### macOS

**You need** an Apple Silicon Mac (M1 or later), macOS 13 or newer, Traktor Pro
4.5.1 in its usual place, and about 700 MB of disk.

1. **Download** `Track Kommander.dmg` from
   [Releases](https://github.com/w0ts/Track-Kommander/releases/latest) and
   open it.
2. **Copy the install line into Terminal.** Open *Install - copy this line into
   Terminal.txt* in the disk image, copy its line, paste it into Terminal
   (Applications → Utilities → Terminal) and press Return:

   ```
   bash "/Volumes/Track Kommander/Track Kommander Files/install.sh"
   ```

   The installer runs in that Terminal window.
3. **Type your Mac password** when Terminal asks (nothing shows while you type:
   Traktor's folder is protected). If Traktor is running, the installer waits
   for you to quit it. Questions come up in Terminal too — answer `y` or `n`.
4. When it is done, Finder shows **Traktor Pro 4 TK** next to Traktor Pro 4,
   and it opens. Next time, open *Traktor Pro 4 TK* yourself (keep it in the
   Dock if you like).

**What the installer does.** It makes *Traktor Pro 4 TK* on your Mac, from
your own Traktor: Traktor's program, told to load Track Kommander, with Track
Kommander and its own Python inside; Traktor's other files are used through
links, not copied. It is signed "ad hoc" for your Mac, because a changed Traktor
cannot keep Native Instruments' signature. Your Traktor Pro 4 is not touched,
and the disk image holds no Native Instruments file. A log is kept in
`~/Library/Logs/Track Kommander/install.log`.

**Remove it:** drag *Traktor Pro 4 TK* to the Bin. To remove everything,
delete `~/Library/Application Support/Track Kommander` and
`~/Library/Caches/Track Kommander` too.

### Windows

**You need** Windows 10 or 11 (64-bit), Traktor Pro 4.5.1 installed in its
usual place, and about 700 MB of disk.

1. **Download** `TrackKommanderSetup.msi` from
   [Releases](https://github.com/w0ts/Track-Kommander/releases/latest) and run
   it. Windows may warn that the publisher is unknown (the installer is not
   signed): *More info* → *Run anyway*.
2. **Start Traktor with Track Kommander** from its Start-menu shortcut (quit
   Traktor first). It checks that your Traktor is the supported version, then
   starts it with Track Kommander inside.

Your Traktor is never changed: the launcher starts your installed Traktor and
loads Track Kommander into it. Starting Traktor from its own shortcut gives
you Traktor without Track Kommander, as before.

**Remove it:** Settings → Apps → *Track Kommander* → Uninstall. To remove
everything, delete `%LOCALAPPDATA%\Track Kommander` too.

---

## First run

1. **Open Traktor with Track Kommander** (macOS: *Traktor Pro 4 TK*; Windows:
   the Start-menu shortcut). Its band appears above Traktor's browser after a
   few seconds; the first start takes a little longer.
2. **Let it analyse your collection.** It starts by itself while Traktor is
   idle and carries on in the background; a large collection takes a while the
   first time and nothing afterwards.
3. **Pick the deck to match** with A–D in the band, and click **Track
   Kommander** in Traktor's tree: the list fills with the best matches.

macOS may ask whether "Traktor Pro 4" may use your Music folder, your
microphone or your controllers: allow, as you did for your normal Traktor (it
asks again because the TK copy is a different app to macOS).

---

## Matching and loading

### The matching dimensions

Every result is scored on the dimensions you have switched on. Each one is a
card above Traktor's list (the **≈** button in Track Kommander's band shows and
hides them):

* **Weight** — how much the dimension counts in the score. 0 turns it off.
* **Target** — for the scalar dimensions, the value you are aiming at. By
  default it *follows the current track*, so you get more of the same; drag it
  to hold a value of your own ("more energy than this"), click ⟲ to follow the
  track again.
* BPM, Key, Genre, Label, Release Year, Import Date, Style and Similarity are
  *relational*: they compare each
  candidate with the current track, so they have a weight but no target.

Hover any card for the same explanation. ★ marks the dimensions on out of the
box.

Which ones to use? The **Stats** button in the band opens library statistics,
where you switch dimensions on and off. It shows, for your own
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
| **Genre** ★ | any genre ←→ same genre | Discogs' genres and styles when the track has a Discogs match, else Traktor's genre tag — web addresses and 'Other' count as no genre. Matched on shared words (rarer ones count for more). |
| **Label** | other label ←→ same label | The record label: Traktor's, or the Discogs match's when Traktor has none. Same label scores full, another none; 'Planet Rhythm Records' is 'Planet Rhythm', and distributors and web addresses are not labels. |
| **Release Year** | years apart ←→ same year | When the record came out: Discogs' original year when the track has a match (not a reissue's), else Traktor's release date. Falls off over a few years. |
| **Import Date** | added apart ←→ added together | When the track entered your collection. Tracks added in the same weeks tend to belong together; falls off over a month or two. |

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

The matches are rows of Traktor's own list: load them the way you load any
track — drag a row onto a deck, use your controller's browse and load buttons,
or Traktor's keyboard shortcuts. Sorting by another column and back to SCORE
works as in any playlist.

### Automation

The band's **AUTO** button cycles the modes:

| Mode | What happens after a load |
|---|---|
| Off | Nothing — just loads the track. |
| Play + Cue | Silence the channel fader, switch the headphone cue, beat-sync, play. |
| AutoMix | The above, plus a volume ramp over the configured number of bars (Settings → AutoMix). |
| Auto DJ | Picks and loads the next match before the current track ends, then runs the full transition. |

### Other things worth knowing

* **Hotcues** (band) — finds tracks that have a beat grid but no cues and adds
  cues for them. **Cleanup** removes the ones it added.
* **Align grid** — beside the deck, *Align grid with master* moves the track's
  beat grid onto the master deck's, once you have nudged it into place by ear.
* **Tagging** (band) — **Look Up Tracks** asks Discogs about every track with
  an artist and a title, in the background, and keeps the release it is on:
  genres and styles, label, catalog number, country, the release date and the
  original year. With a Spotify key set in **Settings → Tagging** it asks
  Spotify too, for the album, release date and label. Sure matches are kept;
  doubtful ones wait under **Review**, where you pick the release or skip the
  track. **Fill Traktor…** then shows every empty field it would fill and
  writes the ones you keep. Nothing you typed is ever overwritten.
  Without a token Discogs allows 25 requests a minute (about 6 seconds a
  track); a free personal access token from Discogs, pasted into **Settings →
  Tagging**, raises that to 60. It is kept encrypted, not in the settings file.

---

## Exporting to Rekordbox and CDJs

The **Export** button in Track Kommander's band. Tick the playlists you want — ticking nothing exports
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

> **Tested on an XDJ-1000**, where a full export loads with waveforms and hot
> cues. The OneLibrary players (CDJ-3000X, CDJ-1500X, XDJ-AZ, OPUS-QUAD,
> OMNIS-DUO) are not tested yet. On one of those, try a spare stick first.

[docs/rekordbox-export.md](docs/rekordbox-export.md) has the full account —
what is written for which players (both `export.pdb` and the OneLibrary
`exportLibrary.db` the 2023+ players require), what is deliberately not
(`PSSI` phrase data, so the CDJ-3000 phrase view will be empty), and how the
blank database template is generated.

---


## Troubleshooting

* **The band says "Track Kommander not found at …"** — the app was not made
  completely: install it again from the disk image (macOS) or repair it from
  Settings → Apps (Windows).
* **The band says "No answer from Track Kommander"** — it is still starting
  (give it a few seconds), or it stopped: quit Traktor and open it again. If it
  keeps happening, send the logs below.
* **After a Traktor update, Track Kommander does not start** — each Traktor
  build needs a Track Kommander release made for it. Until that release is
  out, use Traktor as usual.
* **Logs** — please attach them to a bug report:
  * macOS: `~/Library/Logs/Track Kommander/` (the installer and the start
    inside Traktor), `/tmp/tk_dylib.log` (Track Kommander's loader) and
    `~/Library/Application Support/Track Kommander/logs/`
  * Windows: `%LOCALAPPDATA%\Track Kommander\logs\`

---

## Contributing

Bug reports and feature requests go to the
[issue tracker](https://github.com/w0ts/Track-Kommander/issues). Please
attach the logs (see Troubleshooting) and say which system and Traktor version
you are on. The source is in this repository; the README says how to build and
test it.

---

## Licence and attribution

Track Kommander is **free software**: you can share and change it under the
terms of the [GNU General Public License, version 3](LICENSE). Copyright ©
2026 w0ts.

It is built on open-source components, which keep their own licences: Qt
(PySide6), FFmpeg, libsndfile and soxr under the LGPL, the rest permissive.
FFmpeg is an audio-only build free of GPL components
(`scripts/build_ffmpeg_lgpl.sh`). [THIRD_PARTY.md](THIRD_PARTY.md) walks
through the whole audit — every dependency, every licence, and which
machine-learning models are bundled. The full texts ship with Track Kommander
under `licenses/`, reachable from **Settings → About**.

### Not affiliated with Native Instruments

Track Kommander is an independent project. It is **not affiliated with,
endorsed by, or sponsored by Native Instruments GmbH**. "Traktor", "Traktor
Pro" and "Traktor Kontrol" are their trademarks, used here only to describe
the software this tool works with. "Rekordbox" and "CDJ" are trademarks of
AlphaTheta Corporation. Track Kommander uses Discogs' API but is not
affiliated with, sponsored or endorsed by Discogs; "Discogs" is a trademark of
Zink Media, LLC.

No Native Instruments file is distributed with Track Kommander. It works with
the copy of Traktor you have installed, on your machine: on macOS the
installer makes a changed copy of it there, on Windows a launcher starts it;
your own Traktor Pro 4 is not modified.
