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


def _without_span(opts: str) -> list:
    """The configured options, which rip the whole disc, without their span."""
    args = shlex.split(opts)
    if args and _SPAN.match(args[-1]) and (len(args) < 2 or args[-2] not in _VALUE_OPTS):
        args.pop()
    return args


def cdparanoia_args(opts: str, track: int, shift: int = 0) -> list:
    """cdparanoia arguments that rip one track to trackNN.cdda.wav.

    The configured options rip the whole disc, so their span is dropped and
    replaced with the track. Batch mode is what names the output file, and
    -e makes cdparanoia report what it ran into (see ParanoiaReport). shift
    is added to the sample offset (see alignment).
    """
    args = with_sample_offset(_without_span(opts), shift)
    if not any(a in ('-B', '--batch') for a in args):
        args.append('--batch')
    return with_reports(args) + ['--', str(track)]


def with_sample_offset(args: list, shift: int) -> list:
    """args with shift added to cdparanoia's sample offset (-O)."""
    if not shift:
        return args
    out = list(args)
    for i, a in enumerate(out):
        try:
            if a in ('-O', '--sample-offset') and i + 1 < len(out):
                out[i + 1] = str(int(out[i + 1]) + shift)
                return out
            if a.startswith('--sample-offset='):
                out[i] = f'--sample-offset={int(a.split("=", 1)[1]) + shift}'
                return out
            if a.startswith('-O') and len(a) > 2:
                out[i] = f'-O{int(a[2:]) + shift}'
                return out
        except ValueError:
            continue
    return out + ['-O', str(shift)]


# Output format options, which a read for lining up replaces with raw output.
_FORMAT_OPTS = {
    '-w', '--output-wav', '-a', '--output-aifc', '-f', '--output-aiff',
    '-p', '--output-raw', '-r', '--output-raw-little-endian',
    '-R', '--output-raw-big-endian', '-B', '--batch', '-e', '--stderr-progress',
}

# What a read for lining up covers: 5 seconds, from the middle of a track,
# searched for in the track's file up to MAX_SHIFT samples either way of
# where it should be (as far as the CUETools DB looks for pressings).
LINE_UP_SECTORS = 375
MAX_SHIFT = 5880
_SECTOR = 588  # samples


def line_up_start(total_samples: int) -> Optional[int]:
    """The sector of a track to start a read for lining up at, or None if
    the track is too short."""
    sectors = total_samples // _SECTOR
    margin = MAX_SHIFT // _SECTOR + 1
    if sectors < LINE_UP_SECTORS + 2 * margin:
        return None
    return (sectors - LINE_UP_SECTORS) // 2


def _span_time(sector: int) -> str:
    """A position in a track as cdparanoia's span takes it: m:ss.ff."""
    seconds, frames = divmod(sector, 75)
    return f'{seconds // 60}:{seconds % 60:02d}.{frames:02d}'


def line_up_args(opts: str, track: int, start: int, out: str, shift: int = 0) -> list:
    """cdparanoia arguments that read LINE_UP_SECTORS of track from sector
    start into out, as raw little-endian samples."""
    args = [a for a in _without_span(opts) if a not in _FORMAT_OPTS]
    span = f'{track}[{_span_time(start)}]-{track}[{_span_time(start + LINE_UP_SECTORS - 1)}]'
    return with_sample_offset(args, shift) + ['-r', '--', span, out]


def edge_shift(toc: str, track: int, shift: int) -> int:
    """The part of shift a track can't be read with, or 0.

    cdparanoia refuses a span that starts before the disc does or ends after
    it, which a shift would make it read for the first track (shift < 0,
    when it starts at the start of the disc) or the last (shift > 0). That
    track is read at the drive's offset and shifted with shift_wav instead.
    """
    starts = toc_starts(toc)
    if shift < 0 and track == 1 and starts and starts[0] * _SECTOR < -shift:
        return shift
    if shift > 0 and track == toc_track_count(toc):
        return shift
    return 0


def shift_wav(path: str, shift: int) -> None:
    """Make a WAV hold what reading it with shift more on the sample offset
    would have: its audio moves shift samples earlier (later, if negative)
    and keeps its length, with digital silence for what is beyond the disc.
    """
    import wave
    with wave.open(path, 'rb') as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    width = params.nchannels * params.sampwidth
    silence = bytes(abs(shift) * width)
    if shift > 0:
        frames = frames[shift * width:] + silence
    elif shift < 0:
        frames = silence + frames[:shift * width]
    with wave.open(path, 'wb') as w:
        w.setparams(params)
        w.writeframes(frames)


def decode(flac: str, path: str) -> bytes:
    """A FLAC file's audio as raw little-endian samples."""
    import subprocess
    return subprocess.run([flac, '-d', '-s', '-c', '--force-raw-format', '--endian=little',
                           '--sign=signed', path], capture_output=True, check=True).stdout


def total_samples(path: str) -> int:
    from mutagen.flac import FLAC
    return FLAC(path).info.total_samples


def alignment(read: bytes, start: int, track: bytes) -> Optional[int]:
    """How many samples later in its file the audio of a read of a track is.

    read holds raw samples from sample start of the track on, track the
    track's file decoded. A re-rip lines up with the file when it is read
    with the sample offset less this (cdparanoia's -O reads later samples
    the higher it is). None when the read can't be found in the file.

    Pieces of the read are looked for on their own, so a damaged part of
    the file does not stop the rest from being found; digital silence,
    which is found anywhere, is skipped.
    """
    piece = 4 * _SECTOR * 4
    votes: Counter = Counter()
    for k in range(8):
        at = (len(read) - piece) * (2 * k + 1) // 16 // 4 * 4
        chunk = read[at:at + piece]
        if len(chunk) < piece or chunk.count(chunk[:4]) == piece // 4:
            continue
        expected = start + at // 4
        lo, hi = max(0, expected - MAX_SHIFT) * 4, (expected + MAX_SHIFT) * 4 + piece
        pos = track.find(chunk, lo, hi)
        while pos != -1 and pos % 4:
            pos = track.find(chunk, pos + 1, hi)
        if pos != -1:
            votes[pos // 4 - expected] += 1
    if not votes:
        return None
    d, n = votes.most_common(1)[0]
    return d if 2 * n > sum(votes.values()) else None


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


def _leading_number(text: str) -> int:
    """The number a tag starts with ("2" in "2/3"), or 0."""
    m = re.match(r'\s*(\d+)', text or '')
    return int(m.group(1)) if m else 0


def disc_label(discnumber: str, totaldiscs: str = '') -> str:
    """Which disc of a release the tracks are on, as "disc 2 of 3".

    discnumber may hold the total too ("2/3"). Empty for a release of one
    disc, or when the tags don't say.
    """
    number = _leading_number(discnumber)
    total = _leading_number(totaldiscs) or _leading_number((discnumber or '').partition('/')[2])
    if not number or (total <= 1 and number <= 1):
        return ''
    return f'disc {number} of {total}' if total >= number else f'disc {number}'


def disc_of(path: str) -> str:
    """disc_label from a file's DISCNUMBER and TOTALDISCS tags."""
    try:
        from mutagen.flac import FLAC
        tags = FLAC(path).tags or {}
    except Exception:
        return ''
    first = lambda key: (tags.get(key) or [''])[0]
    return disc_label(first('discnumber'), first('totaldiscs') or first('disctotal'))


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
