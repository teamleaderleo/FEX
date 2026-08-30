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
    offset = len(MAGIC) + 40 + FOZ_HEADER.size
    return MAGIC + record_key + FOZ_HEADER.pack(len(payload), 3, len(payload), 0) + payload, offset


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

            self.assertEqual(result["ordinaryRecordCount"], 1)
            self.assertEqual(result["metadataRecordCount"], 0)
            self.assertTrue(result["minimalPayloads"])
            self.assertEqual(result["records"][0]["guestBytes"], 37)
            self.assertEqual(result["records"][0]["trailingBytes"], 0)
            self.assertEqual(result["totals"]["storedBlobBytes"], len(payload))

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
