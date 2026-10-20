#!/usr/bin/env python3
"""Automatic FONT (attribute, section [7]) selector for a given text.

The problem: the Harmony One has **18 fonts** in the blob's `[7]` section,
and each one draws its own SUBSET of the 71 glyphs. Choosing wrong does not
raise an error: the firmware cuts the string at the first missing glyph and
keeps going ("Philips" with attribute 4 comes out "Phili", because that font
has no `p`).

And there is a second trap, which is the other half of the bug: **every
attribute carries its own PALETTE**. The 18 attributes group into 10
palettes MEASURED from the bitmap (see `palettes()`); 0 is white+black and 9
is white+antialias 0x31a6. Choosing a valid attribute from a different
palette does not truncate the text: it draws it **in a different color**.
That is why `choose()` does NOT break ties with `min()` -- that tiebreak got
the factory palette right only 45% of the time, measured. It takes (or
infers) the `contexto` -- the attribute the rest of the screen uses -- and
stays inside its palette, or warns.

    coverage(blob)                     -> {attribute: set(characters)}
    palettes(blob)                       -> {attribute: (color, ...)}  RGB565 BE
    heights(blob)                         -> {attribute: line height px}
    valid_attributes(texto, blob)                -> [attributes that draw it whole]
    choose(texto, blob, contexto=None)  -> attribute | None
    choose_detail(...)                 -> dict with valid ones + palette warning
    encode(texto, atributo, blob)    -> bytes | None
    width(texto, atributo, blob)        -> int (px, for centering)

Structure of section `[7]` (measured offset: `0x0291af` in config_raw.bin):

    [7]     <u16 18><18 x ptr24>                  one pointer per ATTRIBUTE
    font    <u8 height><u16 71><71 x ptr24>       index = glyph_code - 1
            00 00 00 = that glyph DOES NOT EXIST in this font
    glyph   <u8 width><mode-0 RLE stream>

READ-ONLY module. Does not import `glyphs.py` or `add_device.py` (it
tabulates them from scratch) and does not call `write.py` or any libconcord
primitive.

Control: `python3 fonts.py [blob]`      Standalone test: `--text "Philips"`
"""

from __future__ import annotations

import argparse
import collections
import functools
import hashlib
import pathlib

BASE = 0x040000
MAESTRO_SECCION7 = 0x0C + 4 * 7
MAESTRO_T6 = 0x0C + 4 * 6

# ==========================================================================
# THE GLYPH TABLE: 71 codes -> 71 characters, bijective.
#
# Frozen here (it is a property of the FIRMWARE -- which draws every code --
# not of a particular blob). Verified by THREE independent checks, all in
# `_check()`:
#   (a) SHAPE: the 423 non-null bitmaps decode (0 BADDECODE, 0 hdr!=width)
#       and the shape of every code is the same across the 18 fonts.
#   (b) SEMANTICS: the 476 factory (attribute, text) pairs from `table[6]`
#       translate 476/476 with not a single unresolved code, and read as
#       grammatical English ("Welcome to your remote.", 'press "Help" now.',
#       "It's that easy!", "Battery Trickle\\Charge", "January"/"June"/"July").
#   (c) STRUCTURE: 0 factory texts use a glyph ABSENT from their own font --
#       if the subsets were wrong, the factory would be drawing with glyphs
#       that do not exist.
#
# GOTCHA that cost an entire round: **0x41 is NOT the space**. The space is
# 0x1E (width 3, in 9 fonts). 0x41 is a SECOND blank glyph, wider (4 px),
# that exists only in font 11 -- and font 11 has both. The proof is a single
# factory string: `Starting<0x41>TV<0x1E>HD` uses both in the SAME string.
# Mapping them to the same Python character breaks `inv[' ']` and makes
# `encode()` emit 0x41 for every space -- the language's most frequent
# character. That is why 0x41 gets its own character and NEVER wins the ' '.
# ==========================================================================
BLANCO_ANCHO = "␣"  # 0x41: 4 px blank, only in font 11

