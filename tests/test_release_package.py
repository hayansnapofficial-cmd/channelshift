"""Release metadata and archives must exclude state while retaining the Studio."""
import importlib.util
import io
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("channelshift_build_release", ROOT / "scripts/build-release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleasePackageTests(unittest.TestCase):
    def test_project_and_runtime_versions_agree(self):
        self.assertEqual(release.read_version(), "0.2.0")

    def test_version_comes_from_project_metadata_and_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src/channelshift").mkdir(parents=True)
            (root / "pyproject.toml").write_text('[project]\nname = "channelshift"\nversion = "1.3.5rc2"\n\n[project.scripts]\n', encoding="utf-8")
            runtime = root / "src/channelshift/__init__.py"
            runtime.write_text('__version__ = "1.3.5rc2"\n', encoding="utf-8")
            self.assertEqual(release.read_version(root), "1.3.5rc2")
            runtime.write_text('__version__ = "0.1.0"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "versions differ"):
                release.read_version(root)

    def test_archive_private_paths_and_sqlite_headers_are_rejected(self):
        for name in ("../../auth.json", "/etc/passwd", "C:/private.txt", "docs\\secret.txt",
                     "docs/.ENV.production", "docs/credentials/backup.txt", "docs/members/example.json",
                     "docs/auth.json", "docs/smtp.json", "docs/state.sqlite3", "docs/state.db-wal",
                     "docs/session.dpapi", "docs/apify-token.txt", "docs/.codex/history.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                release.audit_member(name)
        with self.assertRaises(ValueError):
            release.audit_member("docs/innocent.txt", header=b"SQLite format 3\x00")
        release.audit_member("channelshift/member_auth.py")
        release.audit_member("docs/service-dependency-licenses/library.dist-info/licenses/LICENSE")

    def test_zip_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "skill.zip"
            info = zipfile.ZipInfo("channelshift/linked.txt")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(info, "../../../private")
            with self.assertRaises(ValueError):
                release.audit_archive(path)

    def test_tar_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.tar.gz"
            info = tarfile.TarInfo("channelshift/docs/linked.txt")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../../private"
            with tarfile.open(path, "w:gz") as archive:
                archive.addfile(info)
            with self.assertRaises(ValueError):
                release.audit_archive(path)

    def test_duplicate_zip_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "skill.zip"
            with zipfile.ZipFile(path, "w") as archive, warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("channelshift/SKILL.md", "first")
                archive.writestr("channelshift/SKILL.md", "second")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                release.audit_archive(path)

    def test_wheel_without_studio_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "channelshift.whl"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("channelshift/__init__.py", "")
            with self.assertRaisesRegex(ValueError, "required Studio"):
                release.audit_archive(path)

    def test_regular_source_archive_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.tar.gz"
            content = b"# Source documentation\n"
            info = tarfile.TarInfo("channelshift/docs/README.md")
            info.size = len(content)
            with tarfile.open(path, "w:gz") as archive:
                archive.addfile(info, io.BytesIO(content))
            self.assertEqual(release.audit_archive(path), 1)


if __name__ == "__main__":
    unittest.main()
