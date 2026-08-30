import importlib.util
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "build_release.py"
SPEC = importlib.util.spec_from_file_location("build_release", SCRIPT_PATH)
RELEASE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(RELEASE)


class ReleaseBuildTests(unittest.TestCase):
    def test_version_matches_skill_metadata(self):
        self.assertEqual(RELEASE.skill_version(), "0.3.0")
        self.assertEqual(
            RELEASE.archive_name(RELEASE.skill_version()),
            "philosophical-guide-roadmap-v0.3.0.zip",
        )

    def test_archive_is_reproducible_and_matches_source(self):
        files = RELEASE.package_files()
        with tempfile.TemporaryDirectory() as temp_name:
            first = Path(temp_name) / "first.zip"
            second = Path(temp_name) / "second.zip"
            RELEASE.write_archive(first, files)
            RELEASE.write_archive(second, files)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            report = RELEASE.verify_archive(first, files)
            self.assertTrue(report["source_match"])
            self.assertEqual(report["members"], len(RELEASE.REQUIRED_FILES))

    def test_duplicate_archive_member_is_rejected(self):
        files = RELEASE.package_files()
        with tempfile.TemporaryDirectory() as temp_name:
            archive_path = Path(temp_name) / "duplicate.zip"
            RELEASE.write_archive(archive_path, files)
            member = "philosophical-guide/SKILL.md"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive_path, "a") as archive:
                    archive.writestr(member, b"tampered")
            with self.assertRaisesRegex(RELEASE.ReleaseError, "duplicate member"):
                RELEASE.verify_archive(archive_path, files)

    def test_public_archive_name_allows_unicode_but_rejects_directories(self):
        self.assertEqual(RELEASE.validate_archive_filename("哲思引导者skill.zip"), "哲思引导者skill.zip")
        with self.assertRaisesRegex(RELEASE.ReleaseError, "one safe .zip filename"):
            RELEASE.validate_archive_filename("../unsafe.zip")


if __name__ == "__main__":
    unittest.main()
