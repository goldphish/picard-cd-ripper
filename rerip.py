# -*- coding: utf-8 -*-
"""Verifying a rip and re-ripping some of its tracks, without Qt.

The rip is verified with accuraterip-verify (from the accuraterip-verifier
repository) against the exact TOC of the CD in the drive. A track that does
not verify can be ripped again on its own: cdparanoia reads the same span
(the track's start up to the next track's), so the new file has exactly the
old one's length and can take its place.

A new rip never overwrites a file blindly. It is verified first, alongside
the album's other files, and replaces the old file only if it verifies.
"""

import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from typing import Optional

# cdparanoia options that take a value as the next argument, so that value
# is never mistaken for the span.
_VALUE_OPTS = {
    '-d', '--force-cdrom-device', '-g', '--force-generic-device',
    '-k', '--force-cooked-device', '-S', '--force-read-speed',
    '-O', '--sample-offset', '-n', '--force-default-sectors',
    '-t', '--toc-offset', '-x', '--test-flags', '-l', '--log-summary',
    '-L', '--log-debug',
}
_SPAN = re.compile(r'^[-\d\[\]:.]*\d[-\d\[\]:.]*$|^-$')


@dataclass
class Track:
    """One track of the disc and its file."""
    number: int  # on the disc, from 1
    title: str
    path: str


def cdparanoia_args(opts: str, track: int) -> list:
    """cdparanoia arguments that rip one track to trackNN.cdda.wav.

    The configured options rip the whole disc, so their span is dropped and
    replaced with the track. Batch mode is what names the output file.
    """
    args = shlex.split(opts)
    if args and _SPAN.match(args[-1]) and (len(args) < 2 or args[-2] not in _VALUE_OPTS):
        args.pop()
    if not any(a in ('-B', '--batch') for a in args):
        args.append('--batch')
    return args + ['--', str(track)]


def wav_name(track: int) -> str:
    """The file cdparanoia's batch mode writes a track to."""
    return f'track{track:02d}.cdda.wav'


def flac_args(opts: str, wav: str, out: str) -> list:
    return shlex.split(opts) + ['-o', out, wav]


def verifier_args(opts: str, toc: str, directory: str) -> list:
    """accuraterip-verify arguments for one album in directory.

    --force verifies even if a log says the album verified before.
    """
    args = shlex.split(opts) + ['--json', '--force']
    if toc:
        args += ['--toc', toc]
    return args + [directory]


def parse_result(output: str) -> dict:
    """The album result from accuraterip-verify --json output.

    Any other output (an error message) becomes an ERR result.
    """
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                result = json.loads(line)
            except ValueError:
                continue
            if isinstance(result, dict) and 'status' in result:
                return result
    text = output.strip() or 'accuraterip-verify printed nothing'
    return {'status': 'ERR', 'message': text.splitlines()[-1]}


def track_results(result: dict) -> dict:
    """Per-track results keyed by track number."""
    return {t['track']: t for t in result.get('tracks') or [] if 'track' in t}


def failing(result: dict) -> list:
    """The tracks worth re-ripping: those that did not verify when others did.

    When no track verifies, the disc (or the pressing) is not in the
    databases or the offset is wrong, and re-ripping tracks won't help.
    """
    tracks = track_results(result)
    if not any(t.get('verified') for t in tracks.values()):
        return []
    return sorted(n for n, t in tracks.items() if not t.get('verified'))


def make_view(tracks: list, replacements: dict, directory: str) -> None:
    """Fill directory with links to the album's files, one per track.

    replacements maps track numbers to new rips that stand in for their
    files. The links are named by track number, so accuraterip-verify puts
    them in disc order, and the album's other files (another disc in the
    same directory) are left out.
    """
    for name in os.listdir(directory):
        os.remove(os.path.join(directory, name))
    for t in tracks:
        src = replacements.get(t.number, t.path)
        os.symlink(os.path.abspath(src), os.path.join(directory, f'{t.number:02d}.flac'))


def copy_tags(src: str, dst: str) -> None:
    """Give dst exactly src's tags and pictures.

    The old file's tags win, ReplayGain included: they were computed over
    the whole album.
    """
    from mutagen.flac import FLAC
    old, new = FLAC(src), FLAC(dst)
    new.clear_pictures()
    if new.tags is None:
        new.add_tags()
    new.tags.clear()
    if old.tags is not None:
        for key, values in old.tags.as_dict().items():
            new.tags[key] = values
    for picture in old.pictures:
        new.add_picture(picture)
    new.save()


def replace_file(new: str, old: str) -> None:
    """Put new in old's place, atomically and with old's permissions.

    The copy is made next to old first, so a failure part way never leaves
    a truncated file in the library.
    """
    tmp = os.path.join(os.path.dirname(old), f'.{os.path.basename(old)}.rerip')
    try:
        shutil.copyfile(new, tmp)
        shutil.copymode(old, tmp)
        os.replace(tmp, old)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def title_of(path: str) -> str:
    """A track's title from its tags, else its file name."""
    try:
        from mutagen.flac import FLAC
        titles = (FLAC(path).tags or {}).get('title') or []
        if titles:
            return titles[0]
    except Exception:
        pass
    return os.path.splitext(os.path.basename(path))[0]


def toc_track_count(toc: str) -> Optional[int]:
    """The number of tracks in a "1 N leadout offsets..." TOC."""
    parts = toc.split()
    try:
        return int(parts[1]) - int(parts[0]) + 1
    except (IndexError, ValueError):
        return None


def describe(t: dict) -> str:
    """One track's result, for the table."""
    if t.get('verified'):
        conf = t.get('confidence') or 0
        return f'verified (confidence {conf})' if conf else 'verified'
    return f'did not verify (CRC {t.get("crc_v1", 0) & 0xffffffff:08x})'


def summary(result: dict) -> str:
    """The album result in a line."""
    status = result.get('status', 'ERR')
    message = result.get('message') or ''
    if status == 'NONE':
        message = 'not in AccurateRip or the CUETools DB'
    return f'{status}: {message}' if message else status