GLYPHS: dict[int, str] = {
    0x01: "H",
    0x02: "y",
    0x03: "S",
    0x04: "u",
    0x05: "n",
    0x06: "M",
    0x07: "o",
    0x08: "T",
    0x09: "e",
    0x0A: "W",
    0x0B: "d",
    0x0C: "h",
    0x0D: "F",
    0x0E: "r",
    0x0F: "i",
    0x10: "a",
    0x11: "t",
    0x12: "1",
    0x13: "2",
    0x14: ":",
    0x15: "3",
    0x16: "4",
    0x17: "5",
    0x18: "6",
    0x19: "7",
    0x1A: "8",
    0x1B: "9",
    0x1C: "0",
    0x1D: "/",
    0x1E: " ",
    0x1F: "b",
    0x20: "l",
    0x21: "v",
    0x22: "s",
    0x23: "w",
    0x24: "!",
    0x25: "O",
    0x26: "K",
    0x27: "U",
    0x28: "B",
    0x29: "C",
    0x2A: "c",
    0x2B: "p",
    0x2C: "f",
    0x2D: "g",
    0x2E: "L",
    0x2F: ".",
    0x30: "m",
    0x31: "G",
    0x32: "I",
    0x33: "z",
    0x34: "k",
    0x35: "R",
    0x36: "E",
    0x37: "D",
    0x38: "P",
    0x39: "A",
    0x3A: "q",
    0x3B: "\\",
    0x3C: "N",
    0x3D: "x",
    0x3E: "?",
    0x3F: "Y",
    0x40: "V",
    0x41: BLANCO_ANCHO,
    0x42: ",",
    0x43: "'",
    0x44: "-",
    0x45: "j",
    0x46: '"',
    0x47: "J",
}

# The one assertion that would have caught the 0x41 bug on the spot: a
# monoalphabetic substitution is either bijective or it is not. Runs on import.
assert len(GLYPHS) == 71, "the table has %d codes, not 71" % len(GLYPHS)
assert len(set(GLYPHS.values())) == 71, "table is NOT bijective: %s" % [
    c for c, k in collections.Counter(GLYPHS.values()).items() if k > 1
]
INV: dict[str, int] = {v: k for k, v in GLYPHS.items()}

# The THREE uppercase letters the hardware CANNOT draw in any font. Note:
# 'x' exists but it is LOWERCASE (0x3D, starts at x-height, not cap-height;
# anchored in the factory text "Exit" / "Press Exit now.").
MAYUSCULAS_AUSENTES = "QXZ"

# ==========================================================================
# THE HARMONY ONE+: the font is assembled PER CONFIG.
#
# Measured on a Harmony One+ (arch 12, skin 54, firmware 3.4, French
# firmware). Its section [7] carries the French accents (é è à ê â û) plus
# `( ) ; & ? * Q`, and `X`/`Z` are still missing. Unlike the One, the
# code -> character mapping is NOT fixed: two dumps of the same remote,
# before and after adding one device, keep 0x01..0x42 and reassign every
# code from 0x43 up (81 slots before, 82 after). So a One+ blob is read with
# `one_plus_table()`, which identifies each code by the SHAPE of its bitmaps
# against `glyph_shapes_one_plus.SHAPES_ONE_PLUS`.
#
# `GLYPHS_ONE_PLUS` below is the reading of ONE dump -- the one the shapes
# were taken from -- and decodes ONLY that dump. Read the same way as
# `GLYPHS`:
#   (a) SHAPE: every code's bitmap rendered and read by eye (82/82);
#   (b) SEMANTICS: factory texts read as French -- "La sensibilité de
#       l'écran est", "Touchez l'écran pour", "Un véritable jeu d'enfant!",
#       "Initialisation", "LED IR";
#   (c) KNOWN PLAINTEXT: the name and command labels of a device added
#       through MyHarmony show up ONLY in the dump taken after the sync,
#       encoded with this table.
# [ASSUMED] 0x4A: a 5x5 sign, `*` or `+`; one command label uses it, no
#           firmware text pins it.
# [ASSUMED] 0x23 `l` / 0x3A `I`: identical bitmaps, told apart by context
#           ("Initialisation", "LED IR", "ID").
# ==========================================================================
GLYPHS_ONE_PLUS: dict[int, str] = {
    0x01: "H",
    0x02: "y",
    0x03: "D",
    0x04: "i",
    0x05: "m",
    0x06: "L",
    0x07: "u",
    0x08: "n",
    0x09: "M",
    0x0A: "a",
    0x0B: "r",
    0x0C: "e",
    0x0D: "J",
    0x0E: "V",
    0x0F: "S",
    0x10: "1",
    0x11: "2",
    0x12: ":",
    0x13: "3",
    0x14: "4",
    0x15: "5",
    0x16: "6",
    0x17: "7",
    0x18: "8",
    0x19: "9",
    0x1A: "0",
    0x1B: "/",
    0x1C: " ",
    0x1D: "v",
    0x1E: "d",
    0x1F: "s",
    0x20: "b",
    0x21: "t",
    0x22: "f",
    0x23: "l",
    0x24: BLANCO_ANCHO,  # 4 px blank, the One's 0x41
    0x25: "!",
    0x26: "O",
    0x27: "K",
    0x28: "C",
    0x29: "o",
    0x2A: "x",
    0x2B: "U",
    0x2C: "B",
    0x2D: "é",
    0x2E: "à",
    0x2F: "j",
    0x30: "c",
    0x31: "A",
    0x32: "p",
    0x33: "g",
    0x34: ".",
    0x35: "P",
    0x36: "è",
    0x37: "h",
    0x38: "z",
    0x39: "W",
    0x3A: "I",
    0x3B: "E",
    0x3C: "R",
    0x3D: "F",
    0x3E: "'",
    0x3F: "q",
    0x40: "N",
    0x41: "\\",
    0x42: "T",
    0x43: "-",
    0x44: "?",
    0x45: "ê",
    0x46: "w",
    0x47: "(",
    0x48: ")",
    0x49: "G",
    0x4A: "*",
    0x4B: ",",
    0x4C: "Q",
    0x4D: "&",
    0x4E: ";",
    0x4F: "â",
    0x50: "k",
    0x51: "Y",
    0x52: "û",
}
assert len(GLYPHS_ONE_PLUS) == 82, "the One+ table has %d codes, not 82" % len(
    GLYPHS_ONE_PLUS
)
assert len(set(GLYPHS_ONE_PLUS.values())) == 82, "One+ table is NOT bijective"

