# Exporting to a USB stick

Track Kommander writes a Pioneer/AlphaTheta device export straight from your
Traktor collection — a stick a CDJ reads on its own, with beat grids, hotcues
and waveforms. No Rekordbox needed to produce it.

Rekordbox reads the same stick directly, and it carries Traktor playlists
too, so it can go back into Traktor on another machine.

> **The player side has not been confirmed on real hardware yet.** Every byte
> matches what Rekordbox writes wherever that could be checked, and Rekordbox
> itself accepts the analysis — necessary, but not the same as a CDJ loading
> it. Use a spare stick until you have tried one.

## Before the first export

Nothing. The database template is generated if there isn't one
(see [The blank `export.pdb`](#the-blank-exportpdb)).

## Exporting

1. **Library → Export tab.**
2. Tick the playlists to export. Ticking nothing exports the whole
   collection. Smart playlists are evaluated at export time, so they carry
   whatever matches then.
3. Choose the options:
   - **On the key** *(Add by default)* —
     - **Add to what's there** — merge. Tracks and playlists already on the
       stick are kept; a track you export again is refreshed, and a playlist
       of the same name is replaced.
     - **Replace what's there** — the stick ends up holding this selection
       and nothing else. The library is rebuilt from the blank template and
       the audio that is no longer listed is **deleted**. The confirmation
       says how many tracks and how many GB that is before anything is
       written.
   - **Re-analyse the audio** *(off by default)* — decode every track again,
     ignoring anything analysed before. Only needed if a file was replaced
     with different audio of the same size and modification time.
   - **Timing offset** — shifts every cue and beat grid. See
     [MP3 timing](#mp3-timing).
4. **Export to USB…**, pick the drive, confirm.
5. **Stop** ends the run after the track being written. What is already on the
   stick stays there and stays playable; running the export again adds the
   rest. Waveforms computed before you stopped are kept, so nothing is
   decoded twice.

Eject the drive properly before pulling it out — the database is written last
and needs to be flushed.

## What lands on the stick

```
Contents/<artist>/<album>/<audio files>
PIONEER/rekordbox/export.pdb                  the track and playlist database
PIONEER/rekordbox/exportLibrary.db            the same library in OneLibrary form
PIONEER/rekordbox/track-kommander.json        our own record of what was written
PIONEER/MYSETTING.DAT, MYSETTING2.DAT,
        DJMMYSETTING.DAT, DEVSETTING.DAT      player, mixer and device settings
PIONEER/USBANLZ/Pxxx/xxxxxxxx/ANLZ0000.DAT    beat grid, cues A–C, waveform
                             /ANLZ0000.EXT    cues D–H, colour waveform
                             /ANLZ0000.2EX    preview waveform (Rekordbox 7 browser)
PLAYLISTS/<name>.m3u8                         plain playlists, for everything else
Traktor/<name>.nml                            playlists for Traktor's Import Playlist
```

The four settings files are Rekordbox's defaults. Without them an XDJ-1000
stops at "MY SETTING DATA was not found" and nothing on the stick can be
browsed. A stick that already has them, say saved from your own Rekordbox,
keeps its own.

Folder and file names are plain ASCII (letters, digits, space and - _ . ( ) &)
of at most 20 characters, extension included, and no path is longer than 100
characters. Rekordbox cuts names at 48; an XDJ-1000 froze on sticks with
longer names, so ours stay well under. A merge renames a track already on the
stick whose name breaks these rules, and leaves out any carried-over track it
cannot rename, saying so in the report. The player shows
these names only in its folder view: the database carries the real title.

Each track's analysis goes in the folder a player computes from the track's
path, not one of our choosing: an XDJ-1000 ignores the path in the database.

`track-kommander.json` is how a later merge knows what changed: it fingerprints
each track's audio and its metadata separately, so a track whose cues moved is
rewritten without decoding its audio again.

The `.m3u8` files are plain UTF-8 text with paths relative to the playlist, so
the same stick works in a car stereo, a media player or another DJ's software
— none of which can read `export.pdb`. They are a convenience on top of the
real export: if writing them fails, the stick is still a valid device export.

The `Traktor/` files are what Traktor's own *Export Playlist* writes: each
track's entry copied from `collection.nml` — cues, grid, key and tags as they
are, no timing offset — with its location pointing at the copy on the stick.
In Traktor, right-click a playlist folder → *Import Playlist* and pick one. On
macOS a track is located by the stick's volume name, so a stick that keeps its
name is found on any Mac; otherwise Traktor's relocate finds the files. Like
the `.m3u8` files, a failure writing them leaves the device export intact.

## Using the stick in Rekordbox

Plug it in: it appears under *Devices* with both its Device Library
(`export.pdb`) and OneLibrary (`exportLibrary.db`), waveforms and cues
included. To bring tracks into a Rekordbox collection, drag them from there.

Earlier versions also wrote a `rekordbox.xml` to the root of the stick. It is
no longer written, and one left behind by an older export can be deleted.

## Caveats

### The blank `export.pdb`

The database is built by extending a blank one, because the writer library can
add to a file but not create it. Track Kommander generates that blank itself
(`core/pdb_seed.py`) and caches it, so no
Rekordbox-prepared stick is needed. If one happens to be plugged in, its blank
is preferred — a template Rekordbox wrote is by definition the shape Rekordbox
expects.

A generated blank leaves out the `COLORS` and `COLUMNS` rows a Rekordbox one
has (its eight colour names and the browser's column labels). Nothing in the
export reads them, but no player has confirmed it does not want them.

### Hotcues

Traktor stores its beat-grid anchor twice — once as the grid, once as a cue on
the same millisecond sitting in hotcue slot 1. That second one is not a cue you
placed, so it is dropped rather than exported; otherwise every track would
arrive with hotcue A pinned to the first beat. A cue you put anywhere else,
including slot 1, is exported normally.

Players read eight hotcues. Cues beyond that, and cues with no hotcue slot,
become memory cues in the XML and are not written to the device database.

### MP3 timing

MP3 encoder delay can put Traktor and Rekordbox a few milliseconds apart, which
shows up as cues landing slightly off the beat. Export one track, check it, and
set **Timing offset** if you need to. It shifts every cue and grid by the same
amount.

### Two databases

The players split in 2023. The **CDJ-3000X, CDJ-1500X, XDJ-AZ, OPUS-QUAD and
OMNIS-DUO** read only `exportLibrary.db` — OneLibrary, formerly Device Library
Plus — and show "Device Library Plus not found" on a stick without it. The
**CDJ-3000, XDJ-RX3, XDJ-XZ, the NXS2 generation and everything older** read
only `export.pdb`. Rekordbox writes both, and so does this export.

`exportLibrary.db` is a SQLite database encrypted with SQLCipher, keyed with
the one passphrase every rekordbox install shares. It carries the same tracks,
lookups and playlists as `export.pdb` — cues, grids and waveforms are not in
it, they stay in the ANLZ files both databases point at — so it is generated
from the finished `export.pdb` and can never disagree with it. AlphaTheta's
own export guide asks for exactly that: both libraries on one stick listing
the same tracks and playlists.

The 2023+ players also expect exFAT or FAT32; older players FAT32 or HFS+. The
2014-era XDJ-1000 and CDJ-2000nexus do not read exFAT.

> **Not confirmed on a OneLibrary player yet either.** The strongest check
> without a player has been done: Rekordbox 7 opened an 874-track stick,
> listed it under Devices ▸ key ▸ OneLibrary with the same playlists as the
> Device Library node, and its own *Convert from Device Library*, run on that
> file, changed nothing but the My Tag list (it adds its library's default
> tags, which a Traktor collection does not have). Nobody outside
> AlphaTheta's partners has published a hardware result for a third-party
> `exportLibrary.db`.

### Not written

- **`exportExt.pdb`** — the legacy My Tag database. Rekordbox writes it
  beside `export.pdb`, and creates one the moment it opens a stick without it;
  whether any player needs it is unknown.
- **`.3EX`** — a newer analysis variant players do not require.
- **`PSSI` phrase data** — the CDJ-3000 phrase view will be empty.

### Rebuilding leaves orphans

Rebuilding the database from the blank template leaves the audio and analysis
of earlier exports on the drive with nothing pointing at them — invisible to a
player, and still filling the stick.

**Replace** sweeps them, which is what makes it a replace rather than a way to
hide tracks: leaving the files behind is a state nobody wants. The sweep only
ever touches files inside `Contents/` and `PIONEER/` that the database *on that
key* does not reference, and it does nothing at all if that database cannot be
read — an unreadable `export.pdb` would otherwise make every file on the stick
look unreferenced.

Filenames are compared in one Unicode normal form. macOS writes decomposed
names to exFAT while the database rows hold the composed form, so `Maōh` is one
code point in the row and two on the drive; comparing them as written would
make a track the key still lists look unreferenced.

**Add** never sweeps: everything the key already had is still listed, so
nothing is an orphan.

### Damaged files

A file with corrupt MPEG frames still exports — a waveform is produced from
whatever decodes — but the damage is still in the audio a player has to get
through. If a track misbehaves on the CDJ, check whether it decodes cleanly
before suspecting the export.

## Where things live

| What | Where |
|------|-------|
| Export logic | `core/device_export.py` |
| Analysis file format | `core/anlz.py` |
| Copying and free space | `core/usb_export.py` |
| Rekordbox XML | `core/rekordbox.py` |
| Waveform cache | `core/waveform_cache.py` |
| The worker thread | `workers/device_export.py` |
| Cached blank template | `<app data>/export-seed.pdb` |
