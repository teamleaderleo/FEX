#!/usr/bin/env python3
"""Focused tests for the bounded FEX disk-cache shape analyzer."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import AnalyzeDiskCache


MAGIC = AnalyzeDiskCache.MAGIC_PREFIX + b"\x06"
FOZ_HEADER = struct.Struct("<IIII")
INDEX_ENTRY = struct.Struct("<QIQQ")
BLOB_HEADER = struct.Struct("<IIIIIIQQ")


def key(record_hash: int, *, metadata: bool = False) -> bytes:
    result = bytearray(40)
    result[:16] = f"{record_hash:016x}".encode("ascii")
    if metadata:
        result[39] = 0xFF
    return bytes(result)


def data_record(record_key: bytes, payload: bytes) -> tuple[bytes, int]:
    return append_data_record(MAGIC, record_key, payload)


def append_data_record(
    data: bytes, record_key: bytes, payload: bytes, *, gap: bytes = b""
) -> tuple[bytes, int]:
    start = len(data) + len(gap)
    offset = start + len(record_key) + FOZ_HEADER.size
    envelope = record_key + FOZ_HEADER.pack(len(payload), 1, 0, len(payload))
    return data + gap + envelope + payload, offset


def index_record(record_key: bytes, offset: int, size: int, record_hash: int) -> bytes:
    return (
        record_key
        + FOZ_HEADER.pack(INDEX_ENTRY.size, 1, 0, INDEX_ENTRY.size)
        + INDEX_ENTRY.pack(record_hash, size, 0, offset)
    )


def blob(record_hash: int, *, guest: int = 37, tail: bytes = b"") -> bytes:
    host = b"H" * 16
    payload = BLOB_HEADER.pack(guest, len(host), 1, 0, 0, 1, 0xCAFE, 0xBEEF)
    payload += host + b"P" * 8 + b"E" * 8 + b"O" * 4
    return payload + tail


class AnalyzerTests(unittest.TestCase):
    def write_cache(self, directory: Path, data: bytes, index: bytes) -> Path:
        base = directory / "RWCacheDB"
        base.with_suffix(".foz").write_bytes(data)
        (directory / "RWCacheDB_idx.foz").write_bytes(index)
        return base

    def test_minimal_ordinary_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record_hash = 0x1234
            payload = blob(record_hash)
            data, offset = data_record(key(record_hash), payload)
            index = MAGIC + index_record(key(record_hash), offset, len(payload), record_hash)
            result = AnalyzeDiskCache.analyze(self.write_cache(directory, data, index))

            self.assertEqual(result["format"], "teamleaderleo-fex-disk-cache-shape-v2")
            self.assertEqual(result["ordinaryRecordCount"], 1)
            self.assertEqual(result["metadataRecordCount"], 0)
            self.assertTrue(result["minimalPayloads"])
            self.assertEqual(result["records"][0]["guestBytes"], 37)
            self.assertEqual(result["records"][0]["trailingBytes"], 0)
            self.assertEqual(result["totals"]["storedBlobBytes"], len(payload))
            self.assertEqual(
                result["dataTopology"],
                {
                    "allBytesAccounted": True,
                    "interiorUnreferencedBytes": 0,
                    "magicBytes": len(MAGIC),
                    "physicallyContiguous": True,
                    "recordEnvelopeBytes": 56,
                    "referencedBytes": 56 + len(payload),
                    "referencedEnd": len(data),
                    "referencedExtents": [
                        {
                            "bytes": 56 + len(payload),
                            "kind": "ordinary",
                            "offset": len(MAGIC),
                            "payloadBytes": len(payload),
                            "payloadOffset": offset,
                            "record": 0,
                        }
                    ],
                    "referencedPayloadBytes": len(payload),
                    "trailingUnreferencedBytes": 0,
                    "unreferencedBytes": 0,
                    "unreferencedExtents": [],
                },
            )

    def test_legacy_guest_tail_is_measured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record_hash = 0xABCD
            payload = blob(record_hash, guest=11, tail=b"G" * 11)
            data, offset = data_record(key(record_hash), payload)
            index = MAGIC + index_record(key(record_hash), offset, len(payload), record_hash)
            result = AnalyzeDiskCache.analyze(self.write_cache(directory, data, index))

            self.assertFalse(result["minimalPayloads"])
            self.assertEqual(result["records"][0]["trailingBytes"], 11)

    def test_metadata_record_is_counted_without_parsing_as_fex_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record_hash = 0xFFFF
            record_key = key(record_hash, metadata=True)
            data, offset = data_record(record_key, b"meta")
            index = MAGIC + index_record(record_key, offset, 4, record_hash)
            result = AnalyzeDiskCache.analyze(self.write_cache(directory, data, index))

            self.assertEqual(result["metadataRecordCount"], 1)
            self.assertEqual(result["ordinaryRecordCount"], 0)
            self.assertEqual(
                result["dataTopology"]["referencedExtents"][0]["kind"], "metadata"
            )

    def test_interior_and_trailing_unreferenced_extents_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first_hash = 0x1111
            second_hash = 0x2222
            first_payload = blob(first_hash)
            second_payload = blob(second_hash)
            data, first_offset = data_record(key(first_hash), first_payload)
            gap_offset = len(data)
            data, second_offset = append_data_record(
                data, key(second_hash), second_payload, gap=b"GAP"
            )
            tail_offset = len(data)
            data += b"TAIL"
            index = MAGIC
            index += index_record(
                key(first_hash), first_offset, len(first_payload), first_hash
            )
            index += index_record(
                key(second_hash), second_offset, len(second_payload), second_hash
            )

            result = AnalyzeDiskCache.analyze(self.write_cache(directory, data, index))
            topology = result["dataTopology"]

            self.assertFalse(topology["physicallyContiguous"])
            self.assertEqual(topology["interiorUnreferencedBytes"], 3)
            self.assertEqual(topology["trailingUnreferencedBytes"], 4)
            self.assertEqual(topology["unreferencedBytes"], 7)
            self.assertEqual(
                topology["unreferencedExtents"],
                [
                    {"bytes": 3, "kind": "interior", "offset": gap_offset},
                    {"bytes": 4, "kind": "trailing", "offset": tail_offset},
                ],
            )
            self.assertEqual(
                topology["magicBytes"]
                + topology["referencedBytes"]
                + topology["unreferencedBytes"],
                len(data),
            )

    def test_index_must_reference_one_matching_data_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record_hash = 0x3333
            payload = blob(record_hash)
            data, offset = data_record(key(record_hash), payload)
            changed = bytearray(data)
            changed[len(MAGIC)] ^= 1
            index = MAGIC + index_record(
                key(record_hash), offset, len(payload), record_hash
            )
            base = self.write_cache(directory, bytes(changed), index)

            with self.assertRaisesRegex(AnalyzeDiskCache.CacheFormatError, "key disagreement"):
                AnalyzeDiskCache.analyze(base)

    def test_index_refuses_a_contradictory_data_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record_hash = 0x4444
            payload = blob(record_hash)
            data, offset = data_record(key(record_hash), payload)
            changed = bytearray(data)
            FOZ_HEADER.pack_into(
                changed, len(MAGIC) + 40, len(payload), 2, 0, len(payload)
            )
            index = MAGIC + index_record(
                key(record_hash), offset, len(payload), record_hash
            )
            base = self.write_cache(directory, bytes(changed), index)

            with self.assertRaisesRegex(
                AnalyzeDiskCache.CacheFormatError, "data record header"
            ):
                AnalyzeDiskCache.analyze(base)

    def test_overlapping_referenced_data_envelopes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            metadata_key = key(0, metadata=True)
            nested_payload = b"inner"
            nested_envelope = metadata_key + FOZ_HEADER.pack(5, 1, 0, 5) + nested_payload
            outer_payload = b"12345678" + nested_envelope
            data, outer_offset = data_record(metadata_key, outer_payload)
            nested_offset = outer_offset + 8 + len(metadata_key) + FOZ_HEADER.size
            index = MAGIC
            index += index_record(metadata_key, outer_offset, len(outer_payload), 0)
            index += index_record(metadata_key, nested_offset, len(nested_payload), 0)
            base = self.write_cache(directory, data, index)

            with self.assertRaisesRegex(AnalyzeDiskCache.CacheFormatError, "overlap"):
                AnalyzeDiskCache.analyze(base)

    def test_truncated_index_suffix_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            base = self.write_cache(directory, MAGIC, MAGIC + b"x")
            with self.assertRaisesRegex(AnalyzeDiskCache.CacheFormatError, "truncated index"):
                AnalyzeDiskCache.analyze(base)

    def test_out_of_range_data_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record_hash = 1
            index = MAGIC + index_record(key(record_hash), 10_000, 40, record_hash)
            base = self.write_cache(directory, MAGIC, index)
            with self.assertRaisesRegex(AnalyzeDiskCache.CacheFormatError, "outside"):
                AnalyzeDiskCache.analyze(base)

    def test_invalid_magic_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            base = self.write_cache(directory, b"not-a-cache", MAGIC)
            with self.assertRaisesRegex(AnalyzeDiskCache.CacheFormatError, "magic"):
                AnalyzeDiskCache.analyze(base)


if __name__ == "__main__":
    unittest.main()
