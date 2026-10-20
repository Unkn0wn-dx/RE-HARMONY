#!/usr/bin/env python3
"""Lists the devices declared in a Harmony One config blob.

Per device: name, section [5] index (`k1`), command count, ordinal of its
commands screen, and which menu sheet/row of the Devices menu it appears
in.

READS ONLY. Writes nothing, doesn't touch USB. Reuses the model already
verified on the device (`fourth_device.py`, `add_device.py`, `relocate.py`)
instead of reinventing it:

    menu (tabla[6][74/90/141], 3 replicas of the same menu -- ESTADO.md) ->
      N sheets (trailer <flag><header ptr24><u16 N><N x slot ptr24>) ->
        up to 3 rows per sheet, drawn at Y = 57, 111, 165
    row -> touch zone {id, 0x7F} in the sheet's key register ->
      section [11] object: {tipo,0x75} {ordinal,0x7E} {1,0x9A}
      -- the same pattern `device.jump_object()` emits
    ordinal -> tabla[6][ordinal] IS the device's commands screen
    screen -> its buttons resolve, with the SAME double jump
      `reubicar.chain()` walks ({0x7F} -> {0x7F} -> {cmd_id,0x7D}), to a
      cmd_id whose HIGH BYTE is `k1` -- the section [5] device index
      (add_device.py, `cmd_setup_ir`: "k1 = high byte of cmd_id")
    command count = `device.read_section5(b)[k1]['n']` -- the same
      number `device.check_section5()` already checks byte for byte
      against the factory blob (236/236: 84+62+90)

Names are decoded with `glyphs.py` (glyph indices, not ASCII), extending
the base table with the Hub's vocabulary if available.

GOLDEN CHECK: the 3 menu objects (74/90/141) have to return EXACTLY the
same set and order of devices -- if not, abort. And no screen may resolve
0 or >1 `k1` values -- if it does, abort.

## What is NOT an error: a `k1` declared but not referenced

Section [5]'s header can declare MORE devices than the menu reaches. That
is exactly what `delete_device.py` leaves behind, and it is the CORRECT state
after a removal: deleting unhooks the menu row and leaves section [5]'s
sub-table ORPHANED in place, because pulling it out of the middle would
renumber the `k1` of everything that follows, and the firmware
(`cmd_setup_ir`) walks that table WITHOUT a range check -- a `k2` outside
the sub-table HANGS the remote. So here the orphans are REPORTED
(`huerfanos_seccion5`), not treated as "the reader is broken": an earlier
version of this file aborted with "section [5] declares k1 in
[0,1,2,3,4] but the menu resolves [0,1,2,3]", i.e. it refused to list
exactly the blob a removal produces.

Usage:
    python3 list_devices.py <blob.bin> [--json out.json] [--hub <DeviceList.json>]

NOTE ON NAMING: `set_t6`, `make_decoder`, `k1_of_screen`,
`menu_rows`, `DEFAULT_HUB`, and `K1_DE_FABRICA` keep their exact
Spanish names -- `activities.py`, `delete_device.py`, `screen_activities.py`,
and `keys_map.py` all `import listar` directly and call them by name.
The dict keys these functions return/consume (`name`, `k1`,
`incomplete_glyphs`, `screen_ordinal`, `menu_ordinal`, `sheet`, `row`,
`zone`, `commands`, `de_fabrica`, `menu`, `ordinal_menu`, `devices`,
`huerfanos_seccion5`, `declarados_seccion5`, `menus`, `glyph_warning`) are
ALSO left in Spanish: this script's `--json` output is read by
`app/api.py` (`control_listar_dispositivos`) by these exact names and
flows on to `app/ui/app.js`. Everything else (comments, docstrings, and
every other local name) was translated freely.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

import add_device as D
import glyphs
import relocate

#: Resolved by `glyphs.devicelist_path()` (env var, then `<repo>/hub/`, then
#: an account-bridge export), never a path baked into this file.
#:
#: Names NO LONGER DEPEND ON IT. `glyphs.BASE` is the complete 71-code table
#: read out of the blob's own font section [7]. This used to be load-bearing:
#: with the vocabulary missing (or with a cwd-relative default that silently
#: resolved to nothing) the names came out "T?", "?or?uler ??R", "?o?e", "??"
#: with exit 0 -- and a delete-confirmation UI offering to remove "??" is
#: dangerous. What the file is used for now is a CROSS-CHECK: if it is there,
#: the table it derives by elimination has to agree with the font one.
DEFAULT_HUB = str(glyphs.devicelist_path())

#: devices that shipped from the factory in THIS project's remote (TV /
#: DVR / Home). They are section [5]'s `k1` 0..2, the ones living
#: on menu SHEET 1.
#:
#: NOT a format constant -- it's per-PHYSICAL-REMOTE data, hardcoded here.
#: It happens to equal menu sheet 1's row CAPACITY (`MAX_ROWS_PER_SHEET` in
#: `add_device.py`) only because this particular remote's factory devices
#: exactly filled sheet 1; that's a coincidence of this unit's factory
#: config, not something the format guarantees. And it can't be re-derived
#: from a blob AFTER devices have been added: once section [5] has more
#: than 3 sub-tables, nothing in the blob itself says which prefix was
#: there from the factory -- that fact only exists at first contact, before
#: any addition. The correct fix is to record it once per remote and thread
#: it through as a parameter instead of trusting this module-level default:
#: `config_work/read_flash_baseline.py::derivar()` measures
#: `n_dispositivos_actual` and flags `parece_de_fabrica`, and
#: `app/history.py::identify_or_create_remote()` only writes
#: `mandos.n_dispositivos_fabrica` when that flag is True. (The names matter:
#: an earlier version called the measurement `n_dispositivos_fabrica` and
#: took it from ANY dump, which on this remote's own already-written config
#: reports 5 devices / 158 screens as "factory" instead of 3 / 156.)
#: Not done here: `list_devices.py`/`delete_device.py`/`screen_activities.py`/
#: `keys_map.py` all read this name directly, and re-plumbing every call
#: site is real surgery on the load-bearing deletion path -- left as the
#: next step (see the audit this constant is part of), not attempted
#: without a live remote to verify against.
K1_DE_FABRICA = 3


def set_t6(b: bytes) -> int:
    """The master index entry, not the factory constant -- works for any
    already-relocated blob (same reason `device.main()` documents)."""
    t6 = D.u24(b, D.MAESTRO_T6) - D.BASE
    if not 0 <= t6 < len(b) - 3:
        raise SystemExit(
            "the master index at %#04x doesn't point to a valid tabla[6]" % D.MAESTRO_T6
        )
    D.T6 = t6
    return t6


def make_decoder(b: bytes, hub: str | None):
    """Returns `(decode, warning)`. `decode(ptr) -> (text, complete)`.

    `glyphs.BASE` is the COMPLETE table -- 71 of 71 codes, read out of the
    blob's own font section [7] -- so no account data is needed to read a
    name. The Hub's vocabulary is still accepted, and when it is there it
    is used as a CROSS-CHECK: `glyphs.extender()` re-derives the table by
    elimination against real words, and if that reading contradicts the
    font one, that is said out loud instead of picking a winner in silence.

    Two things this used to get wrong, both now fixed:

      * `complete` was `"?" not in txt`. `0x3E` IS the question mark (it is
        in factory strings like "Is the TV on?"), so a name that legitimately
        contained one was flagged "not fully readable". It is now decided by
        CODE: a name is complete when every one of its bytes is in the table.
      * a missing `DeviceList.json` warned that names would come out with
        `?`. It no longer causes that, so the warning no longer fires for it.
        What DOES deserve a warning is a byte outside the 71 codes, which is
        not a missing vocabulary but a bad pointer -- see `_AVISO_CODIGO`.
    """
    table = glyphs.table_for(b)
    warning = None

    if hub and pathlib.Path(hub).exists():
        contra, _ = glyphs.extender(b, glyphs.vocabulario(hub))
        choca = sorted(
            "%#04x: words say %r, fonts say %r" % (g, c, table.get(g))
            for g, c in contra.items()
            if table.get(g) != c
        )
        if choca:
            warning = (
                "the glyph table read from the fonts and the one the Hub's "
                "vocabulary derives DISAGREE: %s. The font one is used (it is "
                "the one `fonts.py` checks against the factory texts), but "
                "one of the two is wrong and the names may be misread."
                % "; ".join(choca)
            )
            print("WARNING: " + warning, file=sys.stderr)

    sin_tabla: set[int] = set()

    def decode(ptr: int) -> tuple[str, bool]:
        end = b.index(b"\x00", ptr)
        crudo = b[ptr:end]
        sin_tabla.update(c for c in crudo if c not in table)
        return "".join(table.get(c, "?") for c in crudo), all(c in table for c in crudo)

    decode.sin_tabla = sin_tabla  # type: ignore[attr-defined]
    decode.table = table  # type: ignore[attr-defined]
    return decode, warning


#: What to say when a name carries a byte that is not one of the 71 glyphs.
#: It is NOT "the vocabulary is missing": the table is complete. A byte
#: outside 0x01..0x47 means the text pointer landed somewhere that is not
#: text.
_AVISO_CODIGO = (
    "a name carries byte(s) %s, which are NOT glyphs (section [7] declares "
    "a fixed set of codes: 0x01..0x47 on the One, 0x01..0x52 on the One+). "
    "That is not a missing vocabulary: it is "
    "a text pointer that does not land on text. Those characters come out "
    "as '?'."
)


#: Offset of each line of a name split over two lines, from the row's text
#: line (One+: a two-line name at Y 51 and 63 for a row at 57).
WRAP_DY = 6


def decode_line(decode, ptr: int | None, inline: bytes | None) -> tuple[str, bool]:
    """One text line of a menu row through `decode`: by pointer (TXT) or
    from its inline glyphs (TXTIN), with the same table and the same
    completeness rule."""
    if inline is None:
        return decode(ptr)
    table = decode.table
    decode.sin_tabla.update(c for c in inline if c not in table)
    return "".join(table.get(c, "?") for c in inline), all(c in table for c in inline)


def row_ordinal(b: bytes, dest11: list[int], ident: int) -> int | None:
    """`{id,0x7F}` -> object in `[11]` -> `{ordinal,0x7E}`. The slot
    `device.jump_object()` emits and that the device already
    executes to open a device's screen when its row is touched."""
    if not 0 <= ident < len(dest11):
        return None
    rs = D._slots(b, dest11[ident])
    if not rs:
        return None
    return next((v for v, cl in rs if cl == 0x7E), None)


