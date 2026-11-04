"""Bounded inspection of Android boot v0-v4 and vendor_boot v3-v4 images.

Layout reference: AOSP system/tools/mkbootimg/include/bootimg/bootimg.h
https://android.googlesource.com/platform/system/tools/mkbootimg/+/refs/heads/main/include/bootimg/bootimg.h

Structural inspection does not verify signatures, authenticity, or suitability
for a device. Trailing partition padding and AVB metadata are hashed but are not
interpreted. No image contents are executed or extracted.
"""
import hashlib
import os
import stat
import struct
from pathlib import Path
from typing import BinaryIO


def _read_at(stream: BinaryIO, offset: int, size: int) -> bytes:
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"Truncated image header or table at offset {offset}")
    return data


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _align(size: int, page: int) -> int:
    return ((size + page - 1) // page) * page


def _page_size(page: int) -> None:
    if page < 512 or page & (page - 1):
        raise ValueError(f"Invalid page size: {page}")


def _section(facts: dict, name: str, offset: int, size: int) -> int:
    """Validate section data AND its specified page padding before recording it."""
    end = offset + _align(size, facts["page_size"])
    if end > facts["size_bytes"]:
        raise ValueError(f"Truncated {name} section: requires {end} bytes")
    facts["sections"].append({"name": name, "offset": offset, "size": size})
    return end


def _boot(stream: BinaryIO, size: int) -> dict:
    prefix = _read_at(stream, 0, 44)
    version = _u32(prefix, 40)
    header_sizes = (1632, 1648, 1660, 1580, 1584)
    if version not in range(len(header_sizes)):
        raise ValueError(f"Unsupported boot header version: {version}")
    header_size = header_sizes[version]
    header = _read_at(stream, 0, header_size)
    page = _u32(header, 36) if version < 3 else 4096
    _page_size(page)
    if page < header_size:
        raise ValueError("Boot header does not fit its page")
    if version in (1, 2) and _u32(header, 1644) != header_size:
        raise ValueError("Invalid boot header_size")
    if version >= 3 and _u32(header, 20) != header_size:
        raise ValueError("Invalid boot header_size")
    kernel = _u32(header, 8)
    ramdisk = _u32(header, 16 if version < 3 else 12)
    if not kernel and (version != 4 or not ramdisk):
        raise ValueError("Missing kernel payload (only v4 init_boot may omit it)")
    facts = {"kind": "boot", "header_version": version,
             "header_size": header_size, "page_size": page,
             "kernel_size": kernel, "ramdisk_size": ramdisk,
             "size_bytes": size, "sections": []}
    offset = _section(facts, "header", 0, header_size)
    offset = _section(facts, "kernel", offset, kernel)
    offset = _section(facts, "ramdisk", offset, ramdisk)
    if version < 3:
        facts["second_size"] = _u32(header, 24)
        offset = _section(facts, "second", offset, facts["second_size"])
        if version >= 1:
            dtbo_size = _u32(header, 1632)
            dtbo_offset = struct.unpack_from("<Q", header, 1636)[0]
            if dtbo_size and dtbo_offset != offset:
                raise ValueError("Invalid recovery DTBO offset or overlapping section")
            facts["recovery_dtbo_size"] = dtbo_size
            offset = _section(facts, "recovery_dtbo", offset, dtbo_size)
        if version == 2:
            facts["dtb_size"] = _u32(header, 1648)
            _section(facts, "dtb", offset, facts["dtb_size"])
    elif version == 4:
        facts["signature_size"] = _u32(header, 1580)
        _section(facts, "boot_signature", offset, facts["signature_size"])
    return facts


def _vendor_boot(stream: BinaryIO, size: int) -> dict:
    prefix = _read_at(stream, 0, 12)
    version = _u32(prefix, 8)
    if version not in (3, 4):
        raise ValueError(f"Unsupported vendor_boot header version: {version}")
    header_size = 2112 if version == 3 else 2128
    header = _read_at(stream, 0, header_size)
    page = _u32(header, 12)
    _page_size(page)
    if _u32(header, 2096) != header_size:
        raise ValueError("Invalid vendor_boot header_size")
    ramdisk = _u32(header, 24)
    dtb = _u32(header, 2100)
    if not ramdisk and not dtb:
        raise ValueError("Missing vendor_boot payload")
    facts = {"kind": "vendor_boot", "header_version": version,
             "header_size": header_size, "page_size": page,
             "kernel_size": 0, "ramdisk_size": ramdisk, "dtb_size": dtb,
             "size_bytes": size, "sections": []}
    offset = _section(facts, "header", 0, header_size)
    offset = _section(facts, "vendor_ramdisk", offset, ramdisk)
    offset = _section(facts, "dtb", offset, dtb)
    if version == 4:
        table_size, entry_count, entry_size, bootconfig = struct.unpack_from(
            "<IIII", header, 2112)
        if table_size != entry_count * entry_size:
            raise ValueError("Vendor ramdisk table size does not match entry count")
        if entry_count and entry_size != 108:
            raise ValueError("Invalid vendor ramdisk table entry size")
        if ramdisk and not entry_count:
            raise ValueError("Missing vendor ramdisk table entries")
        table_offset = offset
        offset = _section(facts, "vendor_ramdisk_table", offset, table_size)
        _section(facts, "bootconfig", offset, bootconfig)
        # Each fixed-size entry is read separately; no attacker-controlled
        # table length is allocated. Bounds also cap the number of iterations.
        for index in range(entry_count):
            entry = _read_at(stream, table_offset + index * entry_size, 108)
            fragment_size, fragment_offset = struct.unpack_from("<II", entry)
            if fragment_offset + fragment_size > ramdisk:
                raise ValueError("Vendor ramdisk table entry exceeds ramdisk section")
        facts.update(vendor_ramdisk_table_size=table_size,
                     vendor_ramdisk_table_entry_num=entry_count,
                     vendor_ramdisk_table_entry_size=entry_size,
                     bootconfig_size=bootconfig)
    return facts


def inspect_image(path: Path) -> dict:
    """Return JSON-serializable header facts and a SHA-256 of the complete file.

    Unknown, unsupported, malformed and truncated images raise ValueError.
    Filesystem errors remain OSError. Only regular files are accepted.
    """
    with Path(path).open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Image must be a regular file")
        magic = _read_at(stream, 0, 8)
        if magic == b"ANDROID!":
            facts = _boot(stream, before.st_size)
        elif magic == b"VNDRBOOT":
            facts = _vendor_boot(stream, before.st_size)
        else:
            raise ValueError("Unrecognized Android boot image magic")
        stream.seek(0)
        digest = hashlib.sha256()
        hashed_size = 0
        while chunk := stream.read(1024 * 1024):
            hashed_size += len(chunk)
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if (hashed_size != before.st_size or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns):
            raise ValueError("Image changed during inspection")
    facts["sha256"] = digest.hexdigest()
    facts["verification"] = {"structure": "valid", "authenticity": "not_verified",
                             "compatibility": "not_verified"}
    return facts
