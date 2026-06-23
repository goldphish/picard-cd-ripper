# -*- coding: utf-8 -*-
"""Unit tests for cdripper/accuraterip.py.

Run with:  python3 -m unittest test_accuraterip

The module is imported directly by path so that importing the ``cdripper``
package (which pulls in ``discid`` and ``picard``) is not required.
"""

import importlib.util
import os
import struct
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    'accuraterip', os.path.join(_HERE, 'cdripper', 'accuraterip.py')
)
accuraterip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(accuraterip)


class _FakeTrack:
    def __init__(self, offset):
        self.offset = offset


class _FakeDisc:
    """Mimics the subset of discid.Disc that accuraterip uses.

    discid reports *absolute* LBAs (including the 150-sector lead-in).
    """
    def __init__(self, offsets, sectors, freedb_id):
        self.tracks = [_FakeTrack(o) for o in offsets]
        self.sectors = sectors
        self.freedb_id = freedb_id


class ComputeDiscIdsTest(unittest.TestCase):
    # 3-track disc. Absolute offsets include the 150-sector lead-in.
    #   absolute offsets : 150,  10000, 20000   leadout (sectors): 30000
    #   0-based offsets   :   0,   9850, 19850   0-based leadout  : 29850
    #
    # id1 = 0 + 9850 + 19850 + 29850                                = 59550  = 0x0000e89e
    # id2 = max(0,1)*1 + 9850*2 + 19850*3 + 29850*(3+1)
    #     = 1 + 19700 + 59550 + 119400                              = 198651 = 0x000307fb
    DISC = _FakeDisc([150, 10000, 20000], 30000, '1a2b3c4d')

    def test_disc_ids_use_zero_based_offsets(self):
        id1, id2, cddb_id = accuraterip.compute_disc_ids(self.DISC)
        self.assertEqual(id1, 0x0000e89e)
        self.assertEqual(id2, 0x000307fb)
        self.assertEqual(cddb_id, 0x1a2b3c4d)

    def test_lead_in_subtraction_matters(self):
        # Guard against the original bug: summing raw absolute offsets.
        absolute_id1 = (150 + 10000 + 20000 + 30000) & 0xFFFFFFFF
        id1, _, _ = accuraterip.compute_disc_ids(self.DISC)
        self.assertNotEqual(id1, absolute_id1)

    def test_disc_id_string_format(self):
        self.assertEqual(
            accuraterip.disc_id_string(self.DISC),
            '003-0000e89e-000307fb-1a2b3c4d',
        )

    def test_track1_offset_zero_is_clamped_in_id2(self):
        # Track 1 at 0-based offset 0 must still contribute (clamped to 1).
        disc = _FakeDisc([150, 10000], 20000, '00000000')
        # 0-based: [0, 9850], leadout 19850
        # id2 = max(0,1)*1 + 9850*2 + 19850*(2+1) = 1 + 19700 + 59550 = 79251
        _, id2, _ = accuraterip.compute_disc_ids(disc)
        self.assertEqual(id2, 79251)


class FetchUrlTest(unittest.TestCase):
    def test_url_fanout_and_filename(self):
        # Capture the URL fetch() builds without hitting the network.
        disc = ComputeDiscIdsTest.DISC
        captured = {}

        def fake_urlopen(url, timeout=None):
            captured['url'] = url
            raise RuntimeError('stop before network')

        orig = accuraterip.urllib.request.urlopen
        accuraterip.urllib.request.urlopen = fake_urlopen
        try:
            accuraterip.fetch(disc)
        finally:
            accuraterip.urllib.request.urlopen = orig

        # id1 = 0x0000e89e -> low three nibbles: e, 9, 8
        self.assertIn('/accuraterip/e/9/8/', captured['url'])
        self.assertTrue(
            captured['url'].endswith(
                'dBAR-003-0000e89e-000307fb-1a2b3c4d.bin'
            )
        )


class ParseBinTest(unittest.TestCase):
    def test_parse_single_pressing(self):
        # One pressing, 2 tracks. Header: track count + 3 disc IDs (13 bytes),
        # then per track: confidence (1) + crcv1 (4) + crcv2 (4).
        data = b''
        data += struct.pack('B', 2)            # track count
        data += struct.pack('<III', 1, 2, 3)   # 3 disc IDs (ignored on parse)
        data += struct.pack('<BII', 10, 0xAAAA1111, 0xBBBB2222)  # track 1
        data += struct.pack('<BII', 7, 0xCCCC3333, 0xDDDD4444)   # track 2

        result = accuraterip.parse_bin(data, 2)
        self.assertEqual(result[0], [(10, 0xAAAA1111, 0xBBBB2222)])
        self.assertEqual(result[1], [(7, 0xCCCC3333, 0xDDDD4444)])

    def test_mismatched_track_count_skipped(self):
        data = struct.pack('B', 3) + struct.pack('<III', 1, 2, 3)
        data += b'\x00' * (3 * 9)  # 3 track records for a 3-track pressing
        result = accuraterip.parse_bin(data, 2)
        self.assertEqual(result, [[], []])


class VerifyTrackTest(unittest.TestCase):
    ENTRIES = [(5, 0x11111111, 0x22222222), (3, 0x33333333, 0x44444444)]

    def test_v1_match(self):
        matched, conf = accuraterip.verify_track((0x11111111, 0), self.ENTRIES)
        self.assertTrue(matched)
        self.assertEqual(conf, 5)

    def test_v2_match(self):
        matched, conf = accuraterip.verify_track((0, 0x44444444), self.ENTRIES)
        self.assertTrue(matched)
        self.assertEqual(conf, 3)

    def test_no_match(self):
        matched, conf = accuraterip.verify_track((0xDEAD, 0xBEEF), self.ENTRIES)
        self.assertFalse(matched)
        self.assertEqual(conf, 0)

    def test_zero_crcv2_does_not_false_match(self):
        # Pure-Python fallback returns crcv2=0; a DB crcv2 of 0 must not match.
        entries = [(9, 0x55555555, 0)]
        matched, _ = accuraterip.verify_track((0x12345678, 0), entries)
        self.assertFalse(matched)


if __name__ == '__main__':
    unittest.main()
