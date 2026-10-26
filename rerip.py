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

import bisect
import json
import os
import re
import shlex
import shutil
from collections import Counter
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
    replaced with the track. Batch mode is what names the output file, and
    -e makes cdparanoia report what it ran into (see ParanoiaReport).
    """
    args = shlex.split(opts)
    if args and _SPAN.match(args[-1]) and (len(args) < 2 or args[-2] not in _VALUE_OPTS):
        args.pop()
    if not any(a in ('-B', '--batch') for a in args):
        args.append('--batch')
    return with_reports(args) + ['--', str(track)]


def with_reports(args: list) -> list:
    """args with cdparanoia's -e (machine-readable progress) added."""
    if any(a in ('-e', '--stderr-progress') for a in args):
        return list(args)
    return ['-e'] + list(args)


# cdparanoia -e writes one line per event: "##: <code> [<event>] @ <position>",
# the position in 16-bit words from the start of the disc.
_EVENT = re.compile(r'^##: -?\d+ \[([^\]]*)\] @ (-?\d+)\s*$')
WORDS_PER_SECTOR = 1176

# The events worth reporting, worst first, with how to name them. The rest
# ("read", "verify", "wrote", "overlap", "backoff", ...) are routine.
PARANOIA_EVENTS = [
    ('skip', 'skip', 'skips'),
    ('transport error', 'drive error', 'drive errors'),
    ('cache error', 'cache error', 'cache errors'),
    ('scratch', 'scratch', 'scratches'),
    ('scratch repair', 'scratch repair', 'scratch repairs'),
    ('dropped', 'dropped-bytes fix', 'dropped-bytes fixes'),
    ('duped', 'duplicated-bytes fix', 'duplicated-bytes fixes'),
    ('drift', 'drift fix', 'drift fixes'),
    ('jitter', 'jitter fix', 'jitter fixes'),
    ('correction', 'correction', 'corrections'),
]
_REPORTED = {name for name, _, _ in PARANOIA_EVENTS}


class ParanoiaReport:
    """Counts the problems cdparanoia -e reports, per track.

    Its event lines (thousands per track) are taken out of the output shown
    to the user. starts are the tracks' first sectors, counted from 0 as
    cdparanoia counts them; without them, everything is counted as track 0.
    """

    def __init__(self, starts: Optional[list] = None) -> None:
        self.starts = starts or []
        self.counts: dict = {}
        self._partial = ''

    def feed(self, text: str) -> str:
        """Count the events in text and return the rest of it."""
        lines = (self._partial + text).split('\n')
        self._partial = lines.pop()
        return ''.join(line + '\n' for line in self._take(lines))

    def flush(self) -> str:
        """Count a last line left without a newline; return it if not an event."""
        rest, self._partial = self._partial, ''
        return ''.join(self._take([rest])) if rest else ''

    def _take(self, lines: list) -> list:
        out = []
        for line in lines:
            m = _EVENT.match(line.strip())
            if not m:
                out.append(line)
            elif m.group(1) in _REPORTED:
                sector = int(m.group(2)) // WORDS_PER_SECTOR
                track = bisect.bisect_right(self.starts, sector) if self.starts else 0
                self.counts.setdefault(track, Counter())[m.group(1)] += 1
        return out

    def track(self, n: int = 0) -> Counter:
        return self.counts.get(n, Counter())


def paranoia_summary(counts: Counter) -> str:
    """What cdparanoia ran into on a track, worst first."""
    parts = [f'{counts[name]} {one if counts[name] == 1 else many}'
             for name, one, many in PARANOIA_EVENTS if counts.get(name)]
    return ', '.join(parts) if parts else 'no problems'


def toc_starts(toc: str) -> list:
    """Each track's first sector, counted from 0 as cdparanoia does."""
    parts = toc.split()
    try:
        return [int(x) - 150 for x in parts[3:]]
    except ValueError:
        return []


def audio_md5(path: str) -> bytes:
    """The MD5 of a FLAC file's audio, which flac stores when encoding.

    Two rips with the same MD5 read the same samples. Empty when unknown.
    """
    from mutagen.flac import FLAC
    md5 = FLAC(path).info.md5_signature
    return md5.to_bytes(16, 'big') if md5 else b''


def wav_name(track: int) -> str:
    """The file cdparanoia's batch mode writes a track to."""
    return f'track{track:02d}.cdda.wav'


def flac_args(opts: str, wav: str, out: str) -> list:
    return shlex.split(opts) + ['-o', out, wav]


def verifier_args(opts: str, toc: str, directory: str, album: str = '') -> list:
    """accuraterip-verify arguments for one album in directory.

    --force verifies even if a log says the album verified before. album is
    the album's own directory, whose files the links in directory stand in
    for: the verifier names it in the result and, when the album verifies,
    writes the log there.
    """
    args = shlex.split(opts) + ['--json', '--force']
    if toc:
        args += ['--toc', toc]
    if album:
        args += ['--album', album]
    return args + [directory]


def album_dir(tracks: list) -> str:
    """The directory the tracks' files are in, or '' if they are in several."""
    dirs = {os.path.dirname(os.path.abspath(t.path)) for t in tracks}
    return dirs.pop() if len(dirs) == 1 else ''


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
        by = ' by the CUETools DB' if t.get('source') == 'ctdb' else ''
        return f'verified{by} (confidence {conf})' if conf else f'verified{by}'
    return f'did not verify (CRC {t.get("crc_v1", 0) & 0xffffffff:08x})'


def summary(result: dict) -> str:
    """The album result in a line."""
    status = result.get('status', 'ERR')
    message = result.get('message') or ''
    if status == 'NONE':
        message = 'not in AccurateRip or the CUETools DB'
    return f'{status}: {message}' if message else status
