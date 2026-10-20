#!/usr/bin/env python3
"""Generates a device's OWN screen (the one with its commands), by cloning the
STRUCTURE of a factory screen: the TV's own.

**It is not page 18.** `TRAZA_TV.md` called it that under the old indexing
(the one this very project refuted: "every screen is page N of [9]"). With the
correct model (`table[6][ordinal] -> TRAILER -> SLOT -> {KEYS, PROGRAM}`, see
`ESTADO.md`, section "Como se arma una pantalla") the TV's command screen is
**`table[6][142]`, sub-screen 1 (K=5)** -- verified right here by walking the
blob backwards from command objects 2385-2391, which `TRAZA_TV.md` did
identify correctly. Sub-screen 1 has the 7 fields in canonical order
`ab b2 b3 b0 b1 b4 b5`, each one bound to an object
`02 | {0x0FCA,0x75} | {command_object, 0x7f}`. Anchor check at startup: if
your blob does not match those exact bytes, the script aborts -- it does not
guess.

## The four pieces of a screen (see ESTADO.md / PLAN.md 2026-07-27)

    tabla[6][ordinal]  -> ptr24 to the TRAILER
    TRAILER             <flag u8><ptr24 HEADER><u16 N><N x ptr24 SLOT>
    SLOT (7 B)          <K u8><ptr24 KEYS><ptr24 PROGRAM>
    HEADER / KEYS       <u8 count><count x {u8 code, u16 operand, u8 class}>
    PROGRAM             bytecode for the 0x0295AC interpreter (13 opcodes)

`K` indexes section [19] (`<u8 33><33 x ptr24 template>`, each template
`<u8 N><N x ptr24 zone>`, 12 B zone `x0 w y0 h tag ptr24-selfpointer`).
A zone's key code is **`tag | 0x80`** (verified 205/205 in round 2 of that
same day; refutes round 1's reading through table 0x67). Touch->pixel
calibration, verified instruction by instruction:

    x_px = (raw_x - 765) * 176 / 3283
    y_px = 220 - (raw_y - 656) * 220 / 3896        (the Y axis is inverted)

## What this script builds

1. Anchors against `table[6][142]` sub-screen 1: confirms K, the KEYS count
   (7) and the codes `ab,b2,b3,b0,b1,b4,b5`. If it differs, it aborts.
2. Reads section [19][K] and **counts how many content zones it has** (all of
   them except the two universal paging strips `0xAE`/`0xAF`) -- that is the
   "check that the template has enough zones" the task asks for. If you ask
   for more commands than fit in one sub-screen, it spreads them over several
   (the firmware's automatic paging, same as `subscreen.py`).
3. For each command: two new objects on the heap (via
   `reubicar.device_objects`, already proven 198/198 on the null
   check): the command (`{cmd_id,0x7D}{dev_id,0x7C}`) and its button wrapper
   (`{0x0FCA,0x75}{command_id,0x7F}`). This is the only thing that was left
   to compose according to `ESTADO.md` / that day's three rounds: relocating
   `[9][10][11]` **and** adding an entry to `table[6]` in the same pass.
4. Builds KEYS (one per sub-screen), PROGRAM (calls a shared prologue that
   clears the screen and draws the title, and for each zone draws the
   command's label at its position, computed from the template's real
   geometry), HEADER (empty if there is a single sub-screen; with a link to
   `[11][242]`/`[11][244]` -- next/previous paging, the pattern proven by
   `subscreen.py` -- if there is more than one) and TRAILER.
5. Extends `table[6]` (156 -> 156+1 entries) by adding the new ordinal and
   repoints the master index (offset `0x0c+4*6`). `relocate.py` does not touch
   this section (it has the documented bug `N_SECCIONES=19` where it should be
   20 for [19]; [6] IS inside its range but its format -- with the padding
   byte -- differs from that of `table()`/`build_table()`, which are for
   [11]), so this piece is written here with its own reader/writer.
6. Verifies: the prior anchor, the zone count, and **two after-the-fact
   checks**: (a) `reubicar.chain()` before/after, identical -- nothing that
   already worked in the `[9]` model broke; (b) its own walk of the new screen
   confirming that every zone resolves to the `(cmd_id, dev_id)` or `page` it
   was asked for. Plus a byte check: below the original closing, **only** the
   closing pointer and the three/four master-index slots that are declared
   change.

## What it does NOT do (on purpose, and why that is not needed here)

It does not synthesize the commands' IR code (that is `synth_ir.py`, already
solved separately, verified 234/234). A new command ends up with a valid
`dev_id`/`cmd_id` reachable from the screen -- **the part that is proven by
looking** -- but with no IR record it still emits nothing when pressed. That
is the correct composition: screen first (verified with the eyes), IR after
(verified with `synth_ir.py` against the Hub's blob). Mixing the two into a
single tool violates the project's golden rule: every piece, its own check.

It writes nothing to the device. It never imports `write.py` nor any
libconcord primitive.

Usage:
    python3 screen_device.py backups/config_raw.bin \\
        --indice-dispositivo 3 --nombre Philips \\
        --comando Power --comando "Vol +" --comando "Vol -" \\
        --comando "Ch +" --comando "Ch -" --comando Mute --comando Menu \\
        --salida output/philips_pantalla.bin

    # with a back-to-the-Devices-menu button (optional, binds b0 to that page):
        --volver-a-pagina 74
"""

