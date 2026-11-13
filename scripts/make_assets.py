from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont

from ascii_studio.demo import demo_frame
from ascii_studio.engine import NO_WINDOW, ffmpeg_path


def main():
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    image = Image.new("RGBA", (256, 256), (5, 5, 6, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, 250, 250), radius=50, outline="#6d5934", width=4)
    font = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 138)
    draw.text((49, 28), "A", font=font, fill="#dbb86c")
    draw.line((64, 210, 190, 210), fill="#dbb86c", width=5)
    image.save(assets / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    image.save(assets / "icon.png")
    demo = assets / "demo.mp4"
    if not demo.exists() or "--force" in sys.argv:
        process = subprocess.Popen([
            ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "960x600", "-r", "24", "-i", "pipe:0",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100:duration=6",
            "-map", "0:v:0", "-map", "1:a:0", "-af", "volume=0.025,afade=t=in:d=0.5,afade=t=out:st=5:d=1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(demo),
        ], stdin=subprocess.PIPE, creationflags=NO_WINDOW)
        try:
            for i in range(144):
                process.stdin.write(demo_frame(i / 24).tobytes())
            process.stdin.close()
            if process.wait(timeout=90):
                raise RuntimeError("Demo encoding failed")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    print("Assets ready:", assets)


if __name__ == "__main__":
    main()
