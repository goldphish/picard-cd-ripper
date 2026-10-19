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