from __future__ import annotations

import argparse
import pathlib

import glyphs
import relocate

BASE = 0x040000


def midx_off(i: int) -> int:
    """Offset of master-index slot `i` (20 sections, 4 B each)."""
    return 0x0C + 4 * i


# --- the factory screen used as anchor and as template ------------------
ORD_TV = 142
SUB_TV = 1  # sub-screen (slot) with the full 7 fields

# Verified canonical order (ESTADO.md / PLAN.md): AB AC B2 B3 B0 B1 B4 B5.
CANONICAL_ORDER = (0xAB, 0xAC, 0xB2, 0xB3, 0xB0, 0xB1, 0xB4, 0xB5)
PAGING_STRIPS = (0x2E, 0x2F)  # tags of the two universal strips (AE/AF)
HEAP_NEXT = 242  # [11][242] = {4042,0x75}{0xFFA1,0x0F} -> next sub-screen
HEAP_PREV = 244  # [11][244] = {4042,0x75}{0xFFA0,0x0F} -> previous sub-screen

def u16(b: bytes, o: int) -> int:
    return int.from_bytes(b[o : o + 2], "little")


def u24(b: bytes, o: int) -> int:
    return int.from_bytes(b[o : o + 3], "little")


def u32(b: bytes, o: int) -> int:
    return int.from_bytes(b[o : o + 4], "little")


def px_x(v: int) -> float:
    return (v - 765) * 176 / 3283


def px_y(v: int) -> float:
    return 220 - (v - 656) * 220 / 3896


# --------------------------------------------------------------------------
# generic readers for the tabla[6] -> trailer -> slot -> record model


def read_record(b: bytes, o: int) -> list[tuple[int, int, int]]:
    """`<u8 count><count x {code u8, operand u16, class u8}>`."""
    n = b[o]
    return [
        (b[o + 1 + 4 * k], u16(b, o + 2 + 4 * k), b[o + 4 + 4 * k]) for k in range(n)
    ]


def read_trailer(b: bytes, o: int) -> dict:
    flag = b[o]
    hdr = u24(b, o + 1) - BASE
    n = u16(b, o + 4)
    slots = [u24(b, o + 6 + 3 * i) - BASE for i in range(n)]
    return {"flag": flag, "hdr": hdr, "n": n, "slots": slots}


def read_slot(b: bytes, o: int) -> dict:
    return {"K": b[o], "keys": u24(b, o + 1) - BASE, "programa": u24(b, o + 4) - BASE}


def read_table6(b: bytes) -> list[int]:
    """Absolute addresses (with BASE) of tabla[6]'s `count` trailers.

    Resolved **through the master index** (slot 6, `0x0c+4*6`), not through a
    fixed file offset: after `build_screen()` that slot no longer points at
    `0x01C699` but at the extended copy in the tail. Reading by fixed offset
    here would always give the old 156-entry table and the new ordinal (156)
    would be invisible to the checks.
    """
    addr = u32(b, midx_off(6)) - BASE
    n = u24(b, addr)  # the u24's high byte is the padding (u16 count + 1 pad)
    return [u24(b, addr + 3 + 3 * i) for i in range(n)]


def build_table6(addresses: list[int]) -> bytes:
    out = bytearray(len(addresses).to_bytes(2, "little") + b"\x00")
    for d in addresses:
        out += d.to_bytes(3, "little")
    return bytes(out)


