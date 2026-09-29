"""Install the independent package into its own environment; preserve existing installs."""
import os
import subprocess
import sys
import venv
from pathlib import Path


def main():
    skill = Path(__file__).resolve().parents[1]
    wheels = list((skill / "assets").glob("channelshift-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Use the released skill ZIP containing one ChannelShift wheel.")
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "ChannelShiftIndependent"
    target = base / "venv"
    if target.exists() or target.is_symlink():
        raise SystemExit("An environment already exists; preserve it or choose a separate manual installation. Nothing was replaced.")
    venv.create(target, with_pip=True)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", str(wheels[0])], check=True)
    print("Installed. MCP command:")
    print(str(target / ("Scripts/channelshift-mcp.exe" if os.name == "nt" else "bin/channelshift-mcp")))
    print("Studio command:")
    print(str(target / ("Scripts/channelshift-studio.exe" if os.name == "nt" else "bin/channelshift-studio")))


if __name__ == "__main__":
    main()
