#!/usr/bin/env python3
"""Strictly summarize record shapes and physical extents in a FEX disk cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import struct
import sys
from pathlib import Path
from typing import Sequence


MAGIC_PREFIX = b"\x81FOSSILIZEDB\x00\x00\x00"
MAGIC_BYTES = 16
KEY_BYTES = 40
DATA_RECORD_OVERHEAD = KEY_BYTES + 16
INDEX_RECORD_BYTES = 84
FOZ_HEADER = struct.Struct("<IIII")
INDEX_ENTRY = struct.Struct("<QIQQ")
BLOB_HEADER = struct.Struct("<IIIIIIQQ")
DEFAULT_MAX_FILE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_RECORDS = 4096


class CacheFormatError(RuntimeError):
    """The cache cannot be interpreted safely by this bounded analyzer."""


def _referenced_extent(
    data: bytes,
    key: bytes,
    stored_bytes: int,
    cache_offset: int,
    position: int,
    kind: str,
) -> dict[str, int | str]:
    if cache_offset < MAGIC_BYTES + DATA_RECORD_OVERHEAD:
        raise CacheFormatError(f"record {position} payload lacks a complete data envelope")
    start = cache_offset - DATA_RECORD_OVERHEAD
    if data[start : start + KEY_BYTES] != key:
        raise CacheFormatError(f"index/data key disagreement at record {position}")
    header = FOZ_HEADER.unpack_from(data, start + KEY_BYTES)
    if header != (stored_bytes, 1, 0, stored_bytes):
        raise CacheFormatError(f"invalid data record header at record {position}")
    return {
        "bytes": DATA_RECORD_OVERHEAD + stored_bytes,
        "kind": kind,
        "offset": start,
        "payloadBytes": stored_bytes,
        "payloadOffset": cache_offset,
        "record": position,
    }


def _data_topology(
    data: bytes, extents: list[dict[str, int | str]]
) -> dict[str, object]:
    ordered = sorted(extents, key=lambda extent: int(extent["offset"]))
    cursor = MAGIC_BYTES
    interior: list[dict[str, int | str]] = []
    for extent in ordered:
        start = int(extent["offset"])
        end = start + int(extent["bytes"])
        if start < cursor:
            raise CacheFormatError(
                f"referenced data records overlap at index record {extent['record']}"
            )
        if start > cursor:
            interior.append(
                {"bytes": start - cursor, "kind": "interior", "offset": cursor}
            )
        cursor = end

    unreferenced = interior
    trailing_bytes = len(data) - cursor
    if trailing_bytes:
        unreferenced = [
            *interior,
            {"bytes": trailing_bytes, "kind": "trailing", "offset": cursor},
        ]
    interior_bytes = sum(int(extent["bytes"]) for extent in interior)
    referenced_bytes = sum(int(extent["bytes"]) for extent in ordered)
    unreferenced_bytes = interior_bytes + trailing_bytes
    if MAGIC_BYTES + referenced_bytes + unreferenced_bytes != len(data):
        raise CacheFormatError("physical data extent accounting disagrees with file size")
    return {
        "allBytesAccounted": True,
        "interiorUnreferencedBytes": interior_bytes,
        "magicBytes": MAGIC_BYTES,
        "physicallyContiguous": unreferenced_bytes == 0,
        "recordEnvelopeBytes": len(ordered) * DATA_RECORD_OVERHEAD,
        "referencedBytes": referenced_bytes,
        "referencedEnd": cursor,
        "referencedExtents": ordered,
        "referencedPayloadBytes": sum(
            int(extent["payloadBytes"]) for extent in ordered
        ),
        "trailingUnreferencedBytes": trailing_bytes,
        "unreferencedBytes": unreferenced_bytes,
        "unreferencedExtents": unreferenced,
    }


def _read_regular(path: Path, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CacheFormatError(f"cannot open regular cache file {path.name}: {exc}") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise CacheFormatError(f"cache path is not a regular file: {path.name}")
        if details.st_size > max_bytes:
            raise CacheFormatError(
                f"cache file {path.name} is {details.st_size} bytes; limit is {max_bytes}"
            )
        chunks: list[bytes] = []
        remaining = details.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise CacheFormatError(f"short read from cache file: {path.name}")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _version(contents: bytes, name: str) -> int:
    if len(contents) < MAGIC_BYTES or contents[:15] != MAGIC_PREFIX:
        raise CacheFormatError(f"invalid Fossilize magic in {name}")
    version = contents[15]
    if version not in (5, 6):
        raise CacheFormatError(f"unsupported Fossilize version {version} in {name}")
    return version


def analyze(
    base: Path,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> dict[str, object]:
    data_path = Path(f"{base}.foz")
    index_path = Path(f"{base}_idx.foz")
    data = _read_regular(data_path, max_file_bytes)
    index = _read_regular(index_path, max_file_bytes)
    data_version = _version(data, data_path.name)
    index_version = _version(index, index_path.name)

    index_payload = len(index) - MAGIC_BYTES
    if index_payload % INDEX_RECORD_BYTES:
        raise CacheFormatError(
            f"truncated index suffix: {index_payload} bytes is not a multiple of "
            f"{INDEX_RECORD_BYTES}"
        )
    record_count = index_payload // INDEX_RECORD_BYTES
    if record_count > max_records:
        raise CacheFormatError(
            f"index contains {record_count} records; limit is {max_records}"
        )

    metadata_count = 0
    ordinary: list[dict[str, int | str]] = []
    referenced_extents: list[dict[str, int | str]] = []
    seen_hashes: set[int] = set()
    for position in range(record_count):
        start = MAGIC_BYTES + position * INDEX_RECORD_BYTES
        key = index[start : start + 40]
        header = FOZ_HEADER.unpack_from(index, start + 40)
        if header != (28, 1, 0, 28):
            raise CacheFormatError(f"invalid index record header at record {position}")
        record_hash, stored_bytes, last_access_time, cache_offset = INDEX_ENTRY.unpack_from(
            index, start + 56
        )
        if cache_offset > len(data) or stored_bytes > len(data) - cache_offset:
            raise CacheFormatError(f"record {position} points outside the data file")
        metadata = key[39] == 0xFF
        referenced_extents.append(
            _referenced_extent(
                data,
                key,
                stored_bytes,
                cache_offset,
                position,
                "metadata" if metadata else "ordinary",
            )
        )
        if metadata:
            metadata_count += 1
            continue
        try:
            key_hash = int(key[:16].decode("ascii"), 16)
        except (UnicodeDecodeError, ValueError) as exc:
            raise CacheFormatError(f"invalid ordinary-record key at record {position}") from exc
        if key_hash != record_hash:
            raise CacheFormatError(f"key/hash disagreement at record {position}")
        if record_hash in seen_hashes:
            raise CacheFormatError(f"duplicate ordinary-record hash at record {position}")
        seen_hashes.add(record_hash)
        if stored_bytes < BLOB_HEADER.size:
            raise CacheFormatError(f"short FEX blob header at record {position}")
        (
            guest_bytes,
            host_bytes,
            entrypoints,
            small_relocations,
            thunk_relocations,
            pages,
            guest_hash_low,
            guest_hash_high,
        ) = BLOB_HEADER.unpack_from(data, cache_offset)
        required_bytes = (
            BLOB_HEADER.size
            + host_bytes
            + pages * 8
            + entrypoints * 8
            + entrypoints * 4
            + small_relocations * 14
            + thunk_relocations * 37
        )
        if required_bytes > stored_bytes:
            raise CacheFormatError(f"truncated FEX blob at record {position}")
        ordinary.append(
            {
                "dataOffset": cache_offset,
                "entrypoints": entrypoints,
                "guestBytes": guest_bytes,
                "guestHash": f"{guest_hash_high:016x}{guest_hash_low:016x}",
                "hash": f"{record_hash:016x}",
                "hostBytes": host_bytes,
                "lastAccessTime": last_access_time,
                "pages": pages,
                "requiredBytes": required_bytes,
                "smallRelocations": small_relocations,
                "storedBytes": stored_bytes,
                "thunkRelocations": thunk_relocations,
                "trailingBytes": stored_bytes - required_bytes,
            }
        )

    totals = {
        "entrypoints": sum(int(item["entrypoints"]) for item in ordinary),
        "guestBytes": sum(int(item["guestBytes"]) for item in ordinary),
        "hostBytes": sum(int(item["hostBytes"]) for item in ordinary),
        "pages": sum(int(item["pages"]) for item in ordinary),
        "requiredBlobBytes": sum(int(item["requiredBytes"]) for item in ordinary),
        "storedBlobBytes": sum(int(item["storedBytes"]) for item in ordinary),
        "trailingBytes": sum(int(item["trailingBytes"]) for item in ordinary),
    }
    topology = _data_topology(data, referenced_extents)
    return {
        "format": "teamleaderleo-fex-disk-cache-shape-v2",
        "dataFile": {
            "bytes": len(data),
            "name": data_path.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "version": data_version,
        },
        "dataTopology": topology,
        "indexFile": {
            "bytes": len(index),
            "name": index_path.name,
            "sha256": hashlib.sha256(index).hexdigest(),
            "version": index_version,
        },
        "indexRecordCount": record_count,
        "metadataRecordCount": metadata_count,
        "minimalPayloads": all(item["trailingBytes"] == 0 for item in ordinary),
        "ordinaryRecordCount": len(ordinary),
        "records": ordinary,
        "totals": totals,
    }


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("base", type=Path, help="cache base path, without .foz")
    result.add_argument("--max-file-bytes", type=positive_int, default=DEFAULT_MAX_FILE_BYTES)
    result.add_argument("--max-records", type=positive_int, default=DEFAULT_MAX_RECORDS)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = analyze(
            args.base,
            max_file_bytes=args.max_file_bytes,
            max_records=args.max_records,
        )
    except CacheFormatError as exc:
        print(f"AnalyzeDiskCache: {exc}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