def read_section19(b: bytes) -> dict[int, list[tuple[int, int, int, int, int]]]:
    """{K: [(tag, x0, w, y0, h), ...]} for the 33 templates.

    `reubicar.sections()` does not see this section (documented bug:
    `N_SECCIONES=19` when there are 20; the master-index slot for [19] is at
    `0x0c+4*19` and that range falls outside `range(19)`). It is read here
    straight from the master index.
    """
    sec19 = u32(b, midx_off(19)) - BASE
    n_tpl = b[sec19]
    tpl_ptrs = [u24(b, sec19 + 1 + 3 * i) - BASE for i in range(n_tpl)]
    out = {}
    for k, toff in enumerate(tpl_ptrs):
        nz = b[toff]
        zones = []
        for i in range(nz):
            zo = u24(b, toff + 1 + 3 * i) - BASE
            # check: the zone's self-pointer (+9, 3 B) has to point at the
            # zone itself -- that is the signature that tells this from noise.
            if u24(b, zo + 9) - BASE != zo:
                raise SystemExit(
                    "section [19]: self-pointer does not close at K=%d zone %d (%#08x)"
                    % (k, i, zo)
                )
            zones.append(
                (b[zo + 8], u16(b, zo), u16(b, zo + 2), u16(b, zo + 4), u16(b, zo + 6))
            )
        out[k] = zones
    return out


# --------------------------------------------------------------------------
# anchor: the TV's command screen, tabla[6][142] sub-screen 1


def check_anchor(b: bytes) -> tuple[int, dict[int, tuple[int, int, int, int]]]:
    """Confirms the reference structure and returns (K, {code: rect})."""
    entries = read_table6(b)
    if len(entries) < ORD_TV + 1:
        raise SystemExit("tabla[6] does not have %d entries" % (ORD_TV + 1))
    trailer = read_trailer(b, entries[ORD_TV] - BASE)
    if trailer["n"] <= SUB_TV:
        raise SystemExit(
            "anchor FAILED: tabla[6][%d] has %d sub-screen(s), expected > %d"
            % (ORD_TV, trailer["n"], SUB_TV)
        )
    slot = read_slot(b, trailer["slots"][SUB_TV])
    reg = read_record(b, slot["keys"])
    expected_codes = [0xAB, 0xB2, 0xB3, 0xB0, 0xB1, 0xB4, 0xB5]
    codes = [c for c, _, _ in reg]
    if codes != expected_codes:
        raise SystemExit(
            "anchor FAILED: tabla[6][%d] sub %d KEYS = %s, expected %s.\n"
            "This blob does not match the verified reference; nothing is generated."
            % (
                ORD_TV,
                SUB_TV,
                [hex(c) for c in codes],
                [hex(c) for c in expected_codes],
            )
        )
    print(
        "anchor OK: tabla[6][%d] sub-screen %d, K=%d, KEYS=%s"
        % (ORD_TV, SUB_TV, slot["K"], [hex(c) for c in codes])
    )

    zones19 = read_section19(b)
    zones_k = zones19[slot["K"]]
    rects = {}
    for tag, x0, w, y0, h in zones_k:
        code = tag | 0x80
        xa, xb = sorted((px_x(x0), px_x(x0 + w)))
        ya, yb = sorted((px_y(y0), px_y(y0 + h)))
        rects[code] = (xa, ya, xb, yb)
    for c in expected_codes:
        if c not in rects:
            raise SystemExit(
                "anchor FAILED: template K=%d has no zone for %#04x" % (slot["K"], c)
            )
    print(
        "section [19][%d]: %d zones total (%d of content, %d of paging)"
        % (
            slot["K"],
            len(zones_k),
            len(zones_k) - sum(1 for t, *_ in zones_k if t in PAGING_STRIPS),
            sum(1 for t, *_ in zones_k if t in PAGING_STRIPS),
        )
    )
    return slot["K"], rects


# --------------------------------------------------------------------------
# text


def glyph_table(b: bytes) -> dict[int, str]:
    """The 71 codes. `glyphs.BASE` already IS the whole table.

    This used to patch `glyphs.BASE` with `EXTRA_GLYPHS` (D, v, V, F, H, m,
    A, re-typed here because glyphs.py was "not part of this task") and then
    run `glyphs.extender()` against a hand-written `COMMAND_VOCAB` to guess
    the rest. Both were workarounds for an incomplete `glyphs.BASE`; a
    fourth copy of the same table is exactly how one of them drifts.
    On a One+ it is that remote's table instead (`glyphs.table_for`).
    """
    return glyphs.table_for(b)