def k1_of_screen(b: bytes, dest11: list[int], ordinal: int) -> set[int]:
    """Walks ALL of `table[6][ordinal]`'s sheets and returns the set of
    `k1` (cmd_id's high byte) its buttons resolve to -- the same double
    jump `{0x7F} -> {0x7F} -> {cmd_id,0x7D}` that `reubicar.chain()` uses
    (there split into `tg`/`t2`; here over ONE specific screen, not the
    whole blob, because what's needed is *which* device it is, not the
    full button->command chain)."""
    t = D.u24(b, D.T6 + 3 + 3 * ordinal) - D.BASE
    tr = D.read_trailer(b, t, max_n=200)
    if tr is None:
        return set()
    ks: set[int] = set()
    for sp in tr["slots"]:
        s = D.read_slot(b, sp - D.BASE)
        if s is None:
            continue
        kr = D.read_key_register(b, s["keyreg"] - D.BASE) or []
        for _cod, ident, category in kr:
            if category != 0x7F or not 0 <= ident < len(dest11):
                continue
            rs1 = D._slots(b, dest11[ident]) or []
            for v, t2 in rs1:
                if t2 == 0x7D:
                    ks.add(v >> 8)
                elif t2 == 0x7F and 0 <= v < len(dest11):
                    rs2 = D._slots(b, dest11[v]) or []
                    for v2, t3 in rs2:
                        if t3 == 0x7D:
                            ks.add(v2 >> 8)
    return ks


