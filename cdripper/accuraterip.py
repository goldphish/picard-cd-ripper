# -*- coding: utf-8 -*-

import struct
import urllib.request
import urllib.error
import os
from typing import Optional

try:
    import accuraterip_checksum as _arc
    _HAS_NATIVE = True
except ImportError:
    _HAS_NATIVE = False


LEAD_IN = 150  # standard 2-second (150-sector) lead-in


def compute_disc_ids(disc):
    """Return (id1, id2, cddb_id) for an AccurateRip URL from a discid.Disc object.

    discid reports absolute LBAs (including the 150-sector lead-in), but the
    AccurateRip DiscIDs are computed from 0-based offsets (lba - 150).
    """
    offsets = [t.offset - LEAD_IN for t in disc.tracks]
    leadout = disc.sectors - LEAD_IN
    n = len(offsets)

    id1 = (sum(offsets) + leadout) & 0xFFFFFFFF
    id2 = (
        sum(max(offsets[i], 1) * (i + 1) for i in range(n))
        + leadout * (n + 1)
    ) & 0xFFFFFFFF
    cddb_id = int(disc.freedb_id, 16)

    return id1, id2, cddb_id


def fetch(disc) -> Optional[bytes]:
    """Fetch the AccurateRip .bin file for the disc. Returns None if not found."""
    n = len(disc.tracks)
    id1, id2, cddb_id = compute_disc_ids(disc)
    filename = f'dBAR-{n:03d}-{id1:08x}-{id2:08x}-{cddb_id:08x}.bin'
    url = (
        f'http://www.accuraterip.com/accuraterip/'
        f'{id1 & 0xF:x}/{(id1 >> 4) & 0xF:x}/{(id1 >> 8) & 0xF:x}/{filename}'
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except Exception:
        return None


def disc_id_string(disc) -> str:
    """Human-readable AccurateRip disc ID string."""
    n = len(disc.tracks)
    id1, id2, cddb_id = compute_disc_ids(disc)
    return f'{n:03d}-{id1:08x}-{id2:08x}-{cddb_id:08x}'


def parse_bin(data: bytes, n_tracks: int) -> list[list[tuple[int, int, int]]]:
    """Parse AccurateRip .bin data.

    Returns a list of length n_tracks, where each element is a list of
    (confidence, crcv1, crcv2) tuples — one per pressing in the database.
    """
    results: list[list[tuple[int, int, int]]] = [[] for _ in range(n_tracks)]
    offset = 0
    while offset < len(data):
        if offset + 13 > len(data):
            break
        n = struct.unpack_from('B', data, offset)[0]
        offset += 1 + 4 + 4 + 4  # skip track count + 3 disc IDs
        if n != n_tracks:
            # Different pressing with different track count — skip
            offset += n * 9
            continue
        for i in range(n):
            if offset + 9 > len(data):
                break
            confidence = struct.unpack_from('B', data, offset)[0]
            crcv1 = struct.unpack_from('<I', data, offset + 1)[0]
            crcv2 = struct.unpack_from('<I', data, offset + 5)[0]
            results[i].append((confidence, crcv1, crcv2))
            offset += 9
    return results


def compute_crcs(wav_path: str, track_idx: int, total_tracks: int) -> tuple[int, int]:
    """Compute AccurateRip CRCv1 and CRCv2 for a WAV file.

    track_idx is 0-based. Uses the C extension if available, else pure Python
    (CRCv1 only; returns 0 for CRCv2 in the fallback case).
    """
    if _HAS_NATIVE:
        return _arc.calculate(wav_path, track_idx, total_tracks)
    return _compute_crcv1_pure(wav_path, track_idx, total_tracks), 0


def _compute_crcv1_pure(wav_path: str, track_idx: int, total_tracks: int) -> int:
    """Pure-Python AccurateRip CRCv1 implementation."""
    SKIP = 2940  # 5 CD frames * 588 samples/frame
    WAV_HEADER = 44

    with open(wav_path, 'rb') as f:
        f.seek(WAV_HEADER)
        raw = f.read()

    count = len(raw) // 4
    is_first = track_idx == 0
    is_last = track_idx == total_tracks - 1

    skip_start = SKIP if is_first else 0
    skip_end = SKIP if is_last else 0

    crc = 0
    for i in range(count - skip_end):
        sample = struct.unpack_from('<I', raw, i * 4)[0]
        if i < skip_start:
            continue
        crc = (crc + (i + 1) * sample) & 0xFFFFFFFF
    return crc


def verify_track(
    crcs: tuple[int, int],
    entries: list[tuple[int, int, int]],
) -> tuple[bool, int]:
    """Check computed CRCs against AccurateRip database entries.

    Returns (matched, best_confidence).
    """
    crcv1, crcv2 = crcs
    best_confidence = 0
    for confidence, db_crcv1, db_crcv2 in entries:
        if crcv1 == db_crcv1 or (crcv2 and crcv2 == db_crcv2):
            if confidence > best_confidence:
                best_confidence = confidence
    matched = best_confidence > 0
    return matched, best_confidence
