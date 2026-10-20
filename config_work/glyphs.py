#!/usr/bin/env python3
"""The blob's glyph table: **decodes** the text it stores and **encodes** new text.

The blob does not store ASCII: it stores **glyph indices**, one byte per
character against a monoalphabetic table, terminated by `0x00` (see
`glyph_decode.py`).

`BASE` is the WHOLE table -- 71 of 71 codes -- and it comes from
`fonts.py`'s `GLYPHS`, which is read out of the blob's own font section [7]
(the bitmap of every glyph in 18 fonts) and pinned against the factory
texts. **It needs no account data.** For a while this file only had the 40
codes that had been cribbed by hand from known command names, and the rest
was filled in with the Hub's vocabulary; without that file the names came
out mutilated on screen (`?or?uler ??R` for `DVR`). They no longer
do -- see the block above `BASE`.

`extender()` stays because it is the historical derivation and it is still
a live cross-check: pointed at a vocabulary it re-derives the table by
elimination and must never contradict `BASE`. With `BASE` already complete
it learns nothing, which is the correct answer, not a failure. How it
works: the Hub's config carries hundreds of plaintext names
(`CommandTypeId`, `Name`, manufacturer and model of every device), and with
that the problem becomes a crossword puzzle:

    1. sweep the blob collecting strings <glyph bytes><0x00>
    2. for each string, search the vocabulary for words of the same length
       that **match at every position already known**
    3. if **exactly one** candidate survives, its new letters are new mappings
    4. repeat: every learned glyph disambiguates more strings, up to a fixed
       point

Step 3 is the one that provides the guarantee: **if two candidates survive,
nothing is learned**. And every new mapping is checked against collision -- if
two different letters claim the same glyph, or one letter claims two, it is
discarded. A monoalphabetic substitution is either bijective or it is not.

Usage:
    python3 glyphs.py <blob.bin> <hub-config.json>
    python3 glyphs.py <blob.bin> <hub-config.json> --codificar "Philips TV"
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re

import fonts

#: Root of this repo (`glyphs.py` lives in `config_work/`).
_RAIZ = pathlib.Path(__file__).resolve().parent.parent

#: Environment variable that points at a Hub `DeviceList.json`.
ENV_DEVICELIST = "RE_HARMONY_HUB_DEVICELIST"

#: Where a clone is expected to drop its own `DeviceList.json`. This is the
#: path named in the "vocabulary not found" message, so the message doubles
#: as the instruction.
DEVICELIST_CONVENIDO = _RAIZ / "hub" / "DeviceList.json"


def devicelist_path() -> pathlib.Path:
    """Where to read a Hub `DeviceList.json` from, resolved at call time.

    A `DeviceList.json` is a **Harmony Hub account export**: it is the
    vocabulary that turns the remote's glyph strips into readable text and
    the independent oracle that names a command by its IR waveform. It is
    per-user data, it is not part of this repo, and it is not redistributed
    with it. So the path is RESOLVED, never hardcoded:

      1. `$RE_HARMONY_HUB_DEVICELIST`, if set (an explicit answer always wins);
      2. `<repo>/hub/DeviceList.json`, the conventional drop-in location;
      3. the first `account_export/output/*/resources/DeviceList.json` in the
         working tree, sorted so the pick is deterministic -- this is where
         the account bridge leaves its exports when it is installed.

    If none exists, `DEVICELIST_CONVENIDO` is returned ANYWAY, on purpose:
    every caller already guards with `.exists()` and degrades with a
    message, and a message naming `<repo>/hub/DeviceList.json` tells the
    reader exactly where to put the file. Returning `None` would only turn
    that into "(None)".

    Nothing here fails if the file is missing, and READING TEXT NO LONGER
    NEEDS IT: `BASE` is the complete 71-code table, taken from the blob's
    own fonts. What is lost without it is the second job -- the oracle that
    puts a NAME on a command from its IR waveform (`commands.hub_names`).
    """
    env_value = os.environ.get(ENV_DEVICELIST)
    if env_value:
        return pathlib.Path(env_value).expanduser()
    if DEVICELIST_CONVENIDO.exists():
        return DEVICELIST_CONVENIDO
    exportados = sorted(_RAIZ.glob("account_export/output/*/resources/DeviceList.json"))
    if exportados:
        return exportados[0]
    return DEVICELIST_CONVENIDO


#: Text for whoever gets an empty vocabulary. Kept next to the resolver so
#: the two cannot drift apart. It used to say the names would come out with
#: '?'; that is no longer true (`BASE` is complete and comes from the blob's
#: own fonts), and saying it anyway sent people hunting for an account
#: export to fix something the export does not fix.
TEXTO_SIN_DEVICELIST = (
    "No Hub DeviceList.json was found. Device and command names still read "
    "fine -- the glyph table is complete and comes out of the blob's own "
    "font section. What is missing is the vocabulary that puts a NAME on a "
    "command from its IR waveform: those stay unnamed.\n"
    "To fix that, drop an export of YOUR OWN Hub account at\n"
    "    %s\n"
    "or point %s at it. Nothing in this repo ships one: it is your data, "
    "not the project's." % (DEVICELIST_CONVENIDO, ENV_DEVICELIST)
)

# ==========================================================================
# THE TABLE IS COMPLETE: 71 of 71 codes, and it does NOT depend on the Hub.
#
# The table below (`CRIB_HISTORICA`) is what this file derived BY HAND
# against known command names: 40 codes. It is not wrong -- it is
# INCOMPLETE, and that is what put `?` on screen. `F`, `m`, `D`, `V`, `H`,
# `M`, `W`, `L`, `A`, `E`, `N`... simply were not in it, so `DVR`
# came out `?or?uler ??R` and any device the user added came out `??`. The
# hole was patched with the Hub's vocabulary (`hub/DeviceList.json`), which
# lets `extender()` learn 21 more codes by elimination -- but that file is
# the user's own account data, it is not published, and WITHOUT it the
# names came out mutilated.
#
# `fonts.py`'s `GLYPHS` closes it without any user data. It is derived from the
# blob itself: section [7] carries the BITMAP of every glyph in 18 fonts,
# and `fonts.py`'s `_check()` pins the 71 codes with four independent checks
# (shape of the 423 bitmaps, semantics of the factory texts, per-font
# repertoire, and the anchor "every code appears in at least one factory
# text"). That is a property of the FIRMWARE, not of a particular account.
#
# The two derivations are INDEPENDENT -- one reads names, the other reads
# pixels -- and they agree on the 40 codes they share. The assert below
# keeps them from drifting apart: if a future session touches either table
# and they stop agreeing, this file refuses to import instead of silently
# decoding one letter as another.
# ==========================================================================
BASE: dict[int, str] = dict(fonts.GLYPHS)

# The 27 that were already solved, plus the three that came out of the
# mislabeled entries in the old crib: `38 07 23 09 0e 25 2c 2c` is not "Power"
# but **"PowerOff"**, so 0x2c is the `f`. Kept as a REGRESSION GUARD, not as
# the source of truth: this is the crib that was measured against real names.
CRIB_HISTORICA = {
    0x02: "y",
    0x03: "S",
    0x04: "u",
    0x05: "n",
    0x07: "o",
    0x08: "T",
    0x09: "e",
    0x0B: "d",
    0x0C: "h",
    0x0E: "r",
    0x0F: "i",
    0x10: "a",
    0x11: "t",
    # **The digits and the colon**, from the blob's cleanest crib: there are 30
    # "System Message: N" strings stored in order, ten with one digit and
    # twenty with two, i.e. numbered **0..29**. That fixes them one by one, and
    # the two-digit ones confirm it on their own: `12 1c` = "10", `13 1c` =
    # "20", `13 1b` = "29". Glyph order does not follow ASCII (0x14 is `:` and
    # falls between the 2 and the 3), but the crib leaves no room for doubt.
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
    # Space, measured: 0x1E appears 409 times inside readable strings and 0x49
    # only 3. It comes from "The battery level is low", "USB Connected",
    # "Update in progress".
    0x1E: " ",
    0x1F: "b",
    0x20: "l",
    0x22: "s",
    0x23: "w",
    0x25: "O",
    0x26: "K",
    0x27: "U",
    0x29: "C",
    # `c`, not `R`: "OnScreen" = 25 05 03 2a 0e 09 09 05. `R` is 0x35, from
    # "Return" = 35 09 11 04 0e 05. Two glyphs cannot be the same letter.
    0x2A: "c",
    0x2B: "p",
    0x2C: "f",
    0x2D: "g",
    0x32: "I",
    0x35: "R",
    0x38: "P",
}

_DISCREPA = {
    "%#04x: crib says %r, fonts say %r" % (g, c, BASE.get(g))
    for g, c in CRIB_HISTORICA.items()
    if BASE.get(g) != c
}
assert not _DISCREPA, "the hand crib and the font table disagree: %s" % sorted(
    _DISCREPA
)
assert len(BASE) == 71 and len(set(BASE.values())) == 71, (
    "the glyph table is not the bijective 71-code one: %d codes, %d letters"
    % (len(BASE), len(set(BASE.values())))
)

#: Real range of glyph codes: 1..71 (`fonts.py`, `<u16 71><71 x ptr24>`,
#: index = code - 1). Anything outside is NOT a glyph, and `extender()` must
#: never learn one -- the old sweep accepted any byte below 0x60, so with a
#: complete table the only thing it could still "learn" was garbage.
COD_MIN = min(BASE)
COD_MAX = max(BASE)


def table_for(b: bytes) -> dict[int, str]:
    """The complete table for THIS blob: `BASE` on a One, the 82-code one
    (`fonts.GLYPHS_ONE_PLUS`) on a One+. Decided by the fonts' own pixels,
    see `fonts.glyphs_for()`."""
    t = fonts.glyphs_for(b)
    return dict(BASE) if t is fonts.GLYPHS else dict(t)


def vocabulario(cfg: str) -> set[str]:
    """Every plaintext name the Hub's config carries."""
    d = json.loads(pathlib.Path(cfg).read_text())
    palabras: set[str] = set()
    pila = [d]
    while pila:
        x = pila.pop()
        if isinstance(x, dict):
            for k in ("CommandTypeId", "Name", "Manufacturer", "Model", "Label"):
                v = x.get(k)
                if isinstance(v, str) and v:
                    palabras.add(v)
                    palabras.update(re.findall(r"[A-Za-z0-9]+", v))
            pila.extend(x.values())
        elif isinstance(x, list):
            pila.extend(x)
    return {p for p in palabras if 2 <= len(p) <= 32}


