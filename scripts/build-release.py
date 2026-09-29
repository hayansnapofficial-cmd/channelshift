"""Package original source, Python wheel and standalone skill, never user state."""
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"


def main():
    built = subprocess.run([sys.executable, "-m", "build", "--no-isolation"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if built.returncode:
        print(built.stdout[-12000:], file=sys.stderr)
        raise SystemExit(built.returncode)
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    artifacts = []
    for name in (f"channelshift-{VERSION}-py3-none-any.whl", f"channelshift-{VERSION}.tar.gz"):
        source = ROOT / "dist" / name
        shutil.copy2(source, release / name)
        artifacts.append(release / name)
    with tempfile.TemporaryDirectory(prefix="channelshift-skill-release-") as temporary:
        skill = Path(temporary) / "channelshift"
        shutil.copytree(ROOT / "skills/channelshift", skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (skill / "assets").mkdir(exist_ok=True)
        shutil.copy2(artifacts[0], skill / "assets" / artifacts[0].name)
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(ROOT / name, skill / name)
        target = release / f"channelshift-skill-{VERSION}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(skill.rglob("*")):
                if path.is_symlink():
                    raise RuntimeError("Release links are not allowed")
                if path.is_file():
                    archive.write(path, path.relative_to(Path(temporary)).as_posix())
        artifacts.append(target)
    checksums = "".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n" for path in artifacts)
    (release / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
    print(checksums)


if __name__ == "__main__":
    main()
