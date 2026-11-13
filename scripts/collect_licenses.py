from __future__ import annotations

import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ascii_studio.engine import NO_WINDOW, ffmpeg_path


def main():
    release = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "ASCII Studio"
    destination = release / "licenses"
    destination.mkdir(parents=True, exist_ok=True)
    for package in ("PySide6-Essentials", "shiboken6", "numpy", "Pillow", "opencv-python-headless", "imageio-ffmpeg", "pyinstaller"):
        dist = importlib.metadata.distribution(package)
        folder = destination / package
        folder.mkdir(exist_ok=True)
        for path in dist.files or []:
            name = path.name.lower()
            if "license" in name or "copying" in name or "copyright" in name or name == "notice":
                source = Path(dist.locate_file(path))
                if source.is_file():
                    target = folder / str(path).replace("/", "_").replace("\\", "_")
                    shutil.copy2(source, target)
        (folder / "METADATA.txt").write_text(dist.read_text("METADATA") or package, encoding="utf-8")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, destination / "Python-LICENSE.txt")
    ffmpeg_license = subprocess.run([ffmpeg_path(), "-L"], capture_output=True, creationflags=NO_WINDOW, check=True)
    (destination / "FFmpeg-LICENSE.txt").write_bytes(ffmpeg_license.stdout + ffmpeg_license.stderr)
    print("Third-party notices:", destination)


if __name__ == "__main__":
    main()