def cadenas(b: bytes, glyphs: set[int], min_len: int = 3):
    """[(offset, bytes)] of the <glyphs><0x00> strips in the blob."""
    out = []
    o = 0
    n = len(b)
    while o < n:
        if b[o] in glyphs:
            j = o
            while j < n and b[j] in glyphs:
                j += 1
            if j < n and b[j] == 0 and j - o >= min_len:
                out.append((o, b[o:j]))
            o = j + 1
        else:
            o += 1
    return out


def compatible(cru: bytes, palabra: str, table: dict) -> bool:
    """Whether the word fits what is already known about those glyphs."""
    if len(cru) != len(palabra):
        return False
    visto: dict[int, str] = {}
    used: dict[str, int] = {}
    for g, c in zip(cru, palabra):
        if g in table and table[g] != c:
            return False
        # within the same word the substitution also has to be bijective
        if visto.setdefault(g, c) != c or used.setdefault(c, g) != g:
            return False
    return True


def extender(b: bytes, vocab: set[str], table: dict | None = None, vueltas: int = 8):
    """Learns new glyphs by elimination. Returns (table, learned)."""
    completa = table_for(b)
    table = dict(table or completa)
    cod_min, cod_max = min(completa), max(completa)
    aprendidos = {}
    for _ in range(vueltas):
        # the candidate alphabet grows: any byte that shows up next to ones
        # already known could be a glyph still unresolved
        # The upper bound is the table's last code (0x47 on the One, 0x52 on
        # the One+), not an eyeballed 0x60: section [7] declares exactly 71
        # (or 82) slots, so anything above is not a glyph and
        # cannot be learned. With the table already complete this loop has
        # nothing left to learn and the only thing the old bound could still
        # admit was a byte that no font can draw.
        alfabeto = set(table) | {
            b[i]
            for i in range(1, len(b) - 1)
            if b[i - 1] in table and cod_min <= b[i] <= cod_max
        }
        nuevos = {}
        for _off, cru in cadenas(b, alfabeto):
            if all(g in table for g in cru):
                continue
            # **Guard against noise.** The sweep accepts any low byte, so it
            # picks up pixel data that looks like strings: of 1,828
            # candidates, 1,597 have no compatible word at all. Without this
            # filter, one of those makes a spurious match and the false glyph
            # enables more false glyphs -- which is how `0x49 = space` snuck
            # in (learned from "··gna") when the real space is `0x1E`,
            # measured at 409 occurrences against 3.
            conocidos = sum(1 for g in cru if g in table)
            if conocidos < 3 or conocidos / len(cru) < 0.6:
                continue
            cands = [p for p in vocab if compatible(cru, p, table)]
            if len(cands) != 1:
                continue  # ambiguous: nothing is learned
            for g, c in zip(cru, cands[0]):
                if g not in table:
                    if nuevos.setdefault(g, c) != c:
                        nuevos[g] = None  # two different readings: discarded
        # apply the ones that collide with nothing
        inv = {v: k for k, v in table.items()}
        change = False
        for g, c in nuevos.items():
            if c is None or g in table or c in inv:
                continue
            table[g] = c
            aprendidos[g] = c
            inv[c] = g
            change = True
        if not change:
            break
    return table, aprendidos


