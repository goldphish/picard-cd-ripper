# picard-cd-ripper

A [MusicBrainz Picard](https://picard.musicbrainz.org/) plugin that rips a CD
with `cdparanoia`, encodes each track to FLAC, and attaches the files to the
matching tracks of the selected album.

Requires Picard 3.0 or newer. For Picard 2.x, use version 0.3.2 of this
plugin.

## Requirements

- `cdparanoia` and `flac` installed in `/usr/bin`
- The CD drive configured under *Options → CD Lookup*

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

cdparanoia and flac command line options can be changed under
*Options → Plugins → CD Ripper*. Settings from the Picard 2 version of the
plugin are carried over the first time the plugin is enabled.

## Docker

`Dockerfile-picard` extends
[jlesage/musicbrainz-picard](https://github.com/jlesage/docker-musicbrainz-picard)
with cdparanoia, flac, and this plugin. The plugin is copied to
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
| `POST /load` | `{paths[], release?, discid?}`: loads the release and adds the files |

Every request needs `Authorization: Bearer <token>`. The bridge is off by
default. Enable it under *Options → Plugins → CD Ripper → cdcheckd bridge*, or
set `CDRIPPER_BRIDGE_PORT` and `CDRIPPER_BRIDGE_TOKEN_FILE` in the
environment (as the compose file does). Never publish its port on the host.

Bridge tests: `python3 -m unittest test_bridge`.
