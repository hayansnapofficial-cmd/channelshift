"""Open the independent studio after its loopback service is ready."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

URL = "http://127.0.0.1:5187/"


def ready():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(URL + "health", timeout=1) as response:
            return json.load(response).get("service") == "channelshift-independent"
    except (OSError, ValueError):
        return False


def main():
    if not ready():
        child = subprocess.Popen([sys.executable, "-m", "channelshift.web"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(60):
            if ready():
                break
            if child.poll() is not None:
                raise RuntimeError("The studio could not start. Check whether port 5187 is already occupied.")
            time.sleep(0.15)
        else:
            raise RuntimeError("The studio did not become ready.")
    if os.name == "nt":
        edge = Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe"
        if edge.is_file():
            profile = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ChannelShift/browser-profile"
            subprocess.Popen([str(edge), "--app=" + URL, "--user-data-dir=" + str(profile), "--no-first-run"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