def font_slots(blob) -> int | None:
    """Slots per font that section [7] declares, or `None` if the section
    cannot be read. NOT a firmware fingerprint: the font only goes up to
    the highest code the config uses (two dumps of the same One+, before
    and after adding a device, declare 81 and 82)."""
    b = _bytes(blob)
    try:
        off = u24(b, MAESTRO_SECCION7) - BASE
        f = u24(b, off + 2) - BASE
        return u16(b, f + 1)
    except IndexError:
        return None


def _blank(b: bytes, code: int) -> bool | None:
    """`True` if every font that has `code` draws it with no lit pixel,
    `False` if one draws something, `None` if no font has it."""
    seen = False
    for f in fonts_by_attribute(b).values():
        p = f["ptr"][code - 1] if code - 1 < len(f["ptr"]) else 0
        if not p:
            continue
        got = _rle(b, p + 1)
        if got is None:
            return None
        if any(kind == "lit" for row in got[2] for kind, _n, _o in row):
            return False
        seen = True
    return True if seen else None


def shape_key(b: bytes, p: int) -> str | None:
    """Fingerprint of the glyph bitmap at `p`: its outline (lit / skipped
    pixels, no colors) hashed, see `glyph_shapes_one_plus.py`."""
    got = _rle(b, p + 1)
    if got is None:
        return None
    w, _h, rows = got
    mask = "/".join(
        "".join(("1" if kind == "lit" else "0") * n for kind, n, _o in row)
        for row in rows
    )
    return hashlib.sha1(("%d:%s" % (w, mask)).encode()).hexdigest()[:12]


