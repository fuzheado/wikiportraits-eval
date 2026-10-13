#!/usr/bin/env python3
"""Offline test of pdfmin's image support: a JPEG and a PNG from Commons, embedded and rendered.

Renders the produced PDF back to PNG with pypdfium2 (dev-only dependency in .venv-pdf) so
the result can be eyeballed, and asserts the PDF really carries the image objects.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiclient  # noqa: E402
from pdfmin import PDF, _jpeg_info, _png_info, _png_to_rgb  # noqa: E402

CASES = [
    ("File:Sundance Film Festival 2024 - Porcelain War - Andrey Stefanov-104A2045.jpg",
     "commons.wikimedia.org", "JPEG portrait"),
    ("File:Wikimedia Movement logo Arabic vertical in white.svg",
     "commons.wikimedia.org", "PNG from SVG (alpha)"),
]

meta = wiclient.resolve([{"title": t, "wiki": w} for t, w, _ in CASES])
doc = PDF(title="pdfmin image test")
doc.h1("Image embedding test")
doc.small("JPEG via /DCTDecode (original bytes) and PNG decoded to raw RGB via /FlateDecode.")

for title, wiki, label in CASES:
    m = meta.get((wiki, title)) or {}
    if not m.get("ok"):
        print("resolve failed:", title, m.get("error"))
        continue
    data = wiclient.fetch_bytes(m["thumburl"])
    print(f"{label:22s} {len(data):>7d} bytes  head={data[:4]!r}  "
          f"jpeg={_jpeg_info(data)}  png={_png_info(data)}")
    name = doc.add_image(data, label=title)
    doc.h2(title.replace("File:", ""))
    doc.card([("h", f"embedded as {name}"),
              f"served {m.get('thumburl', '').rsplit('/', 1)[-1]}",
              "composite 0.763  ·  P(publish) 0.877  ·  infobox 0.676",
              "This line is deliberately long so that wrapping inside the right-hand column can be "
              "checked against the image box."], name=name)

print("skipped:", doc.skipped)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "pdfmin_image_test.pdf")
doc.save(out)
raw = open(out, "rb").read()
print(f"pdf: {len(raw)} bytes  /DCTDecode={raw.count(b'/DCTDecode')}  "
      f"/FlateDecode={raw.count(b'/FlateDecode')}  /XObject={raw.count(b'/XObject')}  "
      f"pages={raw.count(b'/Type /Page ')}")

import pypdfium2 as pdfium  # noqa: E402  (dev-only)

pdf = pdfium.PdfDocument(out)
print("pdfium pages:", len(pdf))
page = pdf[0]
page.render(scale=1.6).to_pil().save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "pdfmin_image_test.png"))
print("rendered", len(pdf), "page(s) to pdfmin_image_test.png")
