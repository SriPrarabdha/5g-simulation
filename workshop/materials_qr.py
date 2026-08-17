#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import qrcode
import qrcode.image.svg


def write_materials_link(url: str, output: Path) -> tuple[Path, Path]:
    if not url.startswith(("http://", "https://")):
        raise ValueError("materials URL must use http or https")
    output.parent.mkdir(parents=True, exist_ok=True)
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=3)
    image.save(output)
    text_path = output.with_suffix(".txt")
    text_path.write_text(url + "\n", encoding="utf-8")
    return output, text_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the participant workshop URL as QR SVG and text.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path in write_materials_link(args.url, args.output):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