def menu_rows(
    b: bytes,
    ordinal_menu: int,
    decode,
    dest11: list[int],
    zones19: dict[int, list],
) -> list[dict]:
    """All the rows (name, sheet, row, screen ordinal, k1) of ONE menu
    object, walking ALL of its sheets.

    `fourth_device.py` only looks at sheet 1 (that's why `cuarto.entradas_de` is
    enough for the factory blob, with 3 devices = 1 sheet): a 4th/5th
    device falls on sheet 2, which ESTADO.md documents ("paginated
    menu... 3 rows per sheet") but `fourth_device.py` never read. Here `tr['slots']`
    is walked in full, with the same trailer/slot reader `add_device.py`
    already uses.
    """
    t = D.u24(b, D.T6 + 3 + 3 * ordinal_menu) - D.BASE
    tr = D.read_trailer(b, t, max_n=200)
    if tr is None:
        raise SystemExit("tabla[6][%d] doesn't parse as a menu screen" % ordinal_menu)

    out = []
    for h, sp in enumerate(tr["slots"], start=1):
        s = D.read_slot(b, sp - D.BASE)
        if s is None:
            raise SystemExit(
                "sheet %d of tabla[6][%d]: invalid slot" % (h, ordinal_menu)
            )
        ins = D.disassemble(b, s["prog"] - D.BASE)
        # A row's name is a pointer TXT on the row's text line, or -- as the
        # One+ firmware draws them -- an inline TXTIN, and a name too long
        # for one line is split into two lines WRAP_DY above and below it
        # (Y 51 and 63 around a row at 57). Lines are grouped
        # by row, top to bottom, and the row's name is their join.
        by_row: dict[int, list] = {}
        for _o, op, ar in ins:
            if op not in ("TXT", "TXTIN") or ar[0] != D.TAG_NAME:
                continue
            row_i, rem = divmod(ar[1] - D.Y_ROW_0 - 19 + WRAP_DY, D.ROW_STEP)
            if rem not in (0, WRAP_DY, 2 * WRAP_DY) or not 0 <= row_i < 8:
                continue
            by_row.setdefault(row_i, []).append(
                (ar[1], ar[2] if op == "TXT" else None, ar[2] if op == "TXTIN" else None)
            )
        text_rows = [sorted(by_row[r]) for r in sorted(by_row)]

        kr = D.read_key_register(b, s["keyreg"] - D.BASE) or []
        zones = zones19.get(s["K"], [])
        order = D.template_buttons(zones)  # top -> bottom, no footer
        by_code = {cod: ident for cod, ident, category in kr if category == 0x7F}
        zone_entries = [(c, by_code[c]) for c in order if c in by_code]

        if len(zone_entries) != len(text_rows):
            raise SystemExit(
                "sheet %d of tabla[6][%d]: %d text rows against %d row "
                "zones -- mismatch, the reader is wrong (names %s, zones %s)"
                % (
                    h,
                    ordinal_menu,
                    len(text_rows),
                    len(zone_entries),
                    text_rows,
                    zone_entries,
                )
            )

        for row, (lines, (cod, ident)) in enumerate(
            zip(text_rows, zone_entries), start=1
        ):
            screen_ordinal = row_ordinal(b, dest11, ident)
            if screen_ordinal is None:
                raise SystemExit(
                    "sheet %d row %d of tabla[6][%d]: zone %#04x (id %d) "
                    "doesn't resolve {ordinal,0x7E}"
                    % (h, row, ordinal_menu, cod, ident)
                )
            ks = k1_of_screen(b, dest11, screen_ordinal)
            if len(ks) != 1:
                raise SystemExit(
                    "screen %d (sheet %d row %d of tabla[6][%d]): resolves %d "
                    "k1 values (%s), it had to be exactly 1"
                    % (
                        screen_ordinal,
                        h,
                        row,
                        ordinal_menu,
                        len(ks),
                        sorted(ks),
                    )
                )
            parts = [decode_line(decode, ptr, inline) for _y, ptr, inline in lines]
            name = " ".join(p for p, _ok in parts)
            name_complete = all(ok for _p, ok in parts)
            out.append(
                {
                    "name": name,
                    "incomplete_glyphs": not name_complete,
                    "k1": next(iter(ks)),
                    "screen_ordinal": screen_ordinal,
                    "menu_ordinal": ordinal_menu,
                    "sheet": h,
                    "row": row,
                    "zone": "0x%02X" % cod,
                }
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--json")
    ap.add_argument("--hub", default=DEFAULT_HUB)
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    if b[:4] != b"GSPM":
        raise SystemExit("the blob doesn't start with GSPM")

    set_t6(b)
    decode, glyph_warning = make_decoder(b, a.hub)

    sec = relocate.sections(b)
    dest11 = relocate.table(b, sec[relocate.OBJECT_TABLE][0])
    zones19 = D.read_section19(b)

    # the tabla[6] objects that carry the device list -- same criterion as
    # `fourth_device.py` (>=3 name rows with the group-54 step), here via
    # `device.menu_objects`, which also resolves ALL live sheets,
    # not just the first.
    candidates = [o["ordinal"] for o in D.menu_objects(b)]
    if not candidates:
        raise SystemExit("no menu object with devices was found")

    by_menu = {o: menu_rows(b, o, decode, dest11, zones19) for o in candidates}

    # GOLDEN CHECK: the N menu replicas have to match exactly on
    # (name, k1, ordinal_pantalla), in the same order.
    reference = by_menu[candidates[0]]

    def key(rows):
        return [(f["name"], f["k1"], f["screen_ordinal"]) for f in rows]

    ref_key = key(reference)
    out_of_sync = [o for o in candidates[1:] if key(by_menu[o]) != ref_key]
    # On the One+ the replicas are separate screens (opened from the main
    # screen, from Devices, from inside an activity) and one of them may list
    # the same devices in another ORDER. There the check is on the set --
    # same name, k1 and screen in every replica -- and the order reported is
    # the one most replicas agree on. The One keeps the strict check.
    if out_of_sync and glyphs.fonts.is_one_plus(b):
        if all(sorted(key(by_menu[o])) == sorted(ref_key) for o in candidates):
            orders = collections.Counter(tuple(key(by_menu[o])) for o in candidates)
            best = orders.most_common(1)[0][0]
            reference = next(by_menu[o] for o in candidates if tuple(key(by_menu[o])) == best)
            print(
                "note: menu objects %s list the same devices in another order"
                % [o for o in candidates if tuple(key(by_menu[o])) != best],
                file=sys.stderr,
            )
            out_of_sync = []
    if out_of_sync:
        raise SystemExit(
            "the menu objects don't match: %s differ from %s (%s vs %s)"
            % (out_of_sync, candidates[0], by_menu[out_of_sync[0]], reference)
        )

    devs5 = D.read_section5(b)
    k1_expected = set(range(len(devs5)))
    k1_found = [f["k1"] for f in reference]

    # THIS one IS still a reader error: two menu rows resolving to the
    # SAME device, or a k1 that section [5] doesn't even declare.
    repeated = sorted({k for k in k1_found if k1_found.count(k) > 1})
    if repeated:
        raise SystemExit(
            "two or more menu rows resolve to the same device (k1 %s): "
            "the reader is wrong" % repeated
        )
    outside = sorted(set(k1_found) - k1_expected)
    if outside:
        raise SystemExit(
            "the menu resolves k1 %s that section [5] doesn't declare "
            "(it declares 0..%d): the reader is wrong" % (outside, len(devs5) - 1)
        )

    # THIS is NOT an error: a declared k1 the menu doesn't reach. It's what
    # `delete_device.py` leaves on purpose -- see the docstring above.
    orphans = sorted(k1_expected - set(k1_found))

    devices = []
    for f in sorted(reference, key=lambda f: f["k1"]):
        devices.append(
            {
                "name": f["name"],
                "incomplete_glyphs": f["incomplete_glyphs"],
                "k1": f["k1"],
                "commands": devs5[f["k1"]]["n"],
                "de_fabrica": f["k1"] < K1_DE_FABRICA,
                "screen_ordinal": f["screen_ordinal"],
                "menu": {
                    "sheet": f["sheet"],
                    "row": f["row"],
                    "zone": f["zone"],
                    "ordinal_menu": f["menu_ordinal"],
                },
            }
        )

    # WHAT IS LOST, per device, already resolved here -- so the app can
    # say it in the confirmation dialog WITHOUT running `delete_device.py` before
    # the user says yes. It's the same function `delete_device.py` prints
    # afterward, so the two screens can't diverge. `borrable`/`reason`
    # also come from here: they are the ONLY two aborts of `delete_device.py`
    # that can be anticipated without running it.
    import activities  # noqa: PLC0415 -- listar is the one that uses it, not the other way around

    def _dec(ptr, inline=None):
        # Same table and same completeness rule as `decode` -- by CODE, not
        # by counting '?' in the output (0x3E is a real question mark). It
        # used to re-run `glyphs.extender()` on every call, which meant a
        # full blob sweep per activity name AND a table that could differ
        # from the one the device rows were read with.
        if inline is not None:
            table = decode.table
            decode.sin_tabla.update(c for c in inline if c not in table)
            return (
                "".join(table.get(c, "?") for c in inline),
                all(c in table for c in inline),
            )
        return decode(ptr)

    n_sheet1 = sum(1 for f in reference if f["sheet"] == 1)
    for d in devices:
        inf = activities.report(b, d["k1"], _dec)
        d["activities"] = {
            "usan_el_aparato": inf["usan_el_aparato"],
            "nombres": [
                inf["nombres"].get(x, "activity %d" % x)
                for x in inf["activities_in_menu"]
            ],
        }
        d["se_pierde"] = activities.human_sentences(inf, d["name"], d["commands"])
        # The ONLY limit left: don't empty menu sheet 1. The old "last one
        # added" rule no longer applies: the N>1 back to N=1 rollback is
        # solved (`erase.hdr_n1_de_fabrica`, factory header re-pointed
        # with byte-for-byte identity, exercised chained). And "factory"
        # no longer applies either: k1 0..2 delete just like added ones.
        if d["menu"]["sheet"] == 1 and n_sheet1 <= 1:
            d["borrable"] = False
            d["reason"] = "ultimo_de_la_hoja_1"
        else:
            d["borrable"] = True
            d["reason"] = None

    # Now that every name has been decoded: a byte outside the 71 glyphs is
    # the only thing left that can put a '?' on screen, and it means the
    # reader landed on something that is not text.
    if decode.sin_tabla and not glyph_warning:
        glyph_warning = _AVISO_CODIGO % ", ".join(
            "%#04x" % c for c in sorted(decode.sin_tabla)
        )
        print("WARNING: " + glyph_warning, file=sys.stderr)

    print("blob: %s (%d B)" % (a.blob, len(b)))
    print(
        "replicated menu objects: %s (%d rows each, in sync)"
        % (candidates, len(reference))
    )
    if orphans:
        print(
            "section [5]: %d device(s) declared, %d reachable from the "
            "menu. ORPHANS (declared and NOT reachable): k1 %s -- that's "
            "what a removal leaves behind, and it is the correct state: "
            "pulling the sub-table out of the middle would renumber the "
            "k1 of the ones that follow, and the firmware walks it "
            "without a range check." % (len(devs5), len(reference), orphans)
        )
    if glyph_warning:
        print("WARNING: " + glyph_warning)
    print(
        "\n%-16s %4s %9s %8s %6s %6s %6s"
        % ("name", "k1", "commands", "screen", "menu", "sheet", "row")
    )
    for d in devices:
        print(
            "%-16s %4d %9d %8d %6d %6d %6d   %s"
            % (
                d["name"],
                d["k1"],
                d["commands"],
                d["screen_ordinal"],
                d["menu"]["ordinal_menu"],
                d["menu"]["sheet"],
                d["menu"]["row"],
                "factory" if d["de_fabrica"] else "added",
            )
        )

    salida = {
        "devices": devices,
        "activities": activities.report(b, -1, _dec),
        "huerfanos_seccion5": orphans,
        "declarados_seccion5": len(devs5),
        "menus": candidates,
        "glyph_warning": glyph_warning,
    }
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(salida, indent=1))
        print("\nwritten %s" % a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
