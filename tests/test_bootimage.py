"""Synthetic Android headers with independently specified on-disk offsets."""
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

try:
    from bootimage import inspect_image
except ImportError:
    inspect_image = None


def boot_fixture(version=4, kernel=b"kernel", ramdisk=b"ramdisk"):
    page = 2048 if version < 3 else 4096
    header_sizes = (1632, 1648, 1660, 1580, 1584)
    header = bytearray(page)
    header[:8] = b"ANDROID!"
    struct.pack_into("<I", header, 40, version)
    if version < 3:
        struct.pack_into("<I", header, 8, len(kernel))
        struct.pack_into("<I", header, 16, len(ramdisk))
        struct.pack_into("<I", header, 36, page)
        if version >= 1:
            struct.pack_into("<I", header, 1644, header_sizes[version])
        if version == 2:
            struct.pack_into("<I", header, 1648, 3)
    else:
        struct.pack_into("<IIII", header, 8,
                         len(kernel), len(ramdisk), 0, header_sizes[version])
    data = bytes(header)
    for part in (kernel, ramdisk, b"dtb" if version == 2 else b""):
        if part:
            data += part + bytes((-len(part)) % page)
    return data


def vendor_fixture(version=4, page=4096):
    header = bytearray(((2128 + page - 1) // page) * page)
    header[:8] = b"VNDRBOOT"
    struct.pack_into("<II", header, 8, version, page)
    struct.pack_into("<I", header, 24, 7)
    struct.pack_into("<II", header, 2096, 2112 if version == 3 else 2128, 3)
    parts = [b"ramdisk", b"dtb"]
    if version == 4:
        struct.pack_into("<IIII", header, 2112, 108, 1, 108, 4)
        table = bytearray(108)
        struct.pack_into("<III", table, 0, 7, 0, 1)
        parts += [bytes(table), b"a=b\n"]
    return bytes(header) + b"".join(p + bytes((-len(p)) % page) for p in parts)


class TestInspectImage(unittest.TestCase):
    def inspect(self, data):
        self.assertIsNotNone(inspect_image, "bootimage.inspect_image is missing")
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "image.img"
            image.write_bytes(data)
            return inspect_image(image)

    def test_boot_versions_have_correct_sizes_and_hash(self):
        for version in range(5):
            with self.subTest(version=version):
                data = boot_fixture(version)
                facts = self.inspect(data)
                self.assertEqual(facts["kind"], "boot")
                self.assertEqual(facts["header_version"], version)
                self.assertEqual(facts["kernel_size"], 6)
                self.assertEqual(facts["ramdisk_size"], 7)
                self.assertEqual(facts["size_bytes"], len(data))
                self.assertEqual(facts["sha256"], hashlib.sha256(data).hexdigest())
                json.dumps(facts)

    def test_vendor_versions_and_two_page_header(self):
        for version, page in ((3, 4096), (4, 4096), (4, 2048)):
            with self.subTest(version=version, page=page):
                facts = self.inspect(vendor_fixture(version, page))
                self.assertEqual(facts["kind"], "vendor_boot")
                self.assertEqual(facts["header_version"], version)
                self.assertEqual(facts["kernel_size"], 0)
                self.assertEqual(facts["ramdisk_size"], 7)
                self.assertEqual(facts["dtb_size"], 3)
                self.assertEqual(facts["page_size"], page)

    def test_init_boot_and_kernel_only_modern_images(self):
        facts = self.inspect(boot_fixture(4, kernel=b""))
        self.assertEqual((facts["kernel_size"], facts["ramdisk_size"]), (0, 7))
        facts = self.inspect(boot_fixture(4, ramdisk=b""))
        self.assertEqual((facts["kernel_size"], facts["ramdisk_size"]), (6, 0))

    def test_header_facts_never_claim_authenticity(self):
        facts = self.inspect(boot_fixture())
        self.assertEqual(facts["verification"]["authenticity"], "not_verified")
        self.assertEqual(facts["verification"]["compatibility"], "not_verified")

    def test_unknown_and_truncated_headers_rejected(self):
        for data in (b"", b"not Android", b"ANDROID!", boot_fixture()[:100],
                     vendor_fixture()[:2111]):
            with self.subTest(size=len(data)):
                with self.assertRaises(ValueError):
                    self.inspect(data)

    def test_unsupported_header_versions_rejected(self):
        for data, offset in ((boot_fixture(), 40), (vendor_fixture(), 8)):
            damaged = bytearray(data)
            struct.pack_into("<I", damaged, offset, 9)
            with self.assertRaises(ValueError):
                self.inspect(damaged)

    def test_every_truncated_section_rejected(self):
        for data, page in [(boot_fixture(v), 2048 if v < 3 else 4096)
                           for v in range(5)] + [(vendor_fixture(), 4096)]:
            for cut in range(page, len(data) + 1, page):
                with self.subTest(size=len(data), cut=cut):
                    with self.assertRaises(ValueError):
                        self.inspect(data[:cut - 1])

    def test_invalid_page_and_header_sizes_rejected(self):
        cases = [(boot_fixture(0), 36, n) for n in (0, 1, 1000, 1024)]
        cases += [(vendor_fixture(), 12, n) for n in (0, 1, 3000)]
        cases += [(boot_fixture(1), 1644, 1632), (boot_fixture(), 20, 4096),
                  (vendor_fixture(), 2096, 2112)]
        for data, offset, value in cases:
            damaged = bytearray(data)
            struct.pack_into("<I", damaged, offset, value)
            with self.subTest(offset=offset, value=value):
                with self.assertRaises(ValueError):
                    self.inspect(damaged)

    def test_absent_payload_rejected(self):
        for version in range(5):
            with self.assertRaises(ValueError):
                self.inspect(boot_fixture(version, kernel=b"", ramdisk=b""))

    def test_declared_huge_kernel_rejected(self):
        damaged = bytearray(boot_fixture())
        struct.pack_into("<I", damaged, 8, 0xffffffff)
        with self.assertRaises(ValueError):
            self.inspect(damaged)

    def test_recovery_dtbo_bounds_and_offset(self):
        data = bytearray(boot_fixture(1))
        struct.pack_into("<IQ", data, 1632, 3, 6144)
        data += b"dtb" + bytes(2045)
        self.assertEqual(self.inspect(data)["recovery_dtbo_size"], 3)
        for offset in (0, 2048, 6145, 2**63):
            struct.pack_into("<Q", data, 1636, offset)
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError):
                    self.inspect(data)

    def test_boot_signature_declared_size_checked(self):
        data = bytearray(boot_fixture())
        struct.pack_into("<I", data, 1580, 4)
        with self.assertRaises(ValueError):
            self.inspect(data)
        data += b"sign" + bytes(4092)
        self.assertEqual(self.inspect(data)["signature_size"], 4)

    def test_vendor_table_shape_and_ramdisk_bounds_checked(self):
        for offset, value in ((2112, 107), (2116, 2), (2120, 4),
                              (12288, 8), (12292, 7)):
            data = bytearray(vendor_fixture())
            struct.pack_into("<I", data, offset, value)
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError):
                    self.inspect(data)

    def test_hash_includes_large_trailing_partition_data(self):
        data = boot_fixture() + b"trailing" * 300000
        facts = self.inspect(data)
        self.assertEqual(facts["sha256"], hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    unittest.main()
