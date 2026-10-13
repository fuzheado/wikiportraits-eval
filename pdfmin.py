"""Minimal dependency-free PDF writer (A4, base-14 Helvetica, JPEG/PNG images).

Enough for a tabular report with photo cards: headings, paragraphs, rules, table rows with column
widths, page numbers, and embedded thumbnails. Deliberately small so the Toolforge build needs no
pip install.

Image support is JPEG (embedded as /DCTDecode, i.e. the original bytes) and PNG (decoded to raw RGB
with zlib and embedded as /FlateDecode) — PNG decoding covers 8-bit greyscale, RGB, palette and
alpha, non-interlaced, which is what the Commons thumbnail service returns. Anything else is
skipped and reported as text rather than silently dropped.
"""
import struct
import zlib
from datetime import datetime, timezone

A4_W, A4_H = 595.28, 841.89
MARGIN = 40.0
CONTENT_W = A4_W - 2 * MARGIN


_SUBS = {"\u2192": "->", "\u2265": ">=", "\u2264": "<=", "\u2018": "'", "\u2019": "'",
         "\u201c": '"', "\u201d": '"', "\u2022": "-", "\u2013": "-", "\u00a0": " "}


def _esc(s: str) -> str:
    s = str(s)
    for k, v in _SUBS.items():
        s = s.replace(k, v)
    return (s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            .replace("\r", " "))


def _wrap(text: str, width: float, size: float, bold: bool = False) -> list:
    """Greedy wrap using an average advance width. Good enough for reports."""
    ratio = 0.54 if bold else 0.50
    maxchars = max(8, int(width / (size * ratio)))
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if len(cand) <= maxchars:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            while len(w) > maxchars:          # a single very long token (URL)
                lines.append(w[:maxchars])
                w = w[maxchars:]
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


# --------------------------------------------------------------------- images
def _jpeg_info(data: bytes):
    """(width, height, components) from a JPEG's SOF marker."""
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h, data[i + 9]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xDA:                     # start of scan: no more headers
            return None
        seg = int.from_bytes(data[i + 2:i + 4], "big")
        i += 2 + seg
    return None