def codificar(text: str, table: dict) -> bytes | None:
    """The text as glyph indices, or None if some letter is missing."""
    inv = {v: k for k, v in table.items()}
    if any(c not in inv for c in text):
        return None
    return bytes(inv[c] for c in text) + b"\x00"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("config")
    ap.add_argument("--codificar")
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    vocab = vocabulario(a.config)
    print("Hub vocabulary: %d words" % len(vocab))
    print("starting glyphs:   %d" % len(BASE))

    table, nuevos = extender(b, vocab)
    print("glyphs after extending: %d  (+%d)" % (len(table), len(nuevos)))
    if nuevos:
        print(
            "  learned: %s"
            % ", ".join("%#04x=%r" % (g, c) for g, c in sorted(nuevos.items()))
        )

    # uppercase letters count: without them the report said "only the j is
    # missing" while it actually could not write "Go to Website" for the W
    alfabeto = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .:-/"
    absent = [c for c in alfabeto if c not in table.values()]
    print("\nstill unresolved: %s" % ("".join(absent) or "(none)"))

    if a.codificar:
        cod = codificar(a.codificar, table)
        print("\n%r ->" % a.codificar)
        if cod is None:
            inv = {v: k for k, v in table.items()}
            print(
                "  CANNOT: missing %r"
                % "".join(sorted({c for c in a.codificar if c not in inv}))
            )
            return 1
        print("  %s" % cod.hex(" "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
