# picard-cd-ripper

A [MusicBrainz Picard](https://picard.musicbrainz.org/) plugin that rips a CD
with `cdparanoia`, encodes each track to FLAC, and attaches the files to the
matching tracks of the selected album.

Requires Picard 3.0 or newer. For Picard 2.x, use version 0.3.2 of this
plugin.

## Requirements

- `cdparanoia` and `flac` installed in `/usr/bin`
- The CD drive configured under *Options → CD Lookup*
- For verification: `accuraterip-verify` from
  [accuraterip-verifier](https://github.com/goldphish/accuraterip-verifier)
  (the Docker image below includes it)

## Installation

Install from the git repository, either in Picard (*Options → Plugins →
Install plugin…*) or from the command line:

```sh
picard-cli plugins install https://github.com/goldphish/picard-cd-ripper
```

Then restart Picard and enable the plugin.

## Usage

1. Insert a CD, look it up, and load the matching release.
2. Right-click the album and choose *Plugins → Rip CD...*.
3. When ripping and encoding finish, click *Finished*.

Ripping a CD again replaces its old files rather than adding new ones beside
them. A track's old file, whether loaded on the album in Picard or saved
where Picard would save the new rip, gets the new audio in place and keeps
its tags. (Otherwise Picard would attach a second file to the track, or save
the new one as `Title (1).flac`.) To re-rip only a few tracks, use *Verify
and re-rip* below.

When the verifier is installed, the new rip is verified as soon as you click
*Finished* (see below), while the CD is still in the drive. Save in Picard
after closing that window: until then the files are in a temporary directory.

cdparanoia, flac and accuraterip-verify command line options can be changed
under *Options → Plugins → CD Ripper*. Set `--offset` in the verifier's
options to your drive's read offset (6 by default). Settings from the Picard 2 version of the
plugin are carried over the first time the plugin is enabled.

## Verify and re-rip

A rip where a track or two doesn't verify usually has read errors in just
those tracks. Rather than ripping the whole CD again:

1. Put the CD in the drive and load its release with the files on it.
2. Right-click the album and choose *Plugins → Verify and re-rip CD...*.
3. The rip is verified against AccurateRip and the CUETools DB with the
   exact TOC of the CD in the drive. Tracks that fail while others verify
   are ticked.
4. Clean the disc if needed and click *Re-rip selected*. Each ticked track
   is ripped on its own (`cdparanoia --batch N`, which reads the same span
   as the full rip), encoded with your flac options, and given the old
   file's tags, pictures and ReplayGain.
5. The new rips are verified together with the album's other files. A
   track's file is replaced (atomically, in place) only if its new rip
   verifies, by AccurateRip or, track by track, by the CUETools DB.
   Otherwise the original is kept.

The *cdparanoia* column shows what cdparanoia ran into on each track's last
rip: skips (parts it gave up on), drive errors, scratches and the
corrections it made. The plugin runs cdparanoia with `-e` to get this, and
the full *Rip CD* reports it per track as well. A clean report doesn't prove
a track is right: a drive can fill in damaged audio the same wrong way on
every read, and cdparanoia can't see that.

### A track that never verifies

Re-rip it a few times, ideally after cleaning the disc and in another
drive. The window compares each re-rip's audio with the one before and with
the current file:

- **Different audio each time:** the drive is struggling. Keep trying,
  slower (`-S 4` in the cdparanoia options) or in another drive.
- **The same audio twice, with no skips:** the drive reads it consistently.
  *Keep consistent re-rips* then replaces the file with it. Use this when
  nothing else helps: it is the best that disc gives, but no database
  confirms it, and the window says so. It is not offered when cdparanoia
  skipped parts of either rip, since it may have filled them the same way.
- **The same audio as the current file:** nothing to gain from keeping it.

When no track verifies at all, nothing is ticked: that usually means your
pressing isn't in either database (or the offset is wrong), which re-ripping
can't fix. cdcheck's *Re-rip in Picard* button opens the same window for
the album it verified, with the failing tracks already ticked.

## Docker

`Dockerfile-picard` extends
[jlesage/musicbrainz-picard](https://github.com/jlesage/docker-musicbrainz-picard)
with cdparanoia, flac, `accuraterip-verify` (built from the `main` branch of
accuraterip-verifier), and this plugin. The plugin is copied to
`/opt/picard-cd-ripper`, and `docker/56-cdripper-plugin.sh` registers it with
Picard each time the container starts (`/config` is a volume, so this can't
happen at build time). Rebuild the image to pick up plugin changes.

### Compose and the cdcheckd bridge

`docker-compose.yml` runs Picard together with
[cdcheckd](https://github.com/goldphish/accuraterip-verifier), a phone app that
checks each CD's release and verifies rips. cdcheckd drives Picard through a
small HTTP bridge in this plugin (see `DESIGN.md` in that repository):

| Endpoint | Does |
|---|---|
| `GET /status` | Picard and plugin versions, and whether "Clear existing tags" is on |
| `GET /disc` | Reads the CD in the drive: disc ID, TOC, MCN, ISRCs |
| `POST /load` | `{paths[], release?, discid?}`: loads the release with the disc ID and adds the files to it, moving them off the release their tags name if it differs, so that saving writes `MUSICBRAINZ_DISCID` |
| `POST /rerip` | `{files[], tracks[], discid?}`: opens the Verify and re-rip window for the files (in track order) with `tracks` ticked. Answers 409 unless the CD in the drive is `discid` with as many tracks as files |

Every request needs `Authorization: Bearer <token>`. The bridge is off by
default. Enable it under *Options → Plugins → CD Ripper → cdcheckd bridge*, or
set `CDRIPPER_BRIDGE_PORT` and `CDRIPPER_BRIDGE_TOKEN_FILE` in the
environment (as the compose file does). Never publish its port on the host.

Tests: `python3 -m unittest test_bridge test_rerip test_ripper` (the re-rip window's
tests need PyQt6, mutagen and `flac`; set `QT_QPA_PLATFORM=offscreen`
without a display).
