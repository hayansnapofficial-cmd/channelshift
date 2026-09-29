"""Package original source, Python wheel and standalone skill, never user state."""
import argparse
import ast
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?")
_PRIVATE_PARTS = {".git", ".venv", ".channelshift", ".codex", "__pycache__", "credentials", "members"}
_PRIVATE_NAMES = {"auth.json", "smtp.json", "gmail-app-password.txt", "apify-token.txt", "channelshift-mcp-token.txt"}
_PRIVATE_SUFFIXES = (".pyc", ".key", ".pem", ".p12", ".pfx", ".dpapi", ".sqlite", ".sqlite3", ".db", ".log")


def read_version(root=ROOT):
    """Read the static project metadata using only the Python 3.10 standard library."""
    content = (root / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\][ \t]*\r?\n(.*?)(?=^\[|\Z)", content)
    versions = re.findall(r'^version\s*=\s*[\"\']([^\"\']+)[\"\']\s*(?:#.*)?$',
                          project.group(1) if project else "", re.MULTILINE)
    if len(versions) != 1 or not _VERSION.fullmatch(versions[0]):
        raise ValueError("A static release version is required in pyproject.toml")
    module = ast.parse((root / "src/channelshift/__init__.py").read_text(encoding="utf-8"))
    runtime_versions = [ast.literal_eval(node.value) for node in module.body
                        if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)]
    if runtime_versions != versions:
        raise ValueError("Package metadata and runtime versions differ")
    return versions[0]


def audit_member(name, *, linked=False, header=b""):
    """Reject private state and archive links before a distribution is published."""
    path = PurePosixPath(name)
    parts = tuple(part.lower() for part in path.parts)
    basename = parts[-1] if parts else ""
    if (linked or not name or "\\" in name or ":" in name or path.is_absolute()
            or any(part in {".", ".."} for part in name.split("/"))
            or any(part in _PRIVATE_PARTS for part in parts)
            or basename in _PRIVATE_NAMES or basename == ".env" or basename.startswith(".env.")
            or basename.endswith(_PRIVATE_SUFFIXES)
            or any(marker in basename for marker in (".sqlite-", ".sqlite3-", ".db-"))
            or header.startswith(b"SQLite format 3\x00")):
        raise ValueError("Release contains a forbidden path, link, or private database")


def audit_archive(path):
    names = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                header = b""
                if not member.is_dir():
                    with archive.open(member) as source:
                        header = source.read(16)
                audit_member(member.filename.rstrip("/"), linked=stat.S_ISLNK(member.external_attr >> 16), header=header)
                names.append(member.filename)
    else:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                source = archive.extractfile(member) if member.isfile() else None
                header = source.read(16) if source else b""
                if source:
                    source.close()
                audit_member(member.name.rstrip("/"), linked=not (member.isfile() or member.isdir()), header=header)
                names.append(member.name)
    if len(set(names)) != len(names):
        raise ValueError("Release contains duplicate paths")
    if path.suffix == ".whl":
        expected = {"channelshift/web/" + name for name in (
            "studio.html", "studio.css", "studio.js", "studio-dom.js", "studio-account.js", "studio-policy.js")}
        expected.update("channelshift/" + name for name in (
            "pipeline_workspace.py", "pipeline_artifacts.py", "pipeline_contracts.py", "pipeline_traceability.py",
            "pipeline_preview.py", "site_obligations.py", "member_web.py"))
        if not expected.issubset(names):
            raise ValueError("Release is missing required Studio files")
    return len(names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="store_true", help="Print the checked package version without building")
    args = parser.parse_args()
    version = read_version()
    if args.version:
        print(version)
        return
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    artifacts = []
    with tempfile.TemporaryDirectory(prefix="channelshift-build-") as output:
        built = subprocess.run([sys.executable, "-X", "utf8", "-m", "build", "--no-isolation", "--outdir", output], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                               env=dict(os.environ, PYTHONUTF8="1"))
        if built.returncode:
            print(built.stdout[-12000:], file=sys.stderr)
            raise SystemExit(built.returncode)
        for name in (f"channelshift-{version}-py3-none-any.whl", f"channelshift-{version}.tar.gz"):
            source = Path(output) / name
            audit_archive(source)
            shutil.copy2(source, release / name)
            artifacts.append(release / name)
    with tempfile.TemporaryDirectory(prefix="channelshift-skill-release-") as temporary:
        skill = Path(temporary) / "channelshift"
        shutil.copytree(ROOT / "skills/channelshift", skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (skill / "assets").mkdir(exist_ok=True)
        shutil.copy2(artifacts[0], skill / "assets" / artifacts[0].name)
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(ROOT / name, skill / name)
        target = release / f"channelshift-skill-{version}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(skill.rglob("*")):
                if path.is_symlink():
                    raise RuntimeError("Release links are not allowed")
                if path.is_file():
                    archive.write(path, path.relative_to(Path(temporary)).as_posix())
        audit_archive(target)
        artifacts.append(target)
    checksums = "".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n" for path in artifacts)
    (release / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
    print(checksums)


if __name__ == "__main__":
    main()
