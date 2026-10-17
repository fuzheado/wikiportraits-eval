#!/usr/bin/env python3
"""Build the EXIF test image used to verify that an upload is upright and metadata-free.

Why this exists (HANDOFF §1, *Local images*): the browser resizes an uploaded image with a canvas, and
two of the things that makes it do are **invisible when they go wrong**.

  * **Rotation.** A phone photo is almost always stored landscape with an EXIF orientation flag saying
    how to turn it for display. A canvas that ignores the flag produces a *sideways* photo, and every
    answer about that photo is wrong in a way that still looks like a plausible answer. There is no
    error, no crash, and nothing on screen to suggest it.
  * **Metadata.** Re-encoding is what strips the EXIF block, so GPS coordinates, timestamps and camera
    serials never leave the machine. If the re-encode were ever replaced by "send the file as it is",
    the images would still score and the privacy claim would be quietly false.

So the fixture is made to fail loudly in both directions: it is **stored 4000×3000 landscape** with
`orientation=6` (i.e. it *displays* as a 3000×4000 portrait) and it carries a **GPS IFD** for
51°30′N 0°7′W. Verified 2026-10-16 through the real page: read as 3000×4000, uploaded 960×1280 / 25 KB,
and the stored bytes had no Exif block, no orientation tag and no GPS IFD.

    .venv-pdf/bin/python tools/make_exif_fixture.py [out.jpg]     # needs pillow (test-only dep)

Then, on a running app:  open the page, add the file, and check the stored bytes
(`curl .../api/upload/<id> -o /tmp/x.jpg`) with a real EXIF reader rather than a byte search — the GPS
pointer is a numeric tag (`0x8825`), so `b"GPS" in bytes` is *false even when GPS is present*. That
mistake was made once while building this fixture; use `Image.open(p).getexif().get_ifd(34853)`.
"""
import sys

try:
    from PIL import Image, ImageDraw
    from PIL.TiffImagePlugin import IFDRational
except ImportError:
    sys.exit("needs pillow — run with the test-only venv: .venv-pdf/bin/python tools/make_exif_fixture.py")

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wikiportraits-exif-fixture.jpg"
W, H = 4000, 3000

im = Image.new("RGB", (W, H), (40, 90, 160))
d = ImageDraw.Draw(im)
# Asymmetric marks, so a wrong rotation is visible rather than merely suspected.
d.rectangle([0, 0, W - 1, H - 1], outline=(250, 250, 250), width=24)
d.ellipse([W * 0.35, H * 0.2, W * 0.65, H * 0.8], fill=(230, 170, 140))      # "head"
d.rectangle([W * 0.1, H * 0.05, W * 0.25, H * 0.25], fill=(240, 220, 40))    # a corner marker
d.rectangle([0, H - 120, W * 0.5, H], fill=(20, 20, 20))                     # a "shoulder" bar

exif = Image.Exif()
exif[274] = 6                       # Orientation: rotate 90 deg clockwise for display
exif[271] = "WikiPortraits Eval test fixture"
exif[272] = "not a real camera"
exif[34853] = {                     # GPS IFD — the metadata that must never leave the machine
    1: "N",
    2: (IFDRational(51), IFDRational(30), IFDRational(0)),
    3: "W",
    4: (IFDRational(0), IFDRational(7), IFDRational(0)),
}
im.save(OUT, exif=exif, quality=92)

# Read it back with a real reader: a byte search for "GPS" is not evidence (see the docstring).
back = Image.open(OUT)
print(f"  {OUT}")
print(f"  stored {back.size[0]}x{back.size[1]} landscape, orientation={back.getexif().get(274)} "
      f"-> displays {back.size[1]}x{back.size[0]} portrait")
print(f"  GPS IFD: {dict(back.getexif().get_ifd(34853)) or '(none — the fixture is useless)'}")
print("  expected once uploaded through the page: read as the displayed portrait size, then sent as "
      "960x1280, with no Exif block, no orientation tag and no GPS IFD.")
