"""Focused SDK extraction regressions; no network, phone, or installed SDK."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from android_sdk_tools import REQUIRED_FILES, install_archive


class AndroidSdkExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "用户 & name" / "Android/Sdk/cmdline-tools/19.0"

    def bundle(self, entries):
        archive = self.root / "tools.zip"
        with zipfile.ZipFile(archive, "w") as z:
            for name, content in entries.items():
                z.writestr(name, content)
        return archive, hashlib.sha1(archive.read_bytes()).hexdigest()

    def valid_entries(self):
        return {"cmdline-tools/" + name: b"fixture" for name in REQUIRED_FILES}

    def test_deep_paths_and_implicit_directories_extract_all_files(self):
        # This mimics Google's longest Java dependency path, well over 260
        # characters after prefixing the old staging directory.
        long_name = ("cmdline-tools/lib/external/com/google/guava/listenablefuture/"
                     "9999.0-empty-to-avoid-conflict-with-guava/"
                     "listenablefuture-9999.0-empty-to-avoid-conflict-with-guava.jar")
        entries = self.valid_entries()
        entries[long_name] = b"complete jar bytes"
        self.destination = self.root / ("long-user-" * 10) / self.destination.relative_to(self.root)
        self.assertGreater(len(str(self.destination / long_name)), 260)
        archive, digest = self.bundle(entries)  # no explicit directory entries
        result = install_archive(archive, self.destination, digest)
        for name, content in entries.items():
            self.assertEqual((result / name.removeprefix("cmdline-tools/")).read_bytes(), content)

    def test_retry_replaces_partial_installation(self):
        self.destination.mkdir(parents=True)
        (self.destination / "old-partial").write_text("partial")
        archive, digest = self.bundle(self.valid_entries())
        install_archive(archive, self.destination, digest)
        self.assertFalse((self.destination / "old-partial").exists())
        self.assertTrue((self.destination / "bin/sdkmanager.bat").is_file())

    def test_missing_library_does_not_replace_previous_installation(self):
        self.destination.mkdir(parents=True)
        marker = self.destination / "keep-me"
        marker.write_text("previous")
        entries = self.valid_entries()
        del entries["cmdline-tools/lib/sdkmanager-classpath.jar"]
        archive, digest = self.bundle(entries)
        with self.assertRaisesRegex(ValueError, "layout"):
            install_archive(archive, self.destination, digest)
        self.assertEqual(marker.read_text(), "previous")

    def test_download_checksum_mismatch_creates_no_installation(self):
        archive, _ = self.bundle(self.valid_entries())
        with self.assertRaisesRegex(ValueError, "checksum"):
            install_archive(archive, self.destination, "0" * 40)
        self.assertFalse(self.destination.exists())

    def test_archive_path_cannot_escape_staging(self):
        entries = self.valid_entries()
        entries["../outside.txt"] = b"bad"
        archive, digest = self.bundle(entries)
        with self.assertRaisesRegex(ValueError, "Invalid path"):
            install_archive(archive, self.destination, digest)
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.destination.parent / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()