def _png_info(data: bytes):
    """(width, height, colour_type, bit_depth, interlace) from IHDR."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if data[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", data[16:24])
    depth, ctype, _comp, _filt, interlace = data[24], data[25], data[26], data[27], data[28]
    return w, h, ctype, depth, interlace


def _png_to_rgb(data: bytes):
    """Decode a non-interlaced 8-bit PNG to raw RGB bytes. Returns (w, h, rgb) or None."""
    info = _png_info(data)
    if not info:
        return None
    w, h, ctype, depth, interlace = info
    if depth != 8 or interlace != 0 or ctype not in (0, 2, 3, 4, 6):
        return None
    pos, idat, pal = 8, bytearray(), None
    while pos + 8 <= len(data):
        ln = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"PLTE":
            pal = chunk
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
        pos += 12 + ln
    if not idat:
        return None
    raw = zlib.decompress(bytes(idat))
    chan = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = w * chan
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _y in range(h):
        ftype = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        if ftype == 1:
            for x in range(chan, stride):
                line[x] = (line[x] + line[x - chan]) & 0xFF
        elif ftype == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif ftype == 3:
            for x in range(stride):
                a = line[x - chan] if x >= chan else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif ftype == 4:
            for x in range(stride):
                a = line[x - chan] if x >= chan else 0
                b = prev[x]
                c = prev[x - chan] if x >= chan else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        # ---- to RGB
        if ctype == 2:
            out += line
        elif ctype == 6:
            for x in range(0, stride, 4):
                alpha = line[x + 3]
                if alpha == 255:
                    out += line[x:x + 3]
                else:
                    for k in range(3):
                        out.append((line[x + k] * alpha + 255 * (255 - alpha)) // 255)
        elif ctype == 0:
            for x in range(stride):
                out += bytes([line[x]]) * 3
        elif ctype == 4:
            for x in range(0, stride, 2):
                alpha = line[x + 1]
                v = (line[x] * alpha + 255 * (255 - alpha)) // 255
                out += bytes([v, v, v])
        else:                                  # palette
            if not pal:
                return None
            for x in range(stride):
                idx = line[x] * 3
                out += pal[idx:idx + 3]
        prev = line
    return w, h, bytes(out)


class PDF:
    def __init__(self, title: str = "Report", subtitle: str = ""):
        self.title, self.subtitle = title, subtitle
        self.pages: list = []                 # list of content streams
        self.buf: list = []
        self.y = A4_H - MARGIN
        self.page_no = 1
        self.images: dict = {}                # name -> {"obj": bytes, "w":, "h":}
        self._img_n = 0
        self.used_images: set = set()         # names referenced from content streams
        self.skipped: list = []               # (label, reason) for images we could not embed

    # ---------------------------------------------------------------- page
    def _new_page(self):
        if self.buf:
            self.pages.append("\n".join(self.buf))
        self.buf = []
        self.y = A4_H - MARGIN
        self.page_no = len(self.pages) + 1

    def _need(self, h: float):
        if self.y - h < MARGIN + 18:
            self._new_page()

    # ------------------------------------------------------------ primitives
    def text(self, s: str, size: float = 9.5, bold: bool = False, color=(0, 0, 0), indent: float = 0.0):
        for line in _wrap(s, CONTENT_W - indent, size, bold):
            self._need(size * 1.5)
            self.buf.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {color[0]} {color[1]} "
                            f"{color[2]} rg {MARGIN + indent:.2f} {self.y:.2f} Td ({_esc(line)}) Tj ET")
            self.y -= size * 1.35
        self.y -= 2

    def h1(self, s: str):
        self.text(s, size=17, bold=True)
        self.y -= 2

    def h2(self, s: str):
        self.y -= 4
        self.text(s, size=12, bold=True)
        self.y -= 1

    def small(self, s: str, indent: float = 0.0):
        self.text(s, size=8.2, color=(0.34, 0.36, 0.40), indent=indent)

    def spacer(self, h: float = 8):
        self.y -= h

    def remaining_h(self) -> float:
        """Vertical space left on the current page, in points (used to avoid near-empty pages)."""
        return self.y - MARGIN

    def rule(self, color=(0.75, 0.79, 0.83), w: float = CONTENT_W):
        self._need(6)
        self.buf.append(f"{color[0]} {color[1]} {color[2]} RG 0.6 w {MARGIN:.2f} {self.y:.2f} m "
                        f"{MARGIN + w:.2f} {self.y:.2f} l S")
        self.y -= 6

    def text_at(self, s, x, y, size=7.0, bold=False, color=(0.10, 0.12, 0.18)):
        """Free-positioned text, baseline at (x, y) — used by the distribution chart."""
        self.buf.append(f"BT /{'F2' if bold else 'F1'} {size:.1f} Tf {color[0]:.2f} {color[1]:.2f} "
                        f"{color[2]:.2f} rg {x:.2f} {y:.2f} Td ({_esc(s)}) Tj ET")

    def line_at(self, x1, y1, x2, y2, color=(0.70, 0.74, 0.79), w=0.7):
        """Free-positioned line segment."""
        self.buf.append(f"{color[0]:.2f} {color[1]:.2f} {color[2]:.2f} RG {w:.2f} w "
                        f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def bar(self, frac: float, width: float = 60, height: float = 5, color=(0.12, 0.37, 0.66)):
        frac = max(0.0, min(1.0, float(frac)))
        self.buf.append(f"{color[0]} {color[1]} {color[2]} rg {MARGIN:.2f} {self.y:.2f} "
                        f"{width * frac:.2f} {height:.2f} re f")
        self.buf.append(f"0.90 0.91 0.93 rg {MARGIN + width * frac:.2f} {self.y:.2f} "
                        f"{width * (1 - frac):.2f} {height:.2f} re f")
        self.y -= height + 3

    def row(self, cells: list, widths: list, size: float = 7.8, bold: bool = False,
            color=(0, 0, 0)):
        """One table row (single line per cell, clipped to its column width)."""
        self._need(size * 1.6)
        x = MARGIN
        for c, w in zip(cells, widths):
            txt = str(c)
            maxchars = max(3, int(w / (size * 0.5)))
            if len(txt) > maxchars:
                txt = txt[:maxchars - 1] + "\u2026"
            self.buf.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {color[0]} {color[1]} "
                            f"{color[2]} rg {x:.2f} {self.y:.2f} Td ({_esc(txt)}) Tj ET")
            x += w
        self.y -= size * 1.45

    def hr_small(self, color=(0.85, 0.87, 0.90)):
        self.rule(color=color)

    # -------------------------------------------------------------- pictures
    def add_image(self, data: bytes, label: str = "") -> str | None:
        """Register image bytes; returns a name usable with .image()/.card(), or None."""
        if not data:
            self.skipped.append((label, "no bytes"))
            return None
        self._img_n += 1
        name = f"Im{self._img_n}"
        if data[:2] == b"\xff\xd8":
            info = _jpeg_info(data)
            if not info or not info[0] or not info[1]:
                self.skipped.append((label, "JPEG header unreadable"))
                return None
            w, h, comps = info
            cs = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(comps)
            if not cs:
                self.skipped.append((label, f"JPEG with {comps} components"))
                return None
            obj = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                   f"/ColorSpace {cs} /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>"
                   .encode() + b"\nstream\n" + data + b"\nendstream")
        else:
            dec = _png_to_rgb(data)
            if not dec:
                why = "unreadable PNG" if data[:8] == b"\x89PNG\r\n\x1a\n" else "unsupported format"
                self.skipped.append((label, why))
                return None
            w, h, rgb = dec
            comp = zlib.compress(rgb, 6)
            obj = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                   f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                   f"/Length {len(comp)} >>".encode() + b"\nstream\n" + comp + b"\nendstream")
        self.images[name] = {"obj": obj, "w": w, "h": h}
        return name

    def image(self, name: str, x: float, y: float, box_w: float, box_h: float,
              frame: bool = True):
        """Draw a registered image fitted inside the box, anchored bottom-left at (x, y).

        A hairline frame is drawn around the fitted image: without it a white-on-transparent
        thumbnail (an SVG logo, say) is invisible on white paper and the reader cannot tell that
        an image was embedded at all.
        """
        im = self.images.get(name)
        if not im:
            return 0.0, 0.0
        scale = min(box_w / im["w"], box_h / im["h"])
        w, h = im["w"] * scale, im["h"] * scale
        self.used_images.add(name)
        self.buf.append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm /{name} Do Q")
        if frame:
            self.buf.append(f"0.80 0.83 0.87 RG 0.5 w {x:.2f} {y:.2f} {w:.2f} {h:.2f} re S")
        return w, h

    def card(self, lines: list, name: str | None = None, img_w: float = 118, pad: float = 10,
             size: float = 8.2):
        """A photo card: image on the left (if any), wrapped lines to its right.

        `lines` may contain ("h", text) tuples for emphasised lines; plain strings are body text.
        Breaks to a new page if the block does not fit, so a card is never split.
        """
        img_h = 0.0
        if name and name in self.images:
            im = self.images[name]
            img_h = min(img_w * im["h"] / im["w"], 210.0)
        text_lines = []
        for ln in lines:
            bold = isinstance(ln, tuple) and ln[0] == "h"
            s = ln[1] if isinstance(ln, tuple) else ln
            text_lines += [(t, bold) for t in _wrap(s, CONTENT_W - img_w - pad - 6, size, bold)]
        text_h = sum(4.6 + (1.4 if b else 1.1) * size for _, b in text_lines)
        block_h = max(img_h, text_h) + 12
        self._need(block_h)
        top = self.y
        x_img = MARGIN
        x_txt = MARGIN + (img_w + pad if name else 0)
        y = top
        for t, bold in text_lines:
            y -= size * (1.4 if bold else 1.1)
            self.buf.append(f"BT /{'F2' if bold else 'F1'} {size} Tf 0.1 0.12 0.18 rg "
                            f"{x_txt:.2f} {y:.2f} Td ({_esc(t)}) Tj ET")
            y -= 4.6
        if name and name in self.images:
            self.image(name, x_img, top - img_h, img_w, img_h)
        self.y = top - block_h

    # ---------------------------------------------------------------- output
    def save(self, path: str):
        self._new_page()
        n_pages = len(self.pages)
        objs: list[bytes] = []

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)                      # 1-based object number

        catalog_id, pages_id = 1, 2
        objs.append(b"")
        objs.append(b"")
        font1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                    b"/Encoding /WinAnsiEncoding >>")
        font2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                    b"/Encoding /WinAnsiEncoding >>")

        img_ids = {}
        for name, im in self.images.items():
            if name in self.used_images:
                img_ids[name] = add(im["obj"])
        xobj = " ".join(f"/{n} {i} 0 R" for n, i in img_ids.items())
        resources = (f"<< /Font << /F1 {font1} 0 R /F2 {font2} 0 R >>"
                     + (f" /XObject << {xobj} >>" if xobj else "") + " >>")

        page_ids, content_ids = [], []
        for i, content in enumerate(self.pages):
            footer = (f"BT /F1 7 Tf {MARGIN:.2f} 24 Td ({_esc(self.title[:90])}) Tj ET "
                      f"BT /F1 7 Tf {A4_W - MARGIN - 60:.2f} 24 Td (page {i + 1} of {n_pages}) "
                      f"Tj ET")
            stream = content + "\n" + footer
            data = stream.encode("cp1252", "replace")
            content_ids.append(add(b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n"
                                   + data + b"\nendstream"))
            page_ids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R "
                                f"/MediaBox [0 0 {A4_W} {A4_H}] /Resources {resources} "
                                f"/Contents {content_ids[-1]} 0 R >>".encode()))

        kids = " ".join(f"{i} 0 R" for i in page_ids)
        created = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%S+00'00'")
        info_id = add(f"<< /Title ({_esc(self.title)}) /Producer (wikiportraits-eval) "
                      f"/CreationDate ({created}) >>".encode())
        objs[catalog_id - 1] = (f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
        objs[pages_id - 1] = (f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())

        out = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets[1:]:
            out += f"{off:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objs) + 1} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        with open(path, "wb") as fh:
            fh.write(bytes(out))
        return path