def encode(text: str, table: dict[int, str]) -> bytes:
    cod = glyphs.codificar(text, table)
    if cod is None:
        inv = {v: k for k, v in table.items()}
        missing = sorted({c for c in text if c not in inv})
        raise SystemExit(
            "cannot encode %r: no glyphs for %r" % (text, "".join(missing))
        )
    return cod


# --------------------------------------------------------------------------
# building the new screen


def build_screen(
    b: bytes,
    device_index: int,
    name: str,
    labels: list[str],
    back_to: int | None,
) -> bytes:
    if not 0 <= device_index <= 0xFF:
        raise SystemExit("--indice-dispositivo must be 0..255")
    if not labels:
        raise SystemExit("at least one --comando is needed")
    if back_to is not None and not 0 <= back_to < len(read_table6(b)):
        raise SystemExit(
            "--volver-a-pagina %d out of range (0..%d)"
            % (back_to, len(read_table6(b)) - 1)
        )

    K, rects = check_anchor(b)
    text_table = glyph_table(b)
    ir_type = relocate.ir_type(b)  # 0x0FCA, read from the blob (not assumed)

    content_order = [c for c in CANONICAL_ORDER if c in rects]
    if back_to is not None:
        order_without_back = [c for c in content_order if c != 0xB0]
    else:
        order_without_back = content_order
    capacity = len(order_without_back)
    print(
        "template K=%d: %d content zones available per sub-screen (%s)%s"
        % (
            K,
            capacity,
            [hex(c) for c in order_without_back],
            "  (+ 0x7E back on b0, on every sub-screen)" if back_to is not None else "",
        )
    )
    if capacity == 0:
        raise SystemExit("template K=%d has no content zones" % K)

    n_commands = len(labels)
    n_sub = -(-n_commands // capacity)  # ceil
    print(
        "%d command(s) -> %d sub-screen(s) of up to %d each"
        % (n_commands, n_sub, capacity)
    )

    sec = relocate.sections(b)
    id_base = len(relocate.table(b, sec[11][0]))

    dev_id = (device_index << 8) | 0x01
    bodies, ids_a = relocate.device_objects(device_index, n_commands, id_base, ir_type)
    # The back button needs NO heap object: a KEYS entry with class 0x7E
    # carries the page ordinal **directly** as its operand -- 11/11 factory
    # cases verified that way (round 2, 27/07/2026). Adding a
    # `navigation_object` here would be one indirection too many: the operand
    # would end up pointing at a tabla[11] index, not at the page.

    a10, z10 = sec[10]
    s10 = bytearray(b[a10:z10])
    offs = []
    for c in bodies:
        offs.append(len(s10))
        s10 += c

    print(
        "new heap objects: %d commands (id %d..%d) + %d button wrappers (id %d..%d)%s"
        % (
            n_commands,
            id_base,
            id_base + 2 * n_commands - 2,
            n_commands,
            id_base + 1,
            id_base + 2 * n_commands - 1,
            "  + back button (no object, tag 0x7E direct) -> page %d" % back_to
            if back_to is not None
            else "",
        )
    )

    # --- step A: relocate [9] (untouched) / [10] (extended) / [11] (extended) ---
    blob_a = relocate.relocate(b, {10: bytes(s10)}, objetos_extra=offs)

    # regression check: the [9] model (plain pages, not tabla[6]) must not
    # change one bit for any pre-existing button.
    before = relocate.chain(b)
    after = relocate.chain(blob_a)
    if before != after:
        raise SystemExit(
            "REGRESSION: relocating [9][10][11] changed %d pre-existing chain(s)"
            % len(
                set(before) ^ set(after)
                | {k for k in before if before[k] != after.get(k)}
            )
        )
    print(
        "regression check (reubicar.chain): %d pre-existing buttons, identical"
        % len(before)
    )

    # --- step B: add the new screen + extend tabla[6] ---
    closing = u24(blob_a, 4) - BASE
    out = bytearray(blob_a[: closing - 2])

    def emit(blk: bytes) -> int:
        at = len(out)
        out.extend(blk)
        return at

    title = encode(name, text_table)
    off_title = emit(title)

    encoded_labels = [encode(e, text_table) for e in labels]
    off_labels = [emit(e) for e in encoded_labels]

    def p(off: int) -> bytes:
        return (off + BASE).to_bytes(3, "little")

    def op01_clear() -> bytes:
        return bytes([0x01, 0, 0, 176, 220]) + (0).to_bytes(2, "little")

    def op10(style: int) -> bytes:
        return bytes([0x10, style])

    def op04(x: int, y: int, ptr_off: int) -> bytes:
        return bytes([0x04, x & 0xFF, y & 0xFF]) + p(ptr_off)

    def op16(ptr_off: int) -> bytes:
        return bytes([0x16]) + p(ptr_off)

    prologue = op01_clear() + op10(6) + op04(6, 4, off_title) + bytes([0x17])
    off_prologue = emit(prologue)

    # "Devices" is the same label the factory back softkey already uses
    # (TRAZA_TV.md / tabla[6][142]) -- an authentic convention, not invented,
    # and its glyphs are already covered by glifos.BASE.
    off_back = emit(encode("Devices", text_table)) if back_to is not None else None

    new_slots = []
    idx_cmd = 0
    for s in range(n_sub):
        chunk = order_without_back[: min(capacity, n_commands - idx_cmd)]
        prog = op16(off_prologue)
        if n_sub > 1:
            ind = encode("%d of %d" % (s + 1, n_sub), text_table)
            off_ind = emit(ind)
            prog += op10(7) + op04(13, 18, off_ind)
        record_entries = []
        for code in chunk:
            x, y, _, _ = rects[code]
            tx, ty = max(0, min(170, round(x) + 3)), max(4, min(214, round(y) + 4))
            prog += op10(9) + op04(tx, ty, off_labels[idx_cmd])
            record_entries.append((code, ids_a[idx_cmd], 0x7F))
            idx_cmd += 1
        if back_to is not None:
            x, y, _, _ = rects[0xB0]
            tx, ty = max(0, min(170, round(x) + 3)), max(4, min(214, round(y) + 4))
            prog += op10(8) + op04(tx, ty, off_back)
            record_entries.append((0xB0, back_to, 0x7E))
        prog += bytes([0x00])
        off_prog = emit(prog)

        reg9 = bytes([len(record_entries)])
        for code, operand, klass in record_entries:
            reg9 += bytes([code]) + relocate.slot(operand, klass)
        off_reg9 = emit(reg9)

        off_slot = emit(bytes([K]) + p(off_reg9) + p(off_prog))
        new_slots.append(off_slot)

    if n_sub > 1:
        hdr = (
            bytes([2])
            + bytes([0xAF])
            + relocate.slot(HEAP_NEXT, 0x7F)
            + bytes([0xAE])
            + relocate.slot(HEAP_PREV, 0x7F)
        )
    else:
        hdr = bytes([0])
    off_hdr = emit(hdr)

    trailer_body = bytes([0]) + p(off_hdr) + len(new_slots).to_bytes(2, "little")
    for so in new_slots:
        trailer_body += p(so)
    off_trailer = emit(trailer_body)
    addr_trailer = off_trailer + BASE

    table6 = read_table6(blob_a)
    new_ordinal = len(table6)
    table6.append(addr_trailer)
    off_table6 = emit(build_table6(table6))
    addr_table6 = off_table6 + BASE

    out[midx_off(6) : midx_off(6) + 4] = addr_table6.to_bytes(4, "little")

    if len(out) % 2:
        out += b"\x00"
    nc = len(out) + 2
    out += b"\x00\x00" + b"PTYY"
    out[4:7] = (BASE + nc).to_bytes(3, "little")
    lo, hi = 0x21, 0x43
    for k in range(0, nc - 2, 2):
        lo ^= out[k]
        hi ^= out[k + 1]
    out[nc - 2] = lo
    out[nc - 1] = hi
    final = bytes(out)

    # --- final checks ---
    check_new_screen(final, new_ordinal, labels, dev_id, back_to)
    check_original_bytes(b, final)

    print("\nnew ordinal in tabla[6]: %d" % new_ordinal)
    print("blob: %d -> %d B  (+%d)" % (len(b), len(final), len(final) - len(b)))
    return final


def check_new_screen(
    final: bytes, ordinal: int, labels: list[str], dev_id: int, back_to: int | None
) -> None:
    entries = read_table6(final)
    trailer = read_trailer(final, entries[ordinal] - BASE)
    resolved = 0
    for so in trailer["slots"]:
        slot = read_slot(final, so)
        for code, operand, klass in read_record(final, slot["keys"]):
            if klass == 0x7E:
                if operand != back_to:
                    raise SystemExit(
                        "check FAILED: back button on %#04x points at page %d, not %d"
                        % (code, operand, back_to)
                    )
                continue
            # the operand is a tabla[11] index:
            # {type,0x75}{cmd_id,0x7F} -> {cmd_id,0x7D}{dev_id,0x7C}
            block = relocate.table(final, relocate.sections(final)[11][0])
            wrapper = block[operand]
            n = final[wrapper]
            atoms = [
                (u16(final, wrapper + 1 + 3 * k), final[wrapper + 3 + 3 * k])
                for k in range(n)
            ]
            id_cmd = next(v for v, op in atoms if op == 0x7F)
            cd = block[id_cmd]
            n2 = final[cd]
            atoms2 = [
                (u16(final, cd + 1 + 3 * k), final[cd + 3 + 3 * k]) for k in range(n2)
            ]
            if not any(op == 0x7D for _, op in atoms2):
                raise SystemExit(
                    "check FAILED: button %#04x has no {cmd_id,0x7D} atom" % code
                )
            dev_val = next(v for v, op in atoms2 if op == 0x7C)
            if dev_val != dev_id:
                raise SystemExit(
                    "check FAILED: button %#04x resolves to dev_id %#06x, expected %#06x"
                    % (code, dev_val, dev_id)
                )
            resolved += 1
    if resolved != len(labels):
        raise SystemExit(
            "check FAILED: %d command buttons resolved, expected %d"
            % (resolved, len(labels))
        )
    print(
        "new-screen check: %d/%d commands resolve to dev_id %#06x"
        % (resolved, len(labels), dev_id)
    )


def check_original_bytes(original: bytes, final: bytes) -> None:
    """Everything that changes below the old closing has to be declared."""
    old_closing = u24(original, 4) - BASE
    allowed = set(range(4, 7))
    for i in (6, 9, 10, 11):
        allowed |= set(range(midx_off(i), midx_off(i) + 4))
    diff = [i for i in range(old_closing - 2) if original[i] != final[i]]
    extra = sorted(set(diff) - allowed)
    if extra:
        raise SystemExit(
            "check FAILED: %d bytes of the original body changed undeclared (e.g. %s)"
            % (len(extra), [hex(x) for x in extra[:10]])
        )
    print(
        "byte check: %d changed below the old closing, all declared (%s)"
        % (
            len(diff),
            ", ".join(
                "%#04x" % x for x in sorted(set(o for o in diff if o >= 0x24) & allowed)
            ),
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    # The CLI flags stay in Spanish on purpose: they are this project's
    # cross-process contract (see the note in app/generate.py), and renaming one
    # breaks callers silently.
    ap.add_argument(
        "--device-index",
        type=int,
        required=True,
        help="0..255, must not collide with an existing one (TV=0, DVR=1, Home=2)",
    )
    ap.add_argument("--name", required=True)
    ap.add_argument(
        "--command",
        action="append",
        default=[],
        dest="commands",
        help="repeatable; one label per button, up to 7 per sub-screen",
    )
    ap.add_argument(
        "--back-to-page",
        type=int,
        default=None,
        help="tabla[6] ordinal the b0 button links to (optional; navigation, not a command)",
    )
    ap.add_argument("--salida")
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    final = build_screen(
        b, a.device_index, a.name, a.commands, a.back_to_page
    )

    if a.salida:
        pathlib.Path(a.salida).write_bytes(final)
        print("\nwritten %s" % a.salida)
        print("to write it to the remote (write.py, a human decides) declare:")
        print(
            "   --repunta 0x24 --repunta 0x25   (master index section [6], 4 B: 0x24..0x27;"
        )
        print(
            "                                     --repunta covers 3 B each, two overlapping"
        )
        print(
            "                                     declarations cover all 4 -- see grabar.nada_se_movio)"
        )
        print(
            "   (offsets 0x30..0x3b -- sections [9][10][11] -- are already in PERMITIDOS by default)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
