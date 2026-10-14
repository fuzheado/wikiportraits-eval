#!/usr/bin/env python3
"""Chart: what each wording does to the same four photos. Run with .venv-pdf/bin/python."""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 1240, 870
BG, INK, MUTED, LINE = (255, 255, 255), (24, 32, 45), (108, 122, 140), (214, 221, 230)
BAND = (255, 244, 224)
img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def font(sz, bold=False):
    paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else ""),
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for p in paths:
        try:
            return ImageFont.truetype(p, sz)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


F, FB, FS = font(17), font(19, True), font(14)

VARIANTS = [
    ("V1 current: \u201cWikipedia infobox portrait\u201d", (90, 120, 170)),
    ("V2 plain + true/false criteria", (196, 122, 60)),
    ("V3 \u201cpublishable professional portrait\u201d", (176, 84, 96)),
    ("V4 score 0\u20134 (professional portrait)", (120, 150, 110)),
    ("V5 choice publish / pass", (98, 88, 160)),
]
# values per image, in variant order (0-1)
IMAGES = [
    ("Andrey Stefanov", "0.740", [0.676, 0.920, 0.960, 0.676, 0.877], 0.763),
    ("Irina Tjelle", "0.528", [0.357, 0.834, 0.944, 0.642, 0.708], 0.398),
    ("Jenni Trang L\u00ea", "0.208", [0.190, 0.814, 0.939, 0.594, 0.846], 0.544),
    ("Wikimedia logo", "0.010", [0.024, 0.052, 0.164, 0.071, 0.037], 0.003),
]

X0, XW = 372, 660          # plot origin and width (0..1 maps to X0..X0+XW)
TOP = 150
ROWH = 132
BARH = 17
GAP = 3

d.text((44, 30), "One question, five wordings \u2014 same four photos", font=FB, fill=INK)
d.text((44, 60), "P(publish-ready).  Shaded band = the 0.35\u20130.65 zone where answers pile up and "
                 "carry no information.", font=F, fill=MUTED)
d.text((44, 82), "Bars shrinking among the three real portraits = lost discrimination, even when the "
                 "overall spread grows.", font=F, fill=MUTED)
d.text((44, 106), "2026-10-13 \u00b7 20 calls \u00b7 all HTTP 200 \u00b7 median 57 s per call",
       font=FS, fill=MUTED)
d.text((X0 + 4, 126), "P(publish-ready)  \u2192", font=FS, fill=MUTED)

# axis + shaded band
d.rectangle([X0 + 0.35 * XW, TOP - 18, X0 + 0.65 * XW, TOP + ROWH * len(IMAGES) - 30], fill=BAND)
for tick, lab in ((0, "0"), (0.2, "0.2"), (0.35, "0.35"), (0.5, "0.5"), (0.65, "0.65"),
                  (0.8, "0.8"), (1.0, "1.0")):
    x = X0 + tick * XW
    d.line([x, TOP - 18, x, TOP + ROWH * len(IMAGES) - 30], fill=LINE)
    d.text((x - 12, TOP + ROWH * len(IMAGES) - 24), lab, font=FS, fill=MUTED)

for ri, (label, cached, vals, composite) in enumerate(IMAGES):
    y0 = TOP + ri * ROWH
    d.text((44, y0 + 18), label, font=FB, fill=INK)
    d.text((44, y0 + 44), f"today's wording: {cached}", font=FS, fill=MUTED)
    for vi, v in enumerate(vals):
        y = y0 + vi * (BARH + GAP)
        col = VARIANTS[vi][1]
        d.rectangle([X0, y, X0 + max(2, v * XW), y + BARH], fill=col)
        tx = X0 + v * XW + 8
        d.text((tx, y + 1), f"{v:.3f}", font=FS, fill=INK)
    # composite marker
    yc = y0 + len(vals) * (BARH + GAP) + 6
    xc = X0 + composite * XW
    d.line([X0, yc + 8, xc, yc + 8], fill=(40, 40, 40), width=2)
    d.text((max(xc + 8, X0 + 70), yc + 1), f"composite {composite:.3f}", font=FS, fill=(40, 40, 40))
    d.line([44, y0 + ROWH - 14, W - 40, y0 + ROWH - 14], fill=LINE)

# legend
ly = TOP + ROWH * len(IMAGES) + 4
for i, (name, col) in enumerate(VARIANTS):
    d.rectangle([44 + (i % 3) * 400, ly + (i // 3) * 26, 64 + (i % 3) * 400,
                 ly + 14 + (i // 3) * 26], fill=col)
    d.text((72 + (i % 3) * 400, ly + (i // 3) * 26 - 2), name, font=FS, fill=INK)
d.text((44, ly + 52), "\u2014", font=FS, fill=(40, 40, 40))
d.text((72, ly + 52), "composite (black line)", font=FS, fill=INK)
d.rectangle([444, ly + 52, 470, ly + 64], fill=BAND)
d.text((478, ly + 52), "dead zone 0.35\u20130.65", font=FS, fill=INK)
d.text((44, ly + 84), "composite = the smaller of the face_visible and eyes_to_camera answers, "
                      "multiplied by the pose weight", font=FS, fill=MUTED)
d.text((44, ly + 104), "(head-and-shoulders 1.0, three-quarter 0.9, full body 0.6, candid 0.5, "
                       "group 0.25). Built from answers we already collect \u2014 no new question.",
       font=FS, fill=MUTED)

img.save(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),   # repo root
                      "research", "wording_comparison.png"))
print("wrote research/wording_comparison.png", img.size)