@functools.lru_cache(maxsize=8)
def one_plus_table(b: bytes) -> dict[int, str]:
    """THIS One+ blob's code -> character table, read off its own bitmaps.

    Every font votes for each code it draws, with the character its outline
    has in `SHAPES_ONE_PLUS`. An outline shared by two characters (`l`/`I`)
    does not vote; such a code is settled by elimination once the others
    are known. A code with no recognized outline, two codes claiming the
    same character, or one code with two readings are left OUT of the table:
    they decode as `?` and `encode()` refuses them -- never guessed."""
    from glyph_shapes_one_plus import SHAPES_ONE_PLUS  # noqa: PLC0415

    votes: dict[int, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    shared: dict[int, set[str]] = {}
    for f in fonts_by_attribute(b).values():
        for i, p in enumerate(f["ptr"]):
            if not p:
                continue
            chars = SHAPES_ONE_PLUS.get(shape_key(b, p) or "")
            if chars is None:
                continue
            if len(chars) == 1:
                votes[i + 1][chars] += 1
            else:
                shared.setdefault(i + 1, set(chars)).intersection_update(chars)
    out = {c: v.most_common(1)[0][0] for c, v in votes.items() if len(v) == 1}
    for c, chars in shared.items():
        left = chars - set(out.values())
        if c not in votes and len(left) == 1:
            out[c] = left.pop()
    claimed = collections.Counter(out.values())
    return {c: ch for c, ch in out.items() if claimed[ch] == 1}


def glyphs_for(blob) -> dict[int, str]:
    """The glyph table THIS blob is written with. A One+ is told apart by
    PIXELS -- its space is 0x1C (blank) and its 0x1E is `d`; on the One 0x1C
    is `0` and 0x1E is the space -- and gets `one_plus_table()`. Anything
    else (the One, a short or garbled blob) gets `GLYPHS`, which is exactly
    what every caller used before the One+ was measured."""
    b = _bytes(blob)
    return one_plus_table(b) if is_one_plus(b) else GLYPHS


def is_one_plus(blob) -> bool:
    """`True` for a One+ font (0x1C blank, 0x1E drawn), see `glyphs_for()`."""
    b = _bytes(blob)
    try:
        return _blank(b, 0x1C) is True and _blank(b, 0x1E) is False
    except IndexError:
        return False


def inv_for(blob) -> dict[str, int]:
    """`glyphs_for(blob)` inverted: character -> code."""
    t = glyphs_for(blob)
    return INV if t is GLYPHS else {v: k for k, v in t.items()}


# Palette of the user screens (white + antialias 0x31a6, which read
# little-endian is the 0xa631 that ESTADO.md cites). Attributes 1, 4 and 9.
PALETA_USUARIO = (0x31A6, 0xFFFF)


def u16(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8)


def u24(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def _bytes(blob) -> bytes:
    if isinstance(blob, (bytes, bytearray)):
        return bytes(blob)
    return pathlib.Path(blob).read_bytes()


# -------------------------------------------------------------- section [7]
def fonts_by_attribute(blob) -> dict[int, dict]:
    """`{attribute: {'height', 'ptr': [71 pointers]}}`, READ from the blob.
    `ptr[code-1] == 0` -> that glyph does not exist in that font."""
    b = _bytes(blob)
    off = u24(b, MAESTRO_SECCION7) - BASE
    out: dict[int, dict] = {}
    for j in range(u16(b, off)):
        f = u24(b, off + 2 + 3 * j) - BASE
        ptr = []
        for k in range(u16(b, f + 1)):
            v = u24(b, f + 3 + 3 * k)
            ptr.append(0 if v == 0 else v - BASE)
        out[j] = {"height": b[f], "ptr": ptr}
    return out


def _rle(b: bytes, at: int):
    """<mode-0 RLE stream> -> (width, height, rows) or None. `at` = ptr + 1.
    Without `rle.py`'s MIN_W/MIN_H guards: those are meant for 8 px icons and
    silently drop the narrow glyphs (i, l, j, period, comma)."""
    o, rows, row, x, w = at, [], [], 0, None
    while o < len(b):
        c = b[o]
        o += 1
        if c == 0x00:
            if row:
                rows.append(row)
            return (w, len(rows), rows) if w is not None and rows else None
        if c == 0x80:
            if w is None:
                w = x
            elif x != w:
                return None
            if not 1 <= w <= 176:
                return None
            rows.append(row)
            row, x = [], 0
            if len(rows) > 220:
                return None
            continue
        n = c & 0x7F
        if n == 0:
            return None
        if c & 0x80:
            row.append(("skip", n, None))
        else:
            if o + 2 * n > len(b):
                return None
            row.append(("lit", n, o))
            o += 2 * n
        x += n
        if (w is not None and x > w) or x > 176:
            return None
    return None


def coverage(blob) -> dict[int, set[str]]:
    """`{attribute: set(characters that font CAN actually draw)}`, computed by
    reading the blob's section [7] -- not hardcoded."""
    out: dict[int, set[str]] = {}
    tabla = glyphs_for(blob)
    for attr, f in fonts_by_attribute(blob).items():
        out[attr] = {
            tabla[i + 1] for i, p in enumerate(f["ptr"]) if p and (i + 1) in tabla
        }
    return out


def heights(blob) -> dict[int, int]:
    """`{attribute: line height in px}`."""
    return {a: f["height"] for a, f in fonts_by_attribute(blob).items()}


_CACHE_PAL: dict[int, dict] = {}


def palettes(blob) -> dict[int, tuple[int, ...]]:
    """`{attribute: (RGB565 big-endian colors, sorted)}`, MEASURED from the
    literal pixels of every font's glyphs -- not hardcoded.

    Measured in config_raw.bin: 10 distinct palettes for 18 attributes. Every
    font uses exactly 2 colors (font 14, only one: it has no antialiasing).
      (0x0000, 0xffff) -> 0, 11, 13, 15    white + BLACK
      (0x31a6, 0xffff) -> 1, 4, 9          white + antialias  (PALETA_USUARIO)
      (0x0060, 0x9663) -> 2, 12            (0x0820, 0xdbc3) -> 3, 16
      (0x10e4, 0x973e) -> 6, 7             (0x31a6, 0x973e) -> 5
      (0x31a6, 0xdbc3) -> 8                (0x31a6, 0x9663) -> 10
      (0x0020, 0xce79) -> 17               (0xffff,)        -> 14
    """
    b = _bytes(blob)
    key = hash(b)
    if key in _CACHE_PAL:
        return _CACHE_PAL[key]
    out: dict[int, tuple[int, ...]] = {}
    for attr, f in fonts_by_attribute(b).items():
        cols: set[int] = set()
        for p in f["ptr"]:
            if not p:
                continue
            got = _rle(b, p + 1)
            if not got:
                continue
            for row in got[2]:
                for kind, n, src in row:
                    if kind != "lit":
                        continue
                    for k in range(n):
                        cols.add((b[src + 2 * k] << 8) | b[src + 2 * k + 1])
        out[attr] = tuple(sorted(cols))
    _CACHE_PAL[key] = out
    return out


# ---------------------------------------------------------------- selection
def valid_attributes(text: str, blob) -> list[int]:
    """Attributes that can draw ALL of `text`, without cutting it."""
    need = set(text)
    return sorted(a for a, ch in coverage(blob).items() if need <= ch)


def choose_detail(text: str, blob, contexto: int | None = None, preferidos=None):
    """The full reasoning behind `choose()`, so the caller does not choose
    blind. Returns:

        {'atributo': int|None, 'valid_attributes': [...], 'faltan': set(str),
         'aviso': str|None, 'paleta': tuple|None, 'alto': int|None}

    A `warning` that starts with "PALETTE CHANGES" means the chosen attribute
    draws the text in a DIFFERENT COLOR than `contexto`. `choose()` in strict
    mode returns `None` in that case instead of silently changing the color.

    Order of preference:
      1. `preferidos` (the first in the list that is valid)
      2. `contexto`, if valid -> changes nothing
      3. valid ones with the SAME palette and the SAME line height as `contexto`
      4. valid ones with the same palette as `contexto` (warns: height changes)
      5. no context: valid ones from PALETA_USUARIO, the one with most coverage
      6. the rest, with a palette-change `warning`
    """
    b = _bytes(blob)
    cob = coverage(b)
    pal = palettes(b)
    alt = heights(b)
    r = {
        "atributo": None,
        "valid_attributes": [],
        "missing": set(text) - set(inv_for(b)),
        "warning": None,
        "paleta": None,
        "height": None,
    }
    if r["missing"]:
        r["warning"] = "characters outside the glyph cipher: %s" % "".join(
            sorted(r["missing"])
        )
        return r
    r["valid_attributes"] = val = valid_attributes(text, b)
    if not val:
        cubre = set().union(*cob.values()) if cob else set()
        absent = "".join(sorted(set(text) - cubre))
        r["warning"] = "none of the %d fonts covers the whole text%s" % (
            len(cob),
            (": no attribute has %r" % absent)
            if absent
            else " (every character exists, just not all together in one font)",
        )
        return r

    chosen = None
    if preferidos:
        chosen = next((p for p in preferidos if p in val), None)
    if chosen is None and contexto is not None:
        if contexto in val:
            chosen = contexto
        else:
            same = [a for a in val if pal[a] == pal.get(contexto)]
            same_height = [a for a in same if alt[a] == alt.get(contexto)]
            if same_height:
                chosen = min(same_height)
            elif same:
                chosen = min(same)
                r["warning"] = (
                    "same color, different line height (%d px against the context's %d px)"
                    % (alt[chosen], alt[contexto])
                )
    if chosen is None:
        pref = [a for a in val if pal[a] == PALETA_USUARIO]
        chosen = sorted(pref or val, key=lambda a: (-len(cob[a]), a))[0]
        if contexto is not None and pal[chosen] != pal.get(contexto):
            r["warning"] = (
                "PALETTE CHANGES: context=attr %d %s -> chosen=attr %d %s"
                % (
                    contexto,
                    ["%#06x" % c for c in pal.get(contexto, ())],
                    chosen,
                    ["%#06x" % c for c in pal[chosen]],
                )
            )
    r["atributo"] = chosen
    r["paleta"] = pal[chosen]
    r["height"] = alt[chosen]
    return r


def choose(
    text: str,
    blob,
    contexto: int | None = None,
    preferidos=None,
    estricto: bool = True,
) -> int | None:
    """An attribute that draws ALL of `text`, or `None`. Never silently cuts.

    `contexto` = the attribute the rest of the screen uses. With `estricto`
    (default), if the only possible attribute would change the PALETTE
    relative to `contexto`, returns `None` instead of drawing the text in a
    different color; with `estricto=False` it returns it anyway (check
    `choose_detail()['warning']`)."""
    r = choose_detail(text, blob, contexto=contexto, preferidos=preferidos)
    if r["atributo"] is None:
        return None
    if estricto and r["warning"] and r["warning"].startswith("PALETTE CHANGES"):
        return None
    return r["atributo"]


def encode(text: str, atributo: int, blob) -> bytes | None:
    """`text` as glyph indices + `0x00` terminator, for THAT attribute, or
    `None` if that font cannot draw it whole (does not truncate). Call after
    `choose()`, with the attribute it returned."""
    chars = coverage(blob).get(atributo)
    if chars is None or not set(text) <= chars:
        return None
    inv = inv_for(blob)
    return bytes(inv[c] for c in text) + b"\x00"


def width(text: str, atributo: int, blob) -> int:
    """Width in px (sum of byte 0 of every glyph's bitmap -- the same count
    as `device.text_width`), for centering. Raises `ValueError` if the
    attribute does not work: there is no sensible width for a glyph that
    does not exist."""
    b = _bytes(blob)
    f = fonts_by_attribute(b).get(atributo)
    if f is None:
        raise ValueError("attribute %r does not exist in section [7]" % (atributo,))
    total = 0
    inv = inv_for(b)
    for c in text:
        code = inv.get(c)
        if code is None:
            raise ValueError("character %r outside the glyph cipher" % c)
        if code - 1 >= len(f["ptr"]) or not f["ptr"][code - 1]:
            raise ValueError(
                "attribute %d has no glyph for %r (call choose() first)" % (atributo, c)
            )
        total += b[f["ptr"][code - 1]]
    return total


# ==========================================================================
# CONTROL. Walks ALL the factory texts (a `table[6]` interpreter,
# reimplemented here) and runs 7 checks, one of them a MUTATION TEST that
# has to FAIL -- a check that cannot fail does not check anything.
# ==========================================================================
def _factory_texts(b: bytes) -> set[tuple[int, bytes]]:
    """`{(active attribute, raw glyphs)}` of every TEXT / INLINE_TEXT that
    some `table[6]` screen draws, carrying the live attribute along and
    following CALL/JMP/SWITCH."""
    t6 = u24(b, MAESTRO_T6) - BASE
    out: set[tuple[int, bytes]] = set()
    vistos: set[tuple[int, int]] = set()

    def anda(o: int, attr: int, prof: int = 0) -> None:
        if prof > 8:
            return
        ini = o
        while 0 <= o < len(b) and o - ini < 8000:
            if (o, attr) in vistos:
                return
            vistos.add((o, attr))
            op = b[o]
            if op == 0x00:
                return
            if op == 0x01:
                o += 7
            elif op == 0x02:
                o += 6
            elif op == 0x04:
                d = u24(b, o + 3) - BASE
                fin = b.find(b"\x00", d)
                if fin != -1:
                    out.add((attr, bytes(b[d:fin])))
                o += 6
            elif op == 0x05:
                fin = b.find(b"\x00", o + 3)
                if fin == -1:
                    return
                out.add((attr, bytes(b[o + 3 : fin])))
                o = fin + 1
            elif op == 0x10:
                attr = b[o + 1]
                o += 2
            elif op == 0x11:
                o += 4
            elif op == 0x12:
                q = o + 3
                dest = []
                for _ in range(b[o + 2]):
                    dest.append(u24(b, q + 1) - BASE)
                    q += 4
                n2 = b[q]
                q += 1
                for _ in range(n2):
                    dest.append(u24(b, q + 2) - BASE)
                    q += 5
                for d in dest:
                    anda(d, attr, prof + 1)
                o = q
            elif op == 0x14:
                o = u24(b, o + 1) - BASE
            elif op == 0x16:
                anda(u24(b, o + 1) - BASE, attr, prof + 1)
                o += 4
            else:
                return  # 0x17 = RET, a legitimate end of the walk

    for k in range(u16(b, t6)):
        tp = u24(b, t6 + 3 + 3 * k) - BASE
        if tp < 0 or tp + 6 > len(b):
            continue
        n = u16(b, tp + 4)
        if not (1 <= n <= 200) or tp + 6 + 3 * n > len(b):
            continue
        for j in range(n):
            sp = u24(b, tp + 6 + 3 * j) - BASE
            if 0 <= sp and sp + 7 <= len(b):
                anda(u24(b, sp + 4) - BASE, 0)
    return out


CENTINELAS = (
    "Bootloader",
    "Devices",
    "Welcome to your remote.",
    "Display",
    "Battery Trickle\\Charge",
    "It's that easy!",
)

#: Same role for the One+ (French firmware). Firmware texts only, none of
#: the user's device or activity names, so any One+ dump carries them.
CENTINELAS_ONE_PLUS = (
    "Initialisation",
    "Dispositifs",
    "Bienvenue!",
    "Un véritable jeu d'enfant!",
    "démarrage\\Echec de la mise à",
    "Février",
)


def _check(blob_path: str) -> bool:
    b = pathlib.Path(blob_path).read_bytes()
    tabla = glyphs_for(b)
    centinelas = CENTINELAS if tabla is GLYPHS else CENTINELAS_ONE_PLUS
    fonts = fonts_by_attribute(b)
    cob, pal, alt = coverage(b), palettes(b), heights(b)
    ok: dict[str, bool] = {}

    print("=== the %d fonts of section [7] ===" % len(fonts))
    for a in sorted(fonts):
        print(
            "  attr %2d  height=%2d  %2d glyphs  palette %-17s %s"
            % (
                a,
                alt[a],
                sum(1 for p in fonts[a]["ptr"] if p),
                " ".join("%#06x" % c for c in pal[a]),
                "".join(sorted(cob[a])),
            )
        )

    # (a) SHAPE -------------------------------------------------------------
    tot = bad = mis = 0
    for f in fonts.values():
        for p in f["ptr"]:
            if not p:
                continue
            tot += 1
            got = _rle(b, p + 1)
            if not got:
                bad += 1
            elif got[0] != b[p]:
                mis += 1
    ok["forma"] = bad == 0 and mis == 0
    print(
        "\n(a) SHAPE: %d non-null glyphs, %d BADDECODE, %d hdr!=width -> %s"
        % (tot, bad, mis, "OK" if ok["forma"] else "FAIL")
    )

    # (b) SEMANTICS -----------------------------------------------------------
    textos = _factory_texts(b)
    sin = [(a, c) for a, c in textos if any(g not in tabla for g in c)]
    ok["semantica"] = not sin
    print(
        "(b) SEMANTICS: %d factory (attr, text) pairs, %d untranslated -> %s"
        % (len(textos), len(sin), "OK" if ok["semantica"] else "FAIL")
    )
    for a, c in sin[:5]:
        print(
            "      attr=%d unresolved: %s" % (a, [hex(g) for g in c if g not in tabla])
        )
    trad = [
        (a, "".join(tabla[g] for g in c), c)
        for a, c in textos
        if not any(g not in tabla for g in c)
    ]
    frases = {s for _, s, _ in trad}
    vivos = [w for w in centinelas if w in frases]
    print(
        "      factory sentinels present: %d/%d  %s"
        % (len(vivos), len(centinelas), vivos[:3])
    )

    # (c) STRUCTURE -----------------------------------------------------------
    outside = [
        (a, s)
        for a, s, c in trad
        if a in fonts
        and any(
            g - 1 >= len(fonts[a]["ptr"]) or not fonts[a]["ptr"][g - 1] for g in c
        )
    ]
    ok["estructura"] = not outside
    print(
        "(c) STRUCTURE: %d texts use a glyph ABSENT from their own font -> %s"
        % (len(outside), "OK" if ok["estructura"] else "FAIL")
    )

    # (d) ANCHOR: every code, and how many slots the factory exercises --------
    used = {g for _, _, c in trad for g in c}
    ejerc = {(a, g) for a, _, c in trad for g in c}
    nonull = {
        (a, i + 1) for a, f in fonts.items() for i, p in enumerate(f["ptr"]) if p
    }
    # only the codes this font declares: a One+ font stops at the highest
    # code the config uses (81 or 82 slots); on the One that is all 71
    declarados = {g for g in tabla if g <= (font_slots(b) or 0)}
    ok["anclaje"] = used == declarados
    print(
        "(d) ANCHOR: %d/%d codes appear in >=1 factory text -> %s"
        % (len(used), len(declarados), "OK" if ok["anclaje"] else "FAIL")
    )
    print(
        "      non-null (font,code) slots: %d; exercised by factory: %d;"
        " only inferred: %d" % (len(nonull), len(ejerc & nonull), len(nonull - ejerc))
    )

    # (e) SELECTION: the check that DISCRIMINATES ------------------------------
    correct = correct_min = 0
    for a, s, _ in trad:
        e = choose(s, b, contexto=a, estricto=False)
        if e is not None and pal[e] == pal[a] and alt[e] == alt[a]:
            correct += 1
        v = valid_attributes(s, b)
        if v and pal[min(v)] == pal[a]:
            correct_min += 1
    ok["seleccion"] = correct == len(trad)
    print(
        "(e) SELECTION: choose(contexto=factory attr) gives the correct"
        " palette AND height: %d/%d -> %s"
        % (correct, len(trad), "OK" if ok["seleccion"] else "FAIL")
    )
    print(
        "      reference: the old min(valid_attributes) tiebreak got the palette"
        " right %d/%d = %.0f%%" % (correct_min, len(trad), 100 * correct_min / len(trad))
    )

    # (f) ROUND-TRIP: this runs, but it is NOT evidence the table is the
    #     correct one -- `encode` is the exact inverse of the same table,
    #     so it holds for ANY bijective table. It only catches a NON-bijective
    #     table, which is exactly the 0x41 bug.
    rt = sum(1 for a, s, c in trad if encode(s, a, b) == c + b"\x00")
    ok["roundtrip"] = rt == len(trad)
    print(
        "(f) ROUND-TRIP (only proves bijectivity): %d/%d -> %s"
        % (rt, len(trad), "OK" if ok["roundtrip"] else "FAIL")
    )

    # (g) MUTATION: tampering with the table has to BREAK check (b). A CYCLE
    #     of 6 letters is permuted: it stays bijective, so the round-trip
    #     does not notice -- which is exactly why the round-trip is not enough.
    ciclo = "PDEBLG"
    rot = {ciclo[i]: ciclo[(i + 1) % len(ciclo)] for i in range(len(ciclo))}
    falsa = {k: rot.get(v, v) for k, v in tabla.items()}
    frases_f = {"".join(falsa[g] for g in c) for _, _, c in trad}
    inv_f = {v: k for k, v in falsa.items()}
    rt_f = sum(
        1
        for _, _, c in trad
        if bytes(inv_f["".join(falsa[g] for g in c)[i]] for i in range(len(c)))
        + b"\x00"
        == c + b"\x00"
    )
    # Only the sentinels that CONTAIN one of the cycle's letters can be
    # required to break: a sentence with none of them stays intact and its
    # survival says nothing (e.g. "It's that easy!", which has not a single
    # uppercase letter from the cycle).
    tocados = [w for w in centinelas if set(w) & set(ciclo)]
    sobreviven = [w for w in tocados if w in frases_f]
    alteradas = sum(1 for _, s, c in trad if s != "".join(falsa[g] for g in c))
    ok["mutacion"] = not sobreviven and len(vivos) == len(centinelas)
    print("(g) MUTATION (tampered table: bijective 6-letter cycle, %s)" % ciclo)
    print(
        "      round-trip with the FALSE table: %d/%d  <- this is why the"
        " round-trip is NOT evidence" % (rt_f, len(trad))
    )
    print(
        "      %d/%d factory texts change reading with the false table"
        % (alteradas, len(trad))
    )
    print(
        "      sentinels: %d/%d intact with the real table; of the %d that"
        " contain a cycle letter, %d survive the mutation -> %s"
        % (
            len(vivos),
            len(centinelas),
            len(tocados),
            len(sobreviven),
            "OK" if ok["mutacion"] else "FAIL",
        )
    )

    # (h) NEGATIVES -------------------------------------------------------
    print("\n=== NEGATIVE CHECK ===")
    # Attribute numbers below are the One's factory layout; the One+ arranges
    # its 17 fonts differently, so those two only run against a One blob.
    negs = []
    if tabla is GLYPHS:
        negs += [
            (
                "choose('Philips', contexto=9) == 9  (the device row uses 9)",
                choose("Philips", b, contexto=9) == 9,
            ),
            (
                "choose('Philips', contexto=4) != 4  (attr 4 has no 'p')",
                choose("Philips", b, contexto=4) != 4,
            ),
        ]
    inv = inv_for(b)
    con_blanco = [a for a, ch in cob.items() if {"a", " "} <= ch]
    attr_blanco = 9 if tabla is GLYPHS else (con_blanco[0] if con_blanco else None)
    negs += [
        (
            "choose('Xbox') is None   (uppercase 'X' does not exist in any font)",
            choose("Xbox", b) is None,
        ),
        (
            "choose('Zappiti') is None   ('Z' does not exist)",
            choose("Zappiti", b) is None,
        ),
        (
            "choose('Señal@') is None   (outside the cipher)",
            choose("Señal@", b) is None,
        ),
        (
            "encode(' ') uses %#04x and NEVER %#04x  (the wide-blank bug)"
            % (inv[" "], inv[BLANCO_ANCHO]),
            attr_blanco is not None
            and encode("a a", attr_blanco, b)
            == bytes([inv["a"], inv[" "], inv["a"], 0x00]),
        ),
    ]
    for etq, v in negs:
        print("  %-70s -> %s" % (etq, "OK" if v else "FAIL"))
    ok["negativos"] = all(v for _, v in negs)

    todo = all(ok.values())
    print(
        "\n%s   (%s)"
        % (
            "=== ALL OK ===" if todo else "=== THERE ARE FAILURES ===",
            ", ".join("%s:%s" % (k, "ok" if v else "FAIL") for k, v in ok.items()),
        )
    )
    return todo


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "blob",
        nargs="?",
        default=str(
            pathlib.Path(__file__).resolve().parent.parent
            / "backups"
            / "config_raw.bin"
        ),
    )
    ap.add_argument("--text", help="test choose()/encode() with a text")
    ap.add_argument(
        "--contexto",
        type=int,
        default=None,
        help="attribute the rest of the screen uses",
    )
    a = ap.parse_args()
    if a.text:
        b = pathlib.Path(a.blob).read_bytes()
        r = choose_detail(a.text, b, contexto=a.contexto)
        print("text     : %r" % a.text)
        print("valid    : %s" % r["valid_attributes"])
        print("attribute: %s" % r["atributo"])
        print("warning  : %s" % r["warning"])
        if r["atributo"] is not None:
            print("codes    : %s" % encode(a.text, r["atributo"], b).hex(" "))
            print(
                "width    : %d px   (line height %d px)"
                % (width(a.text, r["atributo"], b), r["height"])
            )
        return 0 if r["atributo"] is not None else 1
    return 0 if _check(a.blob) else 1


if __name__ == "__main__":
    raise SystemExit(main())
