#!/usr/bin/env python3
"""DELETES ANY device from the Harmony One (factory or added), without
moving a single byte of the body.

It's the mirror of `add_device.py`: where that one adds, extending `[9]`
(with `reubicar.relocate`) and stacking a new tail (headers, programs,
trailers, repointed `table[6]`), this one REMOVES -- makes a Devices menu
row unrecognizable and leaves everything that hung off it as unreachable
garbage. The same principle the WHOLE project uses (`relocate.py`,
`fourth_device.py`, `add_device.py`): "the bytes that stay where they were don't
get erased, they become unreachable, which is safer than compacting".

## WATCH OUT: this is NOT the mirror of `fourth_device.py`

An earlier version of this file mirrored `fourth_device.py` (which edits the
ROWS of SHEET 1, the 18 B `02 06/02 0b/04 3f` inserted in-place in the
menu object, addressed by `--ordinal/--row`). That technique is
HISTORICAL and pointed at the WRONG place for ADDED devices: today's blob
(`config_empaquetada.bin`, produced by `add_device.py`) adds them
as EXTRA ROWS on a separate SHEET 2 (the N>1 mechanism of `table[6]`,
`read_extra_rows`/`menu_sheet_layout`), not on sheet 1.

Measured against the real blob (`config_empaquetada.bin`):

    D.read_extra_rows(b, o, orden_menu)      -> 2: id_jump 2904 (Philips,
                                                  32 commands) and 2972 (LG,
                                                  63 commands) -- matches
                                                  `read_section5(b)` = [84, 62,
                                                  90, 32, 63]

Philips and LG are in `read_extra_rows`; TV/DVR/Home (0..2) are
on sheet 1 (`read_sheet1_rows`, below). This version deletes BOTH kinds,
with its own localization and repackaging for each -- see "TWO
localization paths" below.

## Why touching the menu row is enough

A command button is NEVER resolved by its position: it's resolved by
following pointers (row -> id_jump -> screen ordinal -> slots -> keyreg ->
id_a -> id_b -> {cmd_id,0x7D}/{dev_id,0x7C} -> section [5] by (k1,k2)). If
the ONE row that points to `id_jump` is removed, the rest of that chain
(the whole commands screen, its objects in `[10]`, its section [5]
sub-table) is left with no entry path from the menu. It's still "alive" in
the sense that it would resolve if something pointed to it -- but nothing
does. That's why there's no need to touch section [5] or tabla[6] beyond
that row: leaving them intact is SAFER than renumbering (renumbering a
`k1` from the middle would mean rewriting the `dev_id`/`cmd_id` of EVERY
command of the devices left with a higher index, exactly the kind of mass
change this project avoids).

## What DOES change

  1. `[9]` is extended (via `reubicar.relocate`, the same path
     `add_device.py` uses) with one new keyreg per extra sheet of the 3
     menu objects (74/90/141 in today's blob), already WITHOUT the deleted
     row and REPACKAGED with `menu_sheet_layout` -- the same function that
     builds an addition, run with one fewer row.
  2. Emitted to the tail: the extra sheets' new program(s), their slots,
     and a new trailer for each of the 3 menu objects (sheet 1, LEFT
     UNTOUCHED -- not even copied -- + the new extra sheets).
  3. The 3 `table[6]` entries (74/90/141) are REPOINTED in-place to those
     new trailers. It's a VALUE change of an existing pointer, not a data
     move -- the same kind of change `write.py` declares with
     `--repoint`.

## What does NOT change (on purpose), on NEITHER of the two paths

  * Section [5] in full, ALWAYS: the deleted device's sub-table stays
    there, pointing at records that are still valid, simply with nothing
    referencing it. `check_section5`/`resolve_section5` don't
    distinguish "not referenced" from "never used". That's why the "gap
    vs. renumbering" dilemma in section [5] is a FALSE dilemma for this
    design: neither is chosen, [5] stays byte for byte THE SAME no matter
    which device is deleted (factory or added), so `cmd_setup_ir` never
    sees a k1/k2 out of range because of this operation -- renumbering
    would touch the `dev_id`/`cmd_id` of EVERY command of the devices with
    a higher index (measured: 185 objects, 370 B, just to remove the
    DVR from a 5-device blob), and it wouldn't even close the
    risk: any other place in the blob that stores a raw `k1` without this
    project knowing about it (the Activities state machine, section
    [14]/[0], is a candidate) would end up wrong with nothing to detect
    it.
  * `table[6]`: the deleted device's commands-screen ordinal stays there,
    with its trailer intact -- just nothing jumps to it anymore.
  * The deleted device's `[10]`/`[11]` objects (commands, hooks, id_jump,
    id_volver).
  * The sheet that does NOT have the deleted row: if the device is ADDED,
    the 3 factory sheet-1s stay byte for byte UNTOUCHED, not even copied.
    If the device is FACTORY, the EXTRA sheets (if any) stay just as
    intact -- only the sheet that lost a row gets repackaged.

## TWO localization paths, one per device type

  * ADDED (index>=3): `locate_device` derives `id_jump` by
    ARITHMETIC over `table[11]` (valid because `reubicar.
    device_objects` assigns ids in a fixed order) and requires
    EXACT coverage (`found == expected`, the `n` commands from section [5]
    for that index) against the ONE screen it reaches.
  * FACTORY (index<3): that arithmetic does NOT apply (it gives an id that
    isn't even a valid jump object -- measured) and also the 3 menu
    objects (74/90/141) do NOT share a single id_jump for the same row
    (each has its own). `locate_sheet1_row` localizes PER OBJECT,
    walking each sheet-1 candidate FORWARD and requiring PURITY (every
    command found belongs to `dev_id` and no other) instead of full
    coverage: measured that a factory device can have MORE navigation
    routes to its commands than the Devices menu's (physical buttons,
    Activities) -- measured: Home resolves 58/90 commands from its single
    menu screen and DVR 18/62 from its own. This operation
    doesn't touch those routes or need to map them: section [5] doesn't
    change, so none of them can hang because of this -- it just loses the
    Devices menu entry, not necessarily "invisible" from every other path.

## The ONLY limit left: don't empty menu sheet 1

Doesn't distinguish factory from added. If the device to delete is the
last one still drawn on sheet 1, it aborts BEFORE touching anything: the
Devices menu would be left with no row to show, a state that doesn't
exist at the factory and that the generator itself can't emit.

The OLD limit ("the only added device can't be deleted, because going
back from N>1 to N=1 means restoring the 0xAE/0xAF strips and that
transition isn't exercised") NO LONGER EXISTS: it's solved by copying from
the factory. The N=1 header (the one declaring the 2 NULL strips) stays
alive-but-dead at its original offset -- the addition never erased it, it
just emitted a new one without strips and abandoned it -- so going back to
N=1 is just REPOINTING the trailer to that header. And there's no need to
have `config_raw.bin` on hand: `hdr_n1_de_fabrica()` BUILDS the shape it
would have (`<n+2> af 00 00 00 ae 00 00 00 <today's n entries>`, measured
byte for byte against the 3 factory menus) and SEARCHES for it inside the
blob itself; if it doesn't appear exactly once, it aborts.

## Mandatory checks (abort if they fail), the same for both paths

  (a) THE COMMANDS OF WHAT'S LEFT, exhaustive: every `(k1,k2)` of every
      remaining device -- and also those of the just-orphaned ONE -- is
      re-resolved with `cmd_setup_ir`'s exact arithmetic
      (`resolve_section5`). This is the check that prevents a HANG: the
      firmware walks `[5]` without checking range. It always holds because
      `[5]` never changes, and that's exactly why it's checked: if it ever
      did change, this check catches it before writing.
  (b) `reubicar.chain()`: PRE-EXISTING buttons resolve identically before
      and after.
  (c) no new structure crosses a 64 KB boundary (`crosses_page`).
  (d) `grabar.nothing_moved`, with its NEGATIVE (without declaring the
      repoints it has to say NO; if it said yes, the gate isn't looking at
      anything).
  (e) `configcheck.revisar()` all green.
  (f) THE ACTIVITIES (`activities.py`): the engine is section `[14]`, not
      `[0]`. `[0]` (the state-variable table) is IDENTICAL byte for byte
      with 0 and with 2 added devices, so any invariant over it is always
      green and discriminates nothing. What DOES discriminate is `[14]`'s
      transitive closure through `table[11]`: it gives the `cmd_id`s an
      activity can actually emit. It checks (f1) that `[14]`'s layout
      separates from its negatives, (f2) that the set of `k1` the engine
      reaches is the SAME before and after, (f3) that ALL those `cmd_id`
      resolve through section `[5]` -- i.e. no activity is left pointing
      at a nonexistent device -- and (f4) that the activities menu is left
      with the same rows.
  (g) NAVIGATION AND GEOMETRY: no sheet of the 3 menus references the cut
      `id_jump` again, AND -- what no earlier check looked at -- the row
      drawn at position `k` declares position `k`'s TOUCH ZONE
      (`mapa_de_menu`). The zones (`0xB0/0xB1/0xB2`) are geometric and
      fixed; compacting the rows requires REASSIGNING them. Without this
      check, a blob with the middle row drawn and the bottom zone declared
      passed (a)..(e) green: it looked fine and didn't respond to touch.
  (h) IDENTITY (factory path only): rebuilding sheet 1 with ALL its rows
      has to reproduce the original program and key register BYTE FOR
      BYTE. This is what guarantees "copy from the factory instead of
      inventing": it instantly catches a swapped ATTR, a generic icon
      glued on, or a different keyreg order.

## What the user is told

Nothing technical. `activities.human_sentences()` returns WHAT IS LOST in
plain sentences ("Home disappears from the device list, with its 90
commands and its screen" / "Your activities (TV HD and PC) use this
device. They are NOT deleted and will keep working the same way..."). It
is the SAME function `list_devices.py` uses for the app's confirmation, so the
screen and this tool can never say different things. With `--json`, all
of that comes out already resolved.

Usage:
    python3 delete_device.py <blob.bin> --indice 2 --salida new.bin

NOTE ON NAMING: `_decodificador` keeps its exact Spanish name --
`edit_activity.py`, `keys_physical.py`, and `keys_photo.py` all
`import erase` and call `erase._decodificador(...)` directly. The
`--repoint 0x......` text this script PRINTS (not just the CLI flag
itself) is also load-bearing: `app/api.py`'s `RE_REPUNTA` regex scrapes
this exact substring out of the subprocess's stdout to recover the
repoint list, so every `print(...)` that emits it keeps the literal
"--repunta" -- same reasoning as the CLI flags (`--index`, `--salida`,
`--json`, `--ezhex`, `--plantilla`), which stay as-is because
`app/generate.py` builds the command line with them. Everything else
(comments, docstrings, local names, and the rest of the printed/aborted
text) was translated freely, verified by diffing this script's output
blob byte for byte against the pre-translation baseline for the same
input and flags.
"""

from __future__ import annotations

import argparse
import pathlib

import activities
import configcheck
import add_device as D
import arrow_backlight
import relocate

BASE = D.BASE


def u24(b: bytes, o: int) -> int:
    return D.u24(b, o)


def find_all(b: bytes, patron: bytes, ini: int = 0, fin: int | None = None):
    """Offsets where `patron` appears, OPTIONALLY bounded to `[ini, fin)`.

    The bound is NOT an optimization: it's correctness. `reubicar.relocate`
    deliberately LEAVES the old copy of each section where it was ("the
    bytes that stay where they were don't get erased: they become
    unreachable, which is safer than compacting"), so a blob that went
    through N rounds of add/delete has N DEAD copies of section [10] with
    the same command records inside. Searching the whole blob counts them
    as if they were real candidates -- and then the uniqueness check below
    aborts with "appears 2 times" on a perfectly healthy blob.

    Measured (`config_empaquetada.bin`, the blob that's grabbed and
    running): device 3's pattern appears at 0x1437d9 (dead copy: falls
    inside `philips_empaquetado.bin`'s live [10], an ancestor) and at
    0x14a3d9 (live). Without bounding, deleting Philips was IMPOSSIBLE; and
    on this same tool's own output no index could be deleted, because every
    run adds another dead copy. With the bound there is exactly 1 hit in
    every case measured.
    """
    fin = len(b) if fin is None else fin
    out = []
    i = b.find(patron, ini)
    while i != -1 and i < fin:
        out.append(i)
        i = b.find(patron, i + 1)
    return out


def locate_device(
    b: bytes, index: int, dest0: list[int], devs5: list[dict], sec10: tuple[int, int]
) -> dict:
    """Derives `id_jump` and the commands-screen ordinal for device
    `index`, assuming NOTHING, and verifies the screen resolves to
    EXACTLY the commands section [5] declares for that index.
    """
    dev = devs5[index]
    n = dev["n"]
    if n <= 0:
        raise SystemExit(
            "device %d has no command at all in section [5]: there is no "
            "way to locate it without assuming -- not supported" % index
        )
    dev_id = (index << 8) | 1
    cmd0 = (index << 8) | 0
    patron = bytes([2]) + relocate.slot(cmd0, 0x7D) + relocate.slot(dev_id, 0x7C)
    a10, z10 = sec10
    all_hits = find_all(b, patron)
    # TWO independent filters, and they have to agree: inside the LIVE [10]
    # (by range) and referenced by tabla[11] (by pointer). If they didn't
    # agree, the blob is weird and this aborts instead of picking one.
    live = set(dest0)
    by_range = [h for h in all_hits if a10 <= h < z10]
    by_table = [h for h in all_hits if h in live]
    if by_range != by_table:
        raise SystemExit(
            "device %d's command-0 record doesn't give the same result by "
            "range of the live section [10] (%s) as by tabla[11] (%s): "
            "aborting instead of picking one"
            % (
                index,
                [hex(h) for h in by_range],
                [hex(h) for h in by_table],
            )
        )
    hits = by_range
    if len(hits) != 1:
        raise SystemExit(
            "device %d's command-0 record (%s) appears %d times INSIDE "
            "the live section [10] (%#08x..%#08x), expected 1: cannot be "
            "identified without ambiguity"
            % (index, patron.hex(" "), len(hits), a10, z10)
        )
    if len(all_hits) > 1:
        print(
            "   (note: the pattern appears %d times in the whole blob %s; %d "
            "are DEAD copies of old [10] sections that `reubicar.relocate` "
            "deliberately leaves behind -- not candidates, nothing points to them)"
            % (
                len(all_hits),
                [hex(h) for h in all_hits],
                len(all_hits) - 1,
            )
        )
    off_b0 = hits[0]
    k_b0 = [k for k, d in enumerate(dest0) if d == off_b0]
    if len(k_b0) != 1:
        raise SystemExit(
            "the command object at %#08x has %d entries in tabla[11], "
            "expected 1" % (off_b0, len(k_b0))
        )
    id_jump = k_b0[0] - 2
    if not 0 <= id_jump < len(dest0):
        raise SystemExit(
            "derived id_jump (%d) falls outside tabla[11] (%d entries)"
            % (id_jump, len(dest0))
        )
    off_jump = dest0[id_jump]
    if not 0 <= off_jump < len(b) or b[off_jump] != 3:
        raise SystemExit(
            "the id_jump candidate (%d, offset %#08x) doesn't have a jump "
            "object header (03)" % (id_jump, off_jump)
        )
    rj = D._slots(b, off_jump)
    if rj is None:
        raise SystemExit("the id_jump candidate (%d) doesn't parse" % id_jump)
    tag7e = [v for v, t in rj if t == 0x7E]
    tag9a = [v for v, t in rj if t == 0x9A]
    if len(tag7e) != 1 or len(tag9a) != 1 or tag9a[0] != 1:
        raise SystemExit(
            "the id_jump candidate (%d) doesn't have jump_object()'s exact "
            "shape: slots %s" % (id_jump, rj)
        )
    screen_ordinal = tag7e[0]

    t6_entry = u24(b, D.T6 + 3 + 3 * screen_ordinal) - BASE
    tr = D.read_trailer(b, t6_entry, max_n=200)
    if tr is None:
        raise SystemExit(
            "candidate screen ordinal %d doesn't parse as a trailer" % screen_ordinal
        )
    found = set()
    for sp in tr["slots"]:
        s = D.read_slot(b, sp - BASE)
        if s is None:
            continue
        kr = D.read_key_register(b, s["keyreg"] - BASE) or []
        for _cod, ident, cls in kr:
            if cls != 0x7F or not 0 <= ident < len(dest0):
                continue
            r_a = D._slots(b, dest0[ident])
            if not r_a:
                continue
            ids_b = [v for v, t in r_a if t == 0x7F]
            if not ids_b or not 0 <= ids_b[0] < len(dest0):
                continue
            r_b = D._slots(b, dest0[ids_b[0]])
            if not r_b:
                continue
            cmds_ = [v for v, t in r_b if t == 0x7D]
            devs_ = [v for v, t in r_b if t == 0x7C]
            if cmds_ and devs_:
                found.add((cmds_[0], devs_[0]))
    expected = {((index << 8) | k2, dev_id) for k2 in range(n)}
    if found != expected:
        raise SystemExit(
            "the candidate screen (ordinal %d) does not resolve to EXACTLY "
            "the %d commands of device %d -- extra %s, missing %s"
            % (
                screen_ordinal,
                n,
                index,
                sorted(found - expected),
                sorted(expected - found),
            )
        )
    return {"id_jump": id_jump, "screen_ordinal": screen_ordinal, "n": n}


def resolve_screen(
    b: bytes, dest0: list[int], screen_ordinal: int
) -> set[tuple[int, int]] | None:
    """`{(cmd_id, dev_id), ...}` of ALL buttons reachable from
    `screen_ordinal`'s trailer (all its pages/slots). It's the same
    arithmetic as the bottom half of `locate_device`, pulled out so
    it can be tried against ANY candidate ordinal (needed for sheet-1 rows:
    see `locate_sheet1_row`)."""
    t6_entry = D.u24(b, D.T6 + 3 + 3 * screen_ordinal) - BASE
    if not 0 <= t6_entry < len(b):
        return None
    tr = D.read_trailer(b, t6_entry, max_n=200)
    if tr is None:
        return None
    found: set[tuple[int, int]] = set()
    for sp in tr["slots"]:
        s = D.read_slot(b, sp - BASE)
        if s is None:
            continue
        kr = D.read_key_register(b, s["keyreg"] - BASE) or []
        for _cod, ident, cls in kr:
            if cls != 0x7F or not 0 <= ident < len(dest0):
                continue
            r_a = D._slots(b, dest0[ident])
            if not r_a:
                continue
            ids_b = [v for v, t in r_a if t == 0x7F]
            if not ids_b or not 0 <= ids_b[0] < len(dest0):
                continue
            r_b = D._slots(b, dest0[ids_b[0]])
            if not r_b:
                continue
            cmds_ = [v for v, t in r_b if t == 0x7D]
            devs_ = [v for v, t in r_b if t == 0x7C]
            if cmds_ and devs_:
                found.add((cmds_[0], devs_[0]))
    return found


def locate_sheet1_row(
    b: bytes, o: dict, menu_order: list[int], dest0: list[int], dev_id: int, index: int
) -> dict | None:
    """Looks, AMONG menu object `o`'s SHEET-1 rows, for the one that points
    to FACTORY device `index` (k1 < 3).

    A factory device CANNOT be located with `locate_device`'s
    arithmetic (`id_jump = <object B's index in table[11]> - 2`): that
    formula is an artifact of ADDING (`reubicar.device_objects`
    assigns ids in that exact order), not a general property of the
    format. Measured against `config_empaquetada.bin`: for Home
    (k1=1) it gives id_jump=411, which is NOT a valid jump object (the
    shape validation in `locate_device` would have rejected it).
    Also, unlike an added device, the 3 menu objects do NOT share a single
    id_jump for the same row -- each has its own (measured: ordinal 74
    uses 2326/2327/2328, ordinal 90 uses 2346/2347/2348, ordinal 141 uses
    2725/2726/2727/2728 for the same 3 TV/DVR/Home rows) -- so it has
    to be located PER OBJECT, not once for all 3.

    The criterion is PURITY, not full coverage: each sheet-1 row is
    followed to its screen (`resolve_screen`) and it's required that
    EVERY command found there belongs to `dev_id` and no other. It is NOT
    required that the screen cover the `n` commands section [5] declares
    for the device -- measured that it does NOT: Home resolves 58/90 from
    its single menu screen and DVR 18/62 from its own (figures an
    earlier version of this file had swapped, attributing DVR's to
    Home): a factory device can have more navigation routes to its
    commands than the Devices menu's (physical buttons, Activities), and
    this operation neither touches them nor needs to map them to safely
    cut THIS route -- section [5] never changes, so none of those other
    routes can hang because of this.

    Returns `{'cod','id_jump','screen_ordinal','hallados'}` or `None` if
    no row resolves, purely, to the device being searched for. Aborts if
    there's ambiguity (more than one pure row, or a mixed row that
    includes `dev_id` without being purely its own)."""
    row_codes = menu_order[: D.MAX_ROWS_PER_SHEET]
    kr = D.read_key_register(b, o["keyreg"])
    if kr is None:
        raise SystemExit(
            "menu %d's sheet 1 doesn't parse its key register" % o["ordinal"]
        )
    winner = None
    for cod, ident, cls in kr:
        if cls != 0x7F or cod not in row_codes:
            continue
        if not 0 <= ident < len(dest0):
            continue
        off_jump = dest0[ident]
        if not 0 <= off_jump < len(b) or b[off_jump] != 3:
            continue
        rj = D._slots(b, off_jump)
        tag7e = [v for v, t in rj if t == 0x7E]
        tag9a = [v for v, t in rj if t == 0x9A]
        if len(tag7e) != 1 or len(tag9a) != 1 or tag9a[0] != 1:
            continue
        screen_ordinal = tag7e[0]
        found = resolve_screen(b, dest0, screen_ordinal)
        if not found:
            continue
        devices_present = {d for _c, d in found}
        if devices_present == {dev_id}:
            if winner is not None:
                raise SystemExit(
                    "menu %d has more than one PURE sheet-1 row for device "
                    "%d (%#04x and %#04x): ambiguous, aborting"
                    % (o["ordinal"], index, winner["cod"], cod)
                )
            winner = {
                "cod": cod,
                "id_jump": ident,
                "screen_ordinal": screen_ordinal,
                "hallados": found,
            }
        elif dev_id in devices_present:
            raise SystemExit(
                "row %#04x of menu %d resolves a MIXED screen (dev_ids "
                "%s): there's no PURE candidate for device %d -- aborting "
                "instead of guessing"
                % (cod, o["ordinal"], sorted(devices_present), index)
            )
    return winner


def _decodificador(b: bytes):
    """`decode(ptr, inline=None) -> (text, complete)`: the same glyph
    decoder `list_devices.py` uses (the blob's names are glyph indices, not
    ASCII), with the INLINE variant for `TXTIN` (that's how the factory
    stores the activities menu's "PC" name). A failure here can NOT flip a
    deletion: it degrades to "?" and carries on."""
    import list_devices  # noqa: PLC0415 -- only for names, not to decide anything

    decode, _warning = list_devices.make_decoder(b, list_devices.DEFAULT_HUB)
    import glyphs  # noqa: PLC0415

    # `glyphs.BASE` is the complete 71-code table (read from the blob's own
    # font section [7]), so the INLINE branch reads the same letters the
    # pointer branch does. This used to run `glyphs.extender()` twice --
    # once empty, once with the Hub's vocabulary if it happened to be on
    # disk -- which meant the confirmation dialog could name a device one
    # way and the activities inside it another, depending on whether an
    # account export existed. And `complete` was decided by looking for '?'
    # in the OUTPUT, which is wrong now that 0x3E is a real question mark.
    table = glyphs.table_for(b)

    def dec(ptr, inline=None):
        if inline is not None:
            return (
                "".join(table.get(c, "?") for c in inline),
                all(c in table for c in inline),
            )
        try:
            return decode(ptr)
        except Exception:  # noqa: BLE001
            return "?", False

    return dec


def device_name_of(b: bytes, index: int, decode) -> str:
    """The visible name of device `index`, to talk to the user.

    Comes from the SAME path as `list_devices.py` (menu row -> screen -> k1),
    not a separate table. If it can't be decoded, a neutral label is
    returned instead of inventing a name."""
    import list_devices  # noqa: PLC0415

    dest11 = relocate.table(b, relocate.sections(b)[11][0])
    zones19 = D.read_section19(b)
    for o in D.menu_objects(b):
        try:
            rows = list_devices.menu_rows(b, o["ordinal"], decode, dest11, zones19)
        except SystemExit:
            continue
        for f in rows:
            if f["k1"] == index:
                return f["name"]
    return "device %d" % index


def row_position(text_y: int) -> int | None:
    """The POSITION (0 = top) that corresponds to a row drawn with its
    name at `text_y`, or `None` if that Y doesn't fall on the row grid.

    The grid is the factory one (`Y = Y_ROW_0 + k*ROW_STEP`, text at
    `+19`) and is the SAME one `device.program_menu_sheet` emits for
    extra sheets. The position isn't decorative: it fixes the row's TOUCH
    ZONE (`menu_order[k]`, section [19] geometry), so reading it wrong
    desyncs what's seen from what's touched."""
    d = text_y - D.Y_ROW_0 - 19
    if d < 0 or d % D.ROW_STEP:
        return None
    k = d // D.ROW_STEP
    return k if 0 <= k < D.MAX_ROWS_PER_SHEET else None


def read_sheet1_rows(b: bytes, o: dict, menu_order: list[int]) -> dict:
    """A menu object's sheet 1, read in full and VERIFIED.

    Returns `{'rows': [...], 'attr': int|None, 'kr': [...]}` where each
    row is `{'pos','bmp_grande','bmp_chico','off_text','id','codigo'}` IN
    GEOMETRIC ORDER, with the REAL icon and the REAL touch zone.

    Three things the previous version got wrong that are fixed here:

      1. **Doesn't require 3 rows.** Before, it compared `len(rows)`
         against `menu_order[:MAX_ROWS_PER_SHEET]` (always 3), so after
         deleting ONE factory device sheet 1 was left with 2 and a SECOND
         deletion aborted with "2 name row(s), expected 3" -- on top of
         having already repackaged section [9]. Now the row count comes
         from the PROGRAM (1..3) and each row's zone comes from its
         geometric POSITION, which is the real invariant.

      2. **Reads the real ATTR.** Factory sheet 1 declares `ATTR 4` before
         the first row; emitting `D.ATTR_FILA` (9) instead changes the
         anchor screen's font without anyone asking for it. Here the
         attribute in effect at the moment of the FIRST row is saved and
         re-emitted as-is.

      3. **Is also the ENTRY sync check.** It requires that the row at
         position `k` declares zone `menu_order[k]`, and that no row zone
         is left over with no row. If the input blob was already out of
         sync, it aborts here instead of propagating it.
    """
    row_codes = menu_order[: D.MAX_ROWS_PER_SHEET]
    kr = D.read_key_register(b, o["keyreg"])
    if kr is None:
        raise SystemExit(
            "menu %d's sheet 1: the key register doesn't parse" % o["ordinal"]
        )
    id_by_code = {
        cod: ident for cod, ident, cls in kr if cls == 0x7F and cod in row_codes
    }
    ins = D.disassemble(b, o["prog"])
    rows: list[dict] = []
    bmps: list[int] = []
    current_attr: int | None = None
    first_attr: int | None = None
    for _off, op, ar in ins:
        if op == "BMP":
            bmps.append(ar[2])
        elif op == "ATTR":
            current_attr = ar[0]
        elif op == "TXT" and ar[0] == D.TAG_NAME:
            pos = row_position(ar[1])
            if pos is None:
                continue
            if len(bmps) < 2:
                raise SystemExit(
                    "menu %d's sheet 1: the row at Y=%d doesn't have 2 BMPs "
                    "before it (large icon + small icon)" % (o["ordinal"], ar[1])
                )
            if not rows:
                first_attr = current_attr
            rows.append(
                {
                    "pos": pos,
                    "bmp_grande": bmps[-2],
                    "bmp_chico": bmps[-1],
                    "off_text": ar[2],
                }
            )
            bmps = []
    if not rows:
        raise SystemExit(
            "menu %d's sheet 1: doesn't draw a single device row" % o["ordinal"]
        )
    if [f["pos"] for f in rows] != list(range(len(rows))):
        raise SystemExit(
            "menu %d's sheet 1: the rows are not at positions 0..%d (got "
            "%s) -- the input blob already has gaps, aborting"
            % (o["ordinal"], len(rows) - 1, [f["pos"] for f in rows])
        )
    for f in rows:
        cod = row_codes[f["pos"]]
        if cod not in id_by_code:
            raise SystemExit(
                "menu %d's sheet 1: the row drawn at position %d doesn't "
                "declare its touch zone %#04x in the key register -- what's "
                "seen and what's touched don't match in the INPUT blob"
                % (o["ordinal"], f["pos"], cod)
            )
        f["id"] = id_by_code[cod]
        f["codigo"] = cod
    leftover = set(id_by_code) - {f["codigo"] for f in rows}
    if leftover:
        raise SystemExit(
            "menu %d's sheet 1: the key register declares row zone(s) %s "
            "with no row drawn for them -- out of sync in the INPUT blob"
            % (o["ordinal"], [hex(c) for c in sorted(leftover)])
        )
    return {"rows": rows, "attr": first_attr, "kr": kr}


def reduced_sheet1_program(
    prologo_off: int,
    rows: list[dict],
    attr: int | None,
    pie: bytes | None,
    own_off: int | None,
) -> bytes:
    """Like `device.program_menu_sheet`, but COPYING FROM THE FACTORY
    instead of synthesizing: preserves each row's REAL icon and the
    sheet's REAL ATTR (`rows`/`attr`: `read_sheet1_rows`'s output, already
    without the removed one).

    `device.device_row` ALWAYS hardcodes the TV's icon
    (`ICONO_GRANDE` = "the TV's one (DeviceType 1 = Television)", literal
    in its definition) and ALWAYS `ATTR_FILA` (9): correct for an ADDITION
    -- an added device carries the generic icon and the 62-glyph font it
    needs to write "Philips" -- but WRONG for repackaging factory sheet 1,
    where each row has its own icon (Home and DVR aren't a TV)
    and the font is `ATTR 4`. Check (h) requires that, with ALL the rows,
    this function reproduces the original program BYTE FOR BYTE."""
    if not 1 <= len(rows) <= D.MAX_ROWS_PER_SHEET:
        raise SystemExit("programa_hoja1_reducido: %d row(s)" % len(rows))
    cuerpo = bytes([0x16]) + D.p(prologo_off)
    for k, f in enumerate(rows):
        y = D.Y_ROW_0 + k * D.ROW_STEP
        cuerpo += bytes([0x02, D.X_ICONO_GRANDE, y]) + D.p(f["bmp_grande"])
        cuerpo += bytes([0x02, D.X_ICONO_CHICO, y + 1]) + D.p(f["bmp_chico"])
        if k == 0 and attr is not None:
            cuerpo += bytes([0x10, attr])
        cuerpo += bytes([0x04, D.TAG_NAME, y + 19]) + D.p(f["off_text"])
    if pie is None:
        return cuerpo + b"\x00"
    if pie == b"SWITCH":
        if own_off is None:
            raise SystemExit("programa_hoja1_reducido with SWITCH needs off_propio")
        off_sw = own_off + len(cuerpo)
        sw, cuerpos = D.left_foot_switch(off_sw, off_sw + D.LARGO_SWITCH + 1)
        return cuerpo + sw + b"\x00" + cuerpos
    return cuerpo + pie + b"\x00"


def keyreg_without_row(
    kr: list[tuple[int, int, int]],
    rows: list[dict],
    row_codes: list[int],
    id_cortado: int,
) -> bytes:
    """Sheet 1's key register WITHOUT row `id_cortado`, with the touch
    zones REASSIGNED BY POSITION.

    THIS is the fix that was missing. The zone codes (`0xB0/0xB1/0xB2`)
    are GEOMETRIC: they come from the K=4 template's rectangles in
    section [19] (`0xB0` = the top band, `0xB1` = the middle one, `0xB2` =
    the bottom one; measured with `device._rects_by_code`: y0
    3016/2144/1272, height 807 each). The new program redraws the rows
    COMPACTED (positions 0..n-2), so keeping each surviving row's ORIGINAL
    code leaves the screen out of sync: the row that shows up in the
    middle doesn't respond, and touching the empty space at the bottom
    opens it. The ADDED-device path already reassigned by position
    (`row_codes[k]` when building extra sheets); the factory one
    didn't.

    Everything that is NOT a row (the left footer `0xB3`, class 0x72) is
    copied as-is and IN ITS ORIGINAL ORDER: with `id_cortado=None` this
    function reproduces the input register byte for byte (check (h))."""
    remaining = [f for f in rows if f["id"] != id_cortado]
    new_code = {f["id"]: row_codes[k] for k, f in enumerate(remaining)}
    out = []
    for cod, ident, cls in kr:
        if cod in row_codes:
            if ident == id_cortado:
                continue
            if ident not in new_code:
                raise SystemExit(
                    "keyreg_sin_fila: zone %#04x points to id %d, which "
                    "isn't any of the rows read" % (cod, ident)
                )
            out.append((new_code[ident], ident, cls))
        else:
            out.append((cod, ident, cls))
    return D.build_key_register(out)


#: the two SIDE-STRIP codes a menu header declares NULL when N=1 and that
#: an addition removes when it moves to N>1 (`add_device.py`'s census:
#: 142/142 with N=1, 0/14 with N>1). They go first, in this order.
FRANJAS_N1 = (0xAF, 0xAE)


def hdr_n1_de_fabrica(b: bytes, hdr_actual: int) -> int:
    """The offset of the N=1 header (WITH the 2 null strips) that
    corresponds to the N>1 header the menu uses today.

    It is NOT rebuilt or copied from `config_raw.bin`: the shape it would
    have is BUILT (`<n+2> af 00 00 00 ae 00 00 00 <today's n entries>` --
    measured byte for byte against the 3 factory menus) and that exact
    sequence is SEARCHED for inside the blob itself. If it appears, it's
    the factory header, which stays alive-but-dead at its original offset
    because the addition never erased it: it just emitted a new one
    without strips and abandoned it. So repointing to N=1 is "copying from
    the factory", with byte-for-byte identity as the check, and this tool
    doesn't depend on having the factory blob on hand.

    Aborts if it doesn't appear, or if it appears more than once.
    """
    n = b[hdr_actual]
    cuerpo = bytes(b[hdr_actual + 1 : hdr_actual + 1 + 4 * n])
    buscado = (
        bytes([n + 2])
        + bytes([FRANJAS_N1[0], 0, 0, 0])
        + bytes([FRANJAS_N1[1], 0, 0, 0])
        + cuerpo
    )
    hits = find_all(b, buscado)
    if len(hits) != 1:
        raise SystemExit(
            "going back to N=1 needs the factory header (the one that "
            "declares strips %#04x/%#04x as NULL). The expected shape "
            "(%s) appears %d time(s) in the blob, expected 1: ABORTING "
            "instead of rebuilding it by hand."
            % (FRANJAS_N1[0], FRANJAS_N1[1], buscado.hex(" "), len(hits))
        )
    return hits[0]


#: the tabla[11] ACTIONS that turn the paging-arrow BACKLIGHT **ON**
#: (`{037C}` = `{C022,3F}{C002,3F}{0001,93}`, channels 0 and 2 of the
#: PCA9532 on I2C 0x60; `{0245}` = channels 1 and 3). They are what
#: `flechas.turn_on_paging_arrows` ADDS to the ENTER hook (0x06) of a menu
#: header when the menu goes to N>1 -- and they are a SECOND, INDEPENDENT
#: thing from the 0xAE/0xAF strips: the strips make the touch band dead,
#: the atoms light the key. Going back to N=1 has to undo BOTH.
#: The OFF ones are deliberately NOT here: leaving the LEAVE hook turning
#: the light off is what the factory already does at N=1 and is harmless.
ACCIONES_LUZ_PAGINADO = (arrow_backlight.ACC_FLECHAS_ON_02, arrow_backlight.ACC_FLECHAS_ON_13)


def factory_enter_hook_n1(b: bytes, hdr_n1: int) -> list[tuple[int, int]]:
    """The tabla[11] entries to repoint so that, back at N=1, ENTERING the
    menu no longer lights the paging keys.

    `[(k, offset_de_fabrica), ...]`, empty if there is nothing to undo.

    MEASURED DEFECT this closes: the user removed the two added devices,
    the menu went back to N=1 with the 0xAE/0xAF strips null (that part
    already worked) **and the paging key stayed lit**. Reason: the light
    does not live in the header. `turn_on_paging_arrows` appends
    `{037C,7F}` to the tabla[11] OBJECT the ENTER hook points at and
    repoints `table[11][k]` to the grown copy -- and the FACTORY header,
    which `hdr_n1_de_fabrica` restores, references that very same `k`. So
    restoring the header restores the strips and inherits the light.

    Nothing is invented: the wanted object is the current one MINUS the
    ON actions, and that byte sequence is SEARCHED for inside the blob --
    it is still there, alive-but-dead at its factory offset, because
    `turn_on_paging_arrows` only abandoned it. Preference goes to the hit
    that sits immediately before the SIBLING hook's object (0x07, which
    was never touched), which is how the three factory menus lay it out
    (verified 3/3 on `config_raw.bin`: 0x017108/0x017364/0x017cc6, each
    exactly `len(pattern)` bytes before its 0x07 sibling).
    """
    t11 = relocate.sections(b)[11][0]
    hooks = {
        cod: op
        for cod, op, cls in arrow_backlight._atomos_cab(b, hdr_n1)
        if cls == arrow_backlight.CATEGORY_ACTION
    }
    k = hooks.get(arrow_backlight.CODE_ENTER)
    if k is None:
        return []
    _off_actual, atomos = arrow_backlight._obj11(b, t11, k)
    limpio = [
        x
        for x in atomos
        if not (x[1] == arrow_backlight.CATEGORY_ACTION and x[0] in ACCIONES_LUZ_PAGINADO)
    ]
    if len(limpio) == len(atomos):
        return []
    if not limpio:
        raise SystemExit(
            "going back to N=1: the ENTER hook's object [11][%d] is made "
            "ONLY of paging-light actions (%s); emptying it is not 'copying "
            "from the factory': ABORTING."
            % (k, " ".join("{%04X,%02X}" % x for x in atomos))
        )
    patron = arrow_backlight._arma_obj11(limpio)
    hits = find_all(b, patron)
    if not hits:
        raise SystemExit(
            "going back to N=1 needs the factory ENTER hook (the one "
            "WITHOUT the paging-light action). The expected shape (%s) does "
            "not appear anywhere in the blob: ABORTING instead of "
            "rebuilding it by hand." % patron.hex(" ")
        )
    preferido = None
    k_leave = hooks.get(arrow_backlight.CODE_EXIT)
    if k_leave is not None:
        off_leave, _ = arrow_backlight._obj11(b, t11, k_leave)
        cand = off_leave - len(patron)
        if cand in hits:
            preferido = cand
    return [(k, preferido if preferido is not None else min(hits))]


def mapa_de_menu(
    b: bytes, ordinal_menu: int, menu_order: list[int], dest11: list[int]
) -> list[dict]:
    """SYNC CHECK: what a menu DRAWS against what it DECLARES as touchable,
    sheet by sheet, reading trailer -> slots -> prog/keyreg directly
    (without `menu_objects()`, which is a heuristic).

    Returns `[{'sheet','pos','codigo','id','screen_ordinal'}, ...]` and
    ABORTS if on any sheet the row drawn at position `k` doesn't declare
    zone `menu_order[k]`, or if a row zone is left over with no row drawn.

    This is the check that was missing and the only one that catches the
    out-of-sync-zones defect: (a) looks at buttons, (b) looks at section
    [5], and neither looks at GEOMETRY. `device.read_extra_rows`
    already required this same invariant for EXTRA sheets; here it holds
    for ALL of them, sheet 1 included."""
    row_codes = menu_order[: D.MAX_ROWS_PER_SHEET]
    t = D.u24(b, D.T6 + 3 + 3 * ordinal_menu) - BASE
    tr = D.read_trailer(b, t, max_n=200)
    if tr is None:
        raise SystemExit("menu %d doesn't parse as a trailer" % ordinal_menu)
    out = []
    for h, sp in enumerate(tr["slots"], start=1):
        s = D.read_slot(b, sp - BASE)
        if s is None:
            raise SystemExit("sheet %d of menu %d: invalid slot" % (h, ordinal_menu))
        kr = D.read_key_register(b, s["keyreg"] - BASE) or []
        id_by_code = {
            cod: ident for cod, ident, cls in kr if cls == 0x7F and cod in row_codes
        }
        positions = []
        for _off, op, ar in D.disassemble(b, s["prog"] - BASE):
            if op == "TXT" and ar[0] == D.TAG_NAME:
                pos = row_position(ar[1])
                if pos is not None:
                    positions.append(pos)
        if positions != sorted(positions) or positions != list(range(len(positions))):
            raise SystemExit(
                "SYNC CHECK FAILED: sheet %d of menu %d draws rows at "
                "positions %s (expected 0..%d, no gaps)"
                % (h, ordinal_menu, positions, len(positions) - 1)
            )
        for pos in positions:
            cod = row_codes[pos]
            if cod not in id_by_code:
                raise SystemExit(
                    "SYNC CHECK FAILED: sheet %d of menu %d draws a row at "
                    "position %d but does NOT declare its touch zone %#04x "
                    "-- that row shows and can't be touched (declared: %s)"
                    % (
                        h,
                        ordinal_menu,
                        pos,
                        cod,
                        [hex(c) for c in sorted(id_by_code)],
                    )
                )
            ident = id_by_code[cod]
            rs = D._slots(b, dest11[ident]) if 0 <= ident < len(dest11) else None
            ordi = next((v for v, tg in rs or [] if tg == 0x7E), None)
            if ordi is None:
                raise SystemExit(
                    "SYNC CHECK FAILED: zone %#04x of sheet %d of menu %d "
                    "(id %d) doesn't resolve to any screen ordinal"
                    % (cod, h, ordinal_menu, ident)
                )
            out.append(
                {
                    "sheet": h,
                    "pos": pos,
                    "codigo": cod,
                    "id": ident,
                    "screen_ordinal": ordi,
                }
            )
        leftover = set(id_by_code) - {row_codes[p] for p in positions}
        if leftover:
            raise SystemExit(
                "SYNC CHECK FAILED: sheet %d of menu %d declares zone(s) %s "
                "with no row drawn there -- touching the empty space opens "
                "a screen" % (h, ordinal_menu, [hex(c) for c in sorted(leftover)])
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument(
        "--index",
        type=int,
        required=True,
        help="device index (k1 of section [5])",
    )
    ap.add_argument("--salida")
    ap.add_argument(
        "--json",
        help="report for the app: what is lost, and the checks",
    )
    ap.add_argument("--ezhex", help="besides the .bin, wrap it in a .EZHex")
    ap.add_argument("--plantilla", help="EZHex the header comes from")
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    print("input blob: %s  %d B" % (a.blob, len(b)))
    if b[:4] != b"GSPM":
        raise SystemExit("the blob doesn't start with GSPM")

    # ---- live anchors: tabla[6] from the master index, same as add_device.py ----
    t6_vivo = u24(b, D.MAESTRO_T6) - BASE
    if not 0 <= t6_vivo < len(b) - 3:
        raise SystemExit("the master tabla[6] index doesn't resolve")
    n6 = D.u16(b, t6_vivo)
    outside = [
        k for k in range(n6) if not 0 <= u24(b, t6_vivo + 3 + 3 * k) - BASE < len(b)
    ]
    if outside:
        raise SystemExit(
            "tabla[6] is not aligned on +3 (%d destinations out of range)"
            % len(outside)
        )
    D.T6 = t6_vivo
    print("tabla[6] at %#08x: %d entries" % (D.T6, n6))

    devs5 = D.read_section5(b)
    print(
        "section [5]: %d device(s) (0..2 are factory: TV/Home/DVR; "
        "3+ are added)" % len(devs5)
    )
    if not (0 <= a.index < len(devs5)):
        raise SystemExit(
            "--indice %d out of range: section [5] declares %d "
            "device(s) (0..%d)." % (a.index, len(devs5), len(devs5) - 1)
        )
    es_fabrica = a.index < 3

    sec = relocate.sections(b)
    dest0 = relocate.table(b, sec[11][0])

    # ---- menu geometry, measured the same way as add_device.py. Computed
    # BEFORE localizing because a factory device lives on a SHEET-1 row,
    # not on the extra sheets -- localizing it needs `objs`/`menu_order`. ----
    plantillas = D.read_section19(b)
    menu_order = D.zones_in_reading_order(plantillas[D.K_MENU])
    pies_menu = D.template_feet(plantillas[D.K_MENU])
    if "IZQ" not in pies_menu:
        raise SystemExit("template K=%d has no left footer" % D.K_MENU)
    row_codes = menu_order[: D.MAX_ROWS_PER_SHEET]
    cod_pie_menu = pies_menu["IZQ"]

    objs = D.menu_objects(b)
    if len(objs) != 3:
        raise SystemExit("expected 3 Devices menu objects, found %d" % len(objs))
    print("Devices menu objects: %s" % [o["ordinal"] for o in objs])
    if len({o["N"] for o in objs}) != 1:
        raise SystemExit("the 3 menus don't have the same N: cannot continue")

    sheet1_foot_by_object = {}
    for o in objs:
        ins = D.disassemble(b, o["prog"])
        if any(op == "SWITCH" and ar[0] == D.SEL_PIE for _x, op, ar in ins):
            sheet1_foot_by_object[o["ordinal"]] = b"SWITCH"
        elif any(
            op == "TXT"
            and (ar[0], ar[1]) == D.XY_ACTIVITIES
            and ar[2] == D.PTR_ACTIVITIES
            for _x, op, ar in ins
        ):
            sheet1_foot_by_object[o["ordinal"]] = D.left_foot_case0()
        else:
            raise SystemExit(
                "menu %d's sheet 1 doesn't draw the left footer in a "
                "recognized way" % o["ordinal"]
            )

    menu_foot_by_object = {}
    for o in objs:
        kr1 = D.read_key_register(b, o["keyreg"])
        ent = [e for e in kr1 or [] if e[0] == cod_pie_menu]
        if len(ent) != 1:
            raise SystemExit(
                "menu %d's sheet 1 doesn't declare left footer %#04x"
                % (o["ordinal"], cod_pie_menu)
            )
        menu_foot_by_object[o["ordinal"]] = ent[0]

    rows_by_object = {o["ordinal"]: D.read_extra_rows(b, o, menu_order) for o in objs}
    n_extra_before = len(next(iter(rows_by_object.values())))
    dev_id = (a.index << 8) | 1
    n = devs5[a.index]["n"]
    expected = {((a.index << 8) | k2, dev_id) for k2 in range(n)}

    # ------------------------------------------------------------------
    # TWO LOCALIZATION PATHS, neither touches section [5]. Gap vs.
    # renumbering is a false dilemma FOR THIS DESIGN: section [5] (the
    # device index `cmd_setup_ir` walks with k1*3+1) is never edited, never
    # shrinks and never gains an empty sub-table -- no matter which device
    # is deleted, [5] stays BYTE FOR BYTE the same, so cmd_setup_ir never
    # sees a k1/k2 out of range because of this operation. The only thing
    # that gets cut is NAVIGATION (the Devices menu row that leads there),
    # on whichever sheet that row lives on:
    #   - ADDED (index>=3): the row is on an EXTRA sheet (tabla[6]'s N>1
    #     mechanism); that sheet gets repackaged (already worked).
    #   - FACTORY (index<3): the row is on SHEET 1 (the 3 original
    #     devices, TV/Home/DVR); sheet 1 gets repackaged the same way
    #     as an extra sheet, with MAX_FILAS_POR_HOJA-1 rows.
    # ------------------------------------------------------------------
    jump_id_by_object: dict[int, dict] = {}
    sheet1_by_object: dict[int, dict] = {}
    n_rows_sheet1 = 0
    k_row = None
    if es_fabrica:
        print(
            "\ndevice %d is FACTORY (lives on sheet 1 of the 3 menu "
            "objects). Unlike an added device, the 3 objects do NOT share "
            "a single id_jump for the same row -- each has its own -- so "
            "it's located PER OBJECT, requiring PURITY (no other dev_id "
            "mixed in) instead of full coverage: a factory device can have "
            "more navigation routes than the Devices menu's (physical "
            "buttons, Activities), which this operation neither touches "
            "nor needs to map -- section [5] doesn't change, so those "
            "other routes can't hang because of this." % a.index
        )
        for o in objs:
            r = locate_sheet1_row(b, o, menu_order, dest0, dev_id, a.index)
            if r is None:
                raise SystemExit(
                    "no sheet-1 row of menu %d leads to device %d. Either "
                    "it was already deleted before (its sub-table is still "
                    "declared in section [5] as orphaned, which is the "
                    "normal state after a removal -- `list_devices.py` reports "
                    "it), or it's reached through a route that isn't the "
                    "Devices menu. Either way there's no row to cut: "
                    "ABORTING instead of guessing." % (o["ordinal"], a.index)
                )
            jump_id_by_object[o["ordinal"]] = r
            coverage = len(r["hallados"] & expected)
            print(
                "   menu %3d: row %#04x -> id_jump %d -> screen ordinal %d "
                "-> %d/%d pure command(s) of device %d found from THIS "
                "route (the rest, if any, hangs off another navigation "
                "route this operation doesn't touch or need to touch)"
                % (
                    o["ordinal"],
                    r["cod"],
                    r["id_jump"],
                    r["screen_ordinal"],
                    coverage,
                    n,
                    a.index,
                )
            )
        # each menu's sheet 1, read IN FULL and verified (real row count,
        # real icon, real ATTR, real zone). Not assumed to be 3: a second
        # factory deletion comes in with 2 and has to work.
        for o in objs:
            sheet1_by_object[o["ordinal"]] = read_sheet1_rows(b, o, menu_order)
        counts = {len(h["rows"]) for h in sheet1_by_object.values()}
        if len(counts) != 1:
            raise SystemExit(
                "the 3 menus don't have the same number of sheet-1 rows "
                "(%s): out of sync, cannot continue" % sorted(counts)
            )
        n_rows_sheet1 = counts.pop()
        if n_rows_sheet1 < 2:
            raise SystemExit(
                "the 3 menus' sheet 1 has %d row(s) and this is the LAST "
                "device left there: deleting it would leave the Devices "
                "menu with no row at all on sheet 1, a state that doesn't "
                "exist at the factory and that not even the generator "
                "itself can emit (`reduced_sheet1_program` requires >=1 "
                "row). ABORTING -- the control has to be able to show at "
                "least one device." % n_rows_sheet1
            )
        print(
            "   sheet 1: %d row(s) drawn, ATTR %s (the factory one is "
            "re-emitted as-is, not substituted), each row's own icon "
            "preserved"
            % (
                n_rows_sheet1,
                {h["attr"] for h in sheet1_by_object.values()},
            )
        )
        loc = {"n": n}
    else:
        loc = locate_device(b, a.index, dest0, devs5, sec[10])
        print(
            "\ndevice %d located without assuming anything: id_jump=%d, "
            "commands screen = ordinal %d, its %d button(s) resolve "
            "EXACTLY to the %d commands from section [5] (positive+negative check)"
            % (a.index, loc["id_jump"], loc["screen_ordinal"], loc["n"], loc["n"])
        )
        for o in objs:
            rows = rows_by_object[o["ordinal"]]
            ks = [k for k, (_t, ident) in enumerate(rows) if ident == loc["id_jump"]]
            if len(ks) != 1:
                raise SystemExit(
                    "menu %d has %d row(s) with id_jump=%d, expected "
                    "exactly 1: the device isn't in the menu, or it's "
                    "there more than once" % (o["ordinal"], len(ks), loc["id_jump"])
                )
            if k_row is None:
                k_row = ks[0]
            elif ks[0] != k_row:
                raise SystemExit(
                    "device %d is at row %d of menu %d but at row %d of "
                    "menu %d -- the 3 menus are out of sync, cannot continue"
                    % (a.index, ks[0], o["ordinal"], k_row, objs[0]["ordinal"])
                )
        print(
            "\ndevice %d's row is #%d of %d (0-indexed) across the 3 menus"
            % (a.index, k_row, n_extra_before)
        )

    # ---- BEFORE snapshot: menu geometry and activity engine. Taken here,
    # with the input blob still intact, because checks (f) and (g) compare
    # against this. Taking it later would be comparing it to itself.
    map_before = {
        o["ordinal"]: mapa_de_menu(b, o["ordinal"], menu_order, dest0) for o in objs
    }
    cut_screen_by_object = {
        o["ordinal"]: (
            jump_id_by_object[o["ordinal"]]["screen_ordinal"]
            if es_fabrica
            else loc["screen_ordinal"]
        )
        for o in objs
    }
    for ordinal, rows_before in map_before.items():
        cortada = cut_screen_by_object[ordinal]
        if cortada not in [f["screen_ordinal"] for f in rows_before]:
            raise SystemExit(
                "menu %d doesn't lead to screen %d of device %d (leads to "
                "%s): cannot cut what isn't there"
                % (ordinal, cortada, a.index, [f["screen_ordinal"] for f in rows_before])
            )
    act_before_k1, _n_obj_before = activities.engine_k1(b, dest0)
    act_menu_before = activities.activities_menu(b, dest0)
    decode = _decodificador(b)
    device_name = device_name_of(b, a.index, decode)
    inf_act = activities.report(b, a.index, decode)
    frases = activities.human_sentences(inf_act, device_name, n)

    print("\n" + "=" * 70)
    print("WHAT IS LOST (this is the only thing the app tells the user)")
    print("=" * 70)
    for frase in frases:
        print("   * " + frase)
    print("=" * 70)

    # ---- [9]: one new keyreg per sheet that changes, x3 menus ----
    a9, z9 = sec[9]
    s9 = bytearray(b[a9:z9])
    keyregs_nuevos: dict[int, int] = {}  # ordinal -> relative offset in s9 (sheet 1)
    pages_by_object: dict[int, list] = {}
    rel_keyregs_by_object: dict[int, list[int]] = {}
    factory_hdr_by_ordinal: dict[int, int] = {}
    #: ordinal -> [(tabla[11] index, factory offset)] to repoint when going
    #: back to N=1, so ENTERING the menu stops lighting the paging keys
    paging_light_by_ordinal: dict[int, list[tuple[int, int]]] = {}

    if es_fabrica:
        for o in objs:
            r = jump_id_by_object[o["ordinal"]]
            h1 = sheet1_by_object[o["ordinal"]]
            keyregs_nuevos[o["ordinal"]] = len(s9)
            s9 += keyreg_without_row(h1["kr"], h1["rows"], row_codes, r["id_jump"])
        print(
            "\nsection [9]: %d B -> %d B (+%d new key register(s): sheet 1 "
            "of the 3 menus, with %d row(s) instead of %d, and the touch "
            "zones REASSIGNED BY POSITION -- the rows are redrawn "
            "compacted, so row k has to declare zone %#04x/%#04x/%#04x "
            "based on where it ENDS UP, not where it was)"
            % (
                z9 - a9,
                len(s9),
                len(objs),
                n_rows_sheet1 - 1,
                n_rows_sheet1,
                row_codes[0],
                row_codes[1],
                row_codes[2],
            )
        )
    else:
        n_extra_after = n_extra_before - 1
        layout = D.menu_sheet_layout(n_extra_after)
        N_before = objs[0]["N"]
        N_after = 1 + len(layout)
        if not layout:
            # No extra rows left: tabla[6] goes back to N=1, and with N=1
            # the factory declares the 2 strips 0xAE/0xAF as NULL in the
            # header. Nothing is rebuilt: it's repointed to the factory
            # header, which is still there byte for byte (see
            # `hdr_n1_de_fabrica`).
            for o in objs:
                hdr = hdr_n1_de_fabrica(b, o["hdr"])
                factory_hdr_by_ordinal[o["ordinal"]] = hdr
                # SECOND part, independent of the strips: the LIGHT. It is
                # not in the header, it is in the tabla[11] object the
                # ENTER hook points at, and the factory header the line
                # above restores points at that SAME index -- so without
                # this, N=1 comes back with the paging key still lit
                # (measured on the device).
                pares = factory_enter_hook_n1(b, hdr)
                if pares:
                    paging_light_by_ordinal[o["ordinal"]] = pares
            print(
                "the menu is left with NO extra rows: N %d -> 1. The "
                "header goes back to the FACTORY one (with the 2 strips "
                "%#04x/%#04x null), located by byte-for-byte identity "
                "inside the blob itself: %s -- not rebuilt, repointed."
                % (
                    N_before,
                    FRANJAS_N1[0],
                    FRANJAS_N1[1],
                    {k: hex(v) for k, v in factory_hdr_by_ordinal.items()},
                )
            )
            if paging_light_by_ordinal:
                print(
                    "   and the paging-key LIGHT is turned off too (a "
                    "SECOND thing, not the strips): the ENTER hook's "
                    "tabla[11] object goes back to the factory one, "
                    "WITHOUT %s -- %s"
                    % (
                        "/".join("{%04X,7F}" % x for x in ACCIONES_LUZ_PAGINADO),
                        {
                            ordi: [(k, hex(off)) for k, off in pares]
                            for ordi, pares in paging_light_by_ordinal.items()
                        },
                    )
                )
            else:
                print(
                    "   the ENTER hook does not carry any paging-light "
                    "action: nothing to turn off."
                )
        print(
            "repackaged: %d extra row(s) -> %d sheet(s) of at most %d each "
            "(layout %s); trailer's N: %d -> %d"
            % (
                n_extra_after,
                len(layout),
                D.MAX_ROWS_PER_SHEET,
                layout,
                N_before,
                N_after,
            )
        )
        new_rows_by_object = {
            ordinal: rows[:k_row] + rows[k_row + 1 :]
            for ordinal, rows in rows_by_object.items()
        }
        for o in objs:
            pages = D.partition(new_rows_by_object[o["ordinal"]], layout)
            pages_by_object[o["ordinal"]] = pages
            rels = []
            for pg in pages:
                entradas = [
                    (row_codes[k], ident, 0x7F) for k, (_t, ident) in enumerate(pg)
                ]
                entradas.append(menu_foot_by_object[o["ordinal"]])
                rels.append(len(s9))
                s9 += D.build_key_register(entradas)
            rel_keyregs_by_object[o["ordinal"]] = rels
        print(
            "\nsection [9]: %d B -> %d B (+%d new key register(s))"
            % (z9 - a9, len(s9), sum(len(v) for v in rel_keyregs_by_object.values()))
        )

    blob1 = bytearray(relocate.relocate(b, {9: bytes(s9)}))
    sec1 = relocate.sections(blob1)
    off_keyregs_by_object = {
        ordinal: [sec1[9][0] + r for r in rels]
        for ordinal, rels in rel_keyregs_by_object.items()
    }

    # -------------------------------------------------------- the new tail ---
    close1 = u24(blob1, 4) - BASE
    out = bytearray(blob1[: close1 - 2])
    estructuras: list[tuple[str, int, int]] = []
    relleno_total = 0

    def emitir(blk: bytes, etq: str | None = None) -> int:
        nonlocal relleno_total
        at = len(out)
        if etq is not None and D.crosses_page(at, len(blk)):
            gap = D.PAGE_SIZE - (at % D.PAGE_SIZE)
            out.extend(b"\x00" * gap)
            relleno_total += gap
            at = len(out)
        out.extend(blk)
        if etq is not None:
            estructuras.append((etq, at, len(blk)))
        return at

    nuevos_trailers: dict[int, int] = {}
    if es_fabrica:
        for o in objs:
            off_keyreg = sec1[9][0] + keyregs_nuevos[o["ordinal"]]
            r = jump_id_by_object[o["ordinal"]]
            h1 = sheet1_by_object[o["ordinal"]]
            rows1 = h1["rows"]
            remaining_rows = [f for f in rows1 if f["id"] != r["id_jump"]]
            if len(remaining_rows) != len(rows1) - 1:
                raise SystemExit(
                    "menu %d: could not isolate id_jump %d's row among %s"
                    % (o["ordinal"], r["id_jump"], [f["id"] for f in rows1])
                )
            # Check (h), per object: with ALL the rows, the generator has
            # to reproduce the ORIGINAL program and key register byte for
            # byte. If not, it's inventing something (the ATTR, an icon,
            # the keyreg order) and that would leak into the factory
            # anchor screen without anyone noticing.
            ident_prog = reduced_sheet1_program(
                o["prologo"],
                rows1,
                h1["attr"],
                pie=sheet1_foot_by_object[o["ordinal"]],
                own_off=o["prog"],
            )
            if bytes(b[o["prog"] : o["prog"] + len(ident_prog)]) != ident_prog:
                raise SystemExit(
                    "CHECK (h) FAILED on menu %d: rebuilding sheet 1 with "
                    "its %d rows does NOT reproduce the original program "
                    "byte for byte (original %s..., rebuilt %s...): the "
                    "generator is inventing instead of copying from the factory"
                    % (
                        o["ordinal"],
                        len(rows1),
                        b[o["prog"] : o["prog"] + 24].hex(" "),
                        ident_prog[:24].hex(" "),
                    )
                )
            ident_kr = keyreg_without_row(h1["kr"], rows1, row_codes, None)
            largo_kr = 1 + 4 * b[o["keyreg"]]
            if bytes(b[o["keyreg"] : o["keyreg"] + largo_kr]) != ident_kr:
                raise SystemExit(
                    "CHECK (h) FAILED on menu %d: rebuilding sheet 1's key "
                    "register without removing anything does NOT reproduce "
                    "the original byte for byte" % o["ordinal"]
                )
            at = len(out)
            new_prog = emitir(
                reduced_sheet1_program(
                    o["prologo"],
                    remaining_rows,
                    h1["attr"],
                    pie=sheet1_foot_by_object[o["ordinal"]],
                    own_off=at,
                ),
                "sheet 1 (trimmed) of menu %d" % o["ordinal"],
            )
            if new_prog != at:
                raise SystemExit(
                    "the trimmed sheet-1 program for %d moved from %#08x "
                    "to %#08x: aborting instead of recalculating the SWITCH"
                    % (o["ordinal"], at, new_prog)
                )
            new_slot = emitir(
                bytes([D.K_MENU]) + D.p(off_keyreg) + D.p(new_prog),
                "sheet 1 (trimmed) slot of menu %d" % o["ordinal"],
            )
            slots_menu = [new_slot] + o["slots"][1:]
            tr = emitir(
                bytes([0x00])
                + D.p(o["hdr"])
                + len(slots_menu).to_bytes(2, "little")
                + b"".join(D.p(s) for s in slots_menu),
                "trailer of menu %d" % o["ordinal"],
            )
            nuevos_trailers[o["ordinal"]] = tr
            print(
                "   object %3d: sheet 1 trimmed (row %#04x removed), new "
                "slot %#08x, new trailer %#08x (%d extra sheet(s) LEFT UNTOUCHED)"
                % (o["ordinal"], r["cod"], new_slot, tr, len(o["slots"]) - 1)
            )
    else:
        for o in objs:
            pages = pages_by_object[o["ordinal"]]
            offs_keyreg = off_keyregs_by_object[o["ordinal"]]
            slots_extra = []
            for k, pg in enumerate(pages):
                at = len(out)
                prog_k = emitir(
                    D.program_menu_sheet(
                        o["prologo"],
                        [t for t, _ident in pg],
                        pie=sheet1_foot_by_object[o["ordinal"]],
                        own_off=at,
                    ),
                    "program for sheet %d of %d" % (k + 2, o["ordinal"]),
                )
                if prog_k != at:
                    raise SystemExit(
                        "the program for sheet %d of %d moved from %#08x "
                        "to %#08x: aborting instead of recalculating the SWITCH"
                        % (k + 2, o["ordinal"], at, prog_k)
                    )
                slots_extra.append(
                    emitir(
                        bytes([D.K_MENU]) + D.p(offs_keyreg[k]) + D.p(prog_k),
                        "slot for sheet %d of %d" % (k + 2, o["ordinal"]),
                    )
                )
            slots_menu = [o["slot"]] + slots_extra
            # N=1: the header goes back to being the FACTORY one (with the
            # 2 strips 0xAE/0xAF null). Not rebuilt: repointed to the one
            # still alive-but-dead at its original offset, byte for byte
            # equal to the factory (the addition never erased it, only
            # abandoned it). "Copy from the factory instead of inventing".
            hdr_usar = (
                factory_hdr_by_ordinal[o["ordinal"]]
                if len(slots_menu) == 1
                else o["hdr"]
            )
            tr = emitir(
                bytes([0x00])
                + D.p(hdr_usar)
                + len(slots_menu).to_bytes(2, "little")
                + b"".join(D.p(s) for s in slots_menu),
                "trailer of menu %d" % o["ordinal"],
            )
            nuevos_trailers[o["ordinal"]] = tr
            print(
                "   object %3d: %d extra sheet(s) %s, new trailer %#08x "
                "(sheet 1 %#08x LEFT UNTOUCHED, header %#08x%s)"
                % (
                    o["ordinal"],
                    len(slots_extra),
                    [hex(s) for s in slots_extra],
                    tr,
                    o["slot"],
                    hdr_usar,
                    " = the FACTORY one restored, with the 2 strips null"
                    if len(slots_menu) == 1
                    else "",
                )
            )

    # in-place repointing of the 3 tabla[6] entries -- a pointer VALUE
    # change, not a data move (same criterion as `write.py --repoint`)
    repuntes = []
    for o in objs:
        pos = D.T6 + 3 + 3 * o["ordinal"]
        out[pos : pos + 3] = D.p(nuevos_trailers[o["ordinal"]])
        repuntes.append(pos)
    print(
        "\ntabla[6] repointed in-place at the 3 ordinals %s (nothing else "
        "moved in tabla[6])" % [o["ordinal"] for o in objs]
    )

    # BACK TO N=1: same kind of in-place pointer VALUE change, this time on
    # tabla[11], so ENTERING the menu stops lighting the paging keys. The
    # offsets come from `factory_enter_hook_n1` (factory bytes found
    # inside the blob), not from anything rebuilt here.
    if paging_light_by_ordinal:
        t11_vivo = relocate.sections(out)[11][0]
        hechos = []
        for ordinal, pares in sorted(paging_light_by_ordinal.items()):
            for k, off_fab in pares:
                pos = t11_vivo + 2 + 3 * k
                if bytes(out[pos : pos + 3]) == D.p(off_fab):
                    continue  # already pointing at the factory one
                out[pos : pos + 3] = D.p(off_fab)
                repuntes.append(pos)
                hechos.append((ordinal, k, pos, off_fab))
        print(
            "tabla[11] repointed in-place at %d entry/entries so N=1 does "
            "NOT light the paging keys: %s"
            % (
                len(hechos),
                [
                    "ord %d -> [11][%d] @%#08x = %#08x" % (o, k, p, f)
                    for o, k, p, f in hechos
                ],
            )
        )
    repuntes.sort()

    out = D.close_blob(out)
    fresh = bytes(out)

    # ------------------------------------------------------------ checks ---
    print("\n" + "=" * 70)
    print("CHECKS")
    print("=" * 70)

    D.T6 = t6_vivo  # still the same absolute offset in `fresh`

    # ---- (a) THE COMMANDS OF WHAT'S LEFT, exhaustive. This is the one that
    # prevents a hang: `cmd_setup_ir` walks section [5] with `k1*3+1 ;
    # follow ; k2*3+3 ; follow` WITHOUT checking range, so a (k1,k2) that
    # doesn't resolve hangs the remote. ALL (k1,k2) of ALL remaining
    # devices are re-resolved with the firmware's exact arithmetic.
    new_devs5 = D.read_section5(fresh)
    if new_devs5 != devs5:
        raise SystemExit(
            "CHECK (a) FAILED: section [5] changed and it shouldn't have "
            "-- this tool never touches it"
        )
    fallas_a = []
    total_a = 0
    for k1, dinfo in enumerate(new_devs5):
        if k1 == a.index:
            continue  # the deleted one: reachability from the menu isn't required
        for k2 in range(dinfo["n"]):
            total_a += 1
            cmd_id = (k1 << 8) | k2
            reg, reason = D.resolve_section5(fresh, cmd_id)
            if reason or reg != dinfo["regs"][k2]:
                fallas_a.append((k1, k2, reason))
    if fallas_a:
        raise SystemExit(
            "CHECK (a) FAILED: %d/%d commands don't resolve: %s"
            % (len(fallas_a), total_a, fallas_a[:5])
        )
    # and the ORPHAN also has to keep resolving: the deleted device's
    # sub-table stays in [5] (that's why there's no valid gap or
    # renumbering), and if a physical button or an activity pointed to it,
    # it has to respond.
    fallas_h = []
    for k2 in range(new_devs5[a.index]["n"]):
        reg, reason = D.resolve_section5(fresh, (a.index << 8) | k2)
        if reason or reg != new_devs5[a.index]["regs"][k2]:
            fallas_h.append((a.index, k2, reason))
    if fallas_h:
        raise SystemExit(
            "CHECK (a) FAILED: device %d's ORPHANED sub-table stopped "
            "resolving: %s" % (a.index, fallas_h[:5])
        )
    print(
        "(a) OK -- %d command(s) of the %d device(s) left resolve EXACTLY "
        "through section [5] (k1,k2 in range, firmware arithmetic, "
        "exhaustive). The %d of orphan k1=%d too: [5] stays byte for byte "
        "the same, so there's no gap OR renumbering -- cmd_setup_ir can't "
        "see an out-of-range index because of this operation."
        % (
            total_a,
            len(new_devs5) - 1,
            new_devs5[a.index]["n"],
            a.index,
        )
    )

    # ---- (b) the BUTTONS of what's left, identical ----
    chain_before = relocate.chain(b)
    chain_after = relocate.chain(fresh)
    conservados = {k: v for k, v in chain_after.items() if k in chain_before}
    if conservados != chain_before:
        missing = set(chain_before) - set(chain_after)
        cambian = {
            k
            for k in set(chain_before) & set(chain_after)
            if chain_before[k] != chain_after[k]
        }
        raise SystemExit(
            "CHECK (b) FAILED: the pre-existing button chain changed -- "
            "%d disappeared, %d changed" % (len(missing), len(cambian))
        )
    print(
        "(b) OK -- reubicar.chain(): %d buttons before, %d after; the %d "
        "pre-existing ones are IDENTICAL"
        % (len(chain_before), len(chain_after), len(chain_before))
    )

    # ---- (c) no new table crosses 64 KB ----
    cruces = [(etq, off, L) for etq, off, L in estructuras if D.crosses_page(off, L)]
    if cruces:
        raise SystemExit(
            "CHECK (c) FAILED: new structures crossing a 64 KB boundary: %s" % cruces
        )
    print(
        "(c) OK -- none of the %d new structures cross a 64 KB boundary "
        "(alignment padding: %d B)" % (len(estructuras), relleno_total)
    )

    # ---- (d) THE GATE, the same one that decides whether `write.py`
    # actually writes, with its NEGATIVE. `import grabar` does NOT touch
    # USB (`ctypes.CDLL` lives inside `cargar()`, which is never called here).
    import write  # noqa: PLC0415  -- only for nada_se_movio, see above

    extra = {q + k for q in repuntes for k in range(3)}
    ok_d, difs = write.nothing_moved(b, fresh, extra)
    sin_declarar = sorted(set(difs) - write.ALLOWED - extra)
    if not ok_d or sin_declarar:
        raise SystemExit(
            "CHECK (d) FAILED: nada_se_movio says there are %d undeclared "
            "byte(s): %s" % (len(sin_declarar), [hex(x) for x in sin_declarar])
        )
    ok_neg, _ = write.nothing_moved(b, fresh, set())
    if ok_neg:
        raise SystemExit(
            "CHECK (d) FAILED ON THE NEGATIVE: without declaring any "
            "--repunta the gate still said yes -- it isn't looking at "
            "what changed"
        )
    print(
        "(d) OK -- grabar.nada_se_movio: %d difference(s), 0 undeclared "
        "with the %d --repunta below. NEGATIVE: without declaring them it "
        "gives NO, as it should." % (len(difs), len(repuntes))
    )

    # ---- (e) configcheck ----
    pruebas_cfg = configcheck.revisar(fresh)
    if not all(ok for _n, ok, _d in pruebas_cfg):
        raise SystemExit(
            "CHECK (e) FAILED: configcheck.revisar -- %s"
            % [(n, d) for n, ok, d in pruebas_cfg if not ok]
        )
    print(
        "(e) OK -- configcheck.revisar all green (%d/%d)"
        % (len(pruebas_cfg), len(pruebas_cfg))
    )

    # ---- (f) THE ACTIVITIES ----
    # Looking at section [0] (the state-variable table) alone isn't
    # enough: it's IDENTICAL byte for byte with 0 and with 2 added
    # devices, so its count and id set are constant by construction and
    # can't change their verdict on anything -- an invariant that's always
    # green discriminates nothing. What DOES discriminate is the engine:
    # section [14], whose transitive closure through tabla[11] reaches the
    # `cmd_id`s an activity actually emits.
    lay_ok, lay_tot, lay_neg = activities.engine_layout_check(fresh)
    if lay_tot and (lay_ok != lay_tot or max(lay_neg.values()) >= lay_ok):
        raise SystemExit(
            "CHECK (f) FAILED: section [14]'s layout doesn't separate "
            "(positive %d/%d, negatives %s): the activity engine reader "
            "isn't measuring anything, the rest of this check can't be "
            "trusted" % (lay_ok, lay_tot, lay_neg)
        )
    dest11_n = relocate.table(fresh, relocate.sections(fresh)[11][0])
    k1_act_after, n_obj_act = activities.engine_k1(fresh, dest11_n)
    if k1_act_after != act_before_k1:
        raise SystemExit(
            "CHECK (f) FAILED: the activity engine's device set changed "
            "(before %s, now %s) and this tool doesn't touch section [14]"
            % (sorted(act_before_k1), sorted(k1_act_after))
        )
    fallas_f = []
    for cmd_id in sorted(activities.engine_commands(fresh, dest11_n)):
        k1c = cmd_id >> 8
        k2c = cmd_id & 0xFF
        reg, reason = D.resolve_section5(fresh, cmd_id)
        if reason or not (0 <= k1c < len(new_devs5) and k2c < new_devs5[k1c]["n"]):
            fallas_f.append((hex(cmd_id), reason or "out of range"))
        elif reg != new_devs5[k1c]["regs"][k2c]:
            fallas_f.append((hex(cmd_id), "resolves to a different record"))
    if fallas_f:
        raise SystemExit(
            "CHECK (f) FAILED: %d command(s) an activity can emit do NOT "
            "resolve against section [5] -- an activity would be pointing "
            "at a nonexistent device: %s" % (len(fallas_f), fallas_f[:6])
        )
    cmds_act_after = activities.engine_commands(fresh, dest11_n)
    menu_act_after = activities.activities_menu(fresh, dest11_n)
    if (act_menu_before is None) != (menu_act_after is None) or (
        act_menu_before is not None
        and [f["act"] for f in act_menu_before["rows"]]
        != [f["act"] for f in menu_act_after["rows"]]
    ):
        raise SystemExit(
            "CHECK (f) FAILED: the activities menu changed and it "
            "shouldn't have (before %s, now %s)" % (act_menu_before, menu_act_after)
        )
    print(
        "(f) OK -- the activity engine (section [14], layout %d/%d exact "
        "against negatives %s) reaches %d object(s) and devices %s, the "
        "SAME as before; its %d command(s) all resolve through section "
        "[5] -- no activity references a nonexistent device. The "
        "activities menu (ordinal %s) is left with the same %d row(s)."
        % (
            lay_ok,
            lay_tot,
            lay_neg,
            n_obj_act,
            sorted(k1_act_after),
            len(cmds_act_after),
            None if menu_act_after is None else menu_act_after["ordinal"],
            0 if menu_act_after is None else len(menu_act_after["rows"]),
        )
    )

    # ---- (g) NAVIGATION: cut, and what's seen = what's touched ----
    # (g1) no sheet (1 or extra) of the 3 menus references the cut
    # id_jump(s) again. Read DIRECTLY by known ordinal (trailer -> slots ->
    # keyreg), not by `menu_objects()`'s heuristic.
    cortados = (
        {o["ordinal"]: {jump_id_by_object[o["ordinal"]]["id_jump"]} for o in objs}
        if es_fabrica
        else {o["ordinal"]: {loc["id_jump"]} for o in objs}
    )
    referencian = []
    for o in objs:
        t6_entry_n = D.u24(fresh, D.T6 + 3 + 3 * o["ordinal"]) - BASE
        tr_n = D.read_trailer(fresh, t6_entry_n, max_n=200)
        if tr_n is None:
            raise SystemExit(
                "CHECK (g) FAILED: menu %d's new trailer doesn't parse" % o["ordinal"]
            )
        for sp in tr_n["slots"]:
            s = D.read_slot(fresh, sp - BASE)
            if s is None:
                continue
            kr = D.read_key_register(fresh, s["keyreg"] - BASE) or []
            if any(ident in cortados[o["ordinal"]] for _c, ident, _cl in kr):
                referencian.append(o["ordinal"])
    if referencian:
        raise SystemExit(
            "CHECK (g) FAILED: device %d is still referenced from menu %s"
            % (a.index, sorted(set(referencian)))
        )
    # (g2) SYNC: the row drawn at position k declares position k's touch
    # zone, on ALL sheets of the 3 menus, and every row still leads to the
    # SAME screen as before.
    map_after = {
        o["ordinal"]: mapa_de_menu(fresh, o["ordinal"], menu_order, dest11_n)
        for o in objs
    }
    for ordinal, rows_after in map_after.items():
        screens_before = [f["screen_ordinal"] for f in map_before[ordinal]]
        screens_after = [f["screen_ordinal"] for f in rows_after]
        cortada = cut_screen_by_object[ordinal]
        esperado = [p for p in screens_before if p != cortada]
        if screens_after != esperado:
            raise SystemExit(
                "CHECK (g) FAILED: menu %d now leads to screens %s and it "
                "had to lead to %s (before %s, minus the %d that was cut) "
                "-- the rows ended up shifted"
                % (ordinal, screens_after, esperado, screens_before, cortada)
            )
    print(
        "(g) OK -- navigation to device %d cut in the 3 menus, and on ALL "
        "their sheets the row drawn at position k declares position k's "
        "touch zone (what's seen = what's touched). Every remaining row "
        "still opens its SAME screen: %s"
        % (
            a.index,
            {k: [f["screen_ordinal"] for f in v] for k, v in map_after.items()},
        )
    )

    # ---- (i) PAGING, THE TWO HALVES. With N=1 there is nowhere to page to,
    # and the factory does TWO separate things: it declares the 0xAE/0xAF
    # side strips NULL in the header (so the firmware eats the touch and
    # never reaches the global pager) AND it does not carry the class-0x3F
    # action that lights the key. Doing only the first one is the measured
    # defect ("only one page left and the touch LED to change page is still
    # on"), and no earlier check looked at it.
    new_t6 = relocate.sections(fresh)[6][0]
    new_t11 = relocate.sections(fresh)[11][0]
    fallas_i, detail_i = [], []
    for o in objs:
        ordinal = o["ordinal"]
        tr = D.u24(fresh, new_t6 + 3 + 3 * ordinal) - BASE
        N = int.from_bytes(fresh[tr + 4 : tr + 6], "little")
        hdr = D.u24(fresh, tr + 1) - BASE
        cab = arrow_backlight._atomos_cab(fresh, hdr)
        nulas = [
            c
            for c, op, cls in cab
            if c in FRANJAS_N1 and op == 0 and cls == 0 and N == 1
        ]
        luces = []
        for cod, op, cls in cab:
            if cls != arrow_backlight.CATEGORY_ACTION or cod != arrow_backlight.CODE_ENTER:
                continue
            _off, atomos = arrow_backlight._obj11(fresh, new_t11, op)
            luces += [
                x
                for x in atomos
                if x[1] == arrow_backlight.CATEGORY_ACTION and x[0] in ACCIONES_LUZ_PAGINADO
            ]
        if N == 1:
            if len(nulas) != len(FRANJAS_N1):
                fallas_i.append(
                    "menu %d has N=1 and declares %d of the %d strips "
                    "%s as null (%s): the touch band would keep paging"
                    % (
                        ordinal,
                        len(nulas),
                        len(FRANJAS_N1),
                        [hex(f) for f in FRANJAS_N1],
                        ["{%02X,%04X,%02X}" % x for x in cab],
                    )
                )
            if luces:
                fallas_i.append(
                    "menu %d has N=1 and its ENTER hook still lights the "
                    "paging keys (%s): the LED stays on with a single page"
                    % (ordinal, " ".join("{%04X,%02X}" % x for x in luces))
                )
        detail_i.append(
            "%d: N=%d, %d null strip(s), %d light action(s)"
            % (ordinal, N, len(nulas), len(luces))
        )
    if fallas_i:
        raise SystemExit("CHECK (i) FAILED: " + "; ".join(fallas_i))
    print(
        "(i) OK -- paging, THE TWO HALVES: with N=1 the header declares the "
        "2 strips %#04x/%#04x null AND the ENTER hook carries no class-0x3F "
        "light action (%s -> %s). With N>1 nothing is required: %s"
        % (
            FRANJAS_N1[0],
            FRANJAS_N1[1],
            "/".join("{%04X,7F}" % x for x in ACCIONES_LUZ_PAGINADO),
            "PCA9532 channels off",
            "; ".join(detail_i),
        )
    )

    print("\nblob: %d B -> %d B  (+%d)" % (len(b), len(fresh), len(fresh) - len(b)))

    print("\n" + "=" * 70)
    print("REPOINTS TO DECLARE (%d, 3 B each):" % len(repuntes))
    print("   " + " ".join("--repunta %#08x" % q for q in repuntes))
    print("\nWRITE COMMAND (a human runs this, this tool does NOT write):")
    print(
        "   python3 write.py %s \\\n     --referencia %s \\\n     %s"
        % (
            a.ezhex or "<output.EZHex>",
            a.blob,
            " ".join("--repunta %#08x" % q for q in repuntes),
        )
    )
    print("=" * 70)

    if a.json:
        import json  # noqa: PLC0415

        pathlib.Path(a.json).write_text(
            json.dumps(
                {
                    "index": a.index,
                    "name": device_name,
                    "commands": n,
                    "de_fabrica": es_fabrica,
                    "se_pierde": frases,
                    "activities": inf_act,
                    "repuntes": ["%#08x" % q for q in repuntes],
                    "checks": [
                        "a: %d command(s) of what's left + %d of the "
                        "orphan, exhaustive through section [5]"
                        % (total_a, new_devs5[a.index]["n"]),
                        "b: reubicar.chain, %d identical button(s)" % len(chain_before),
                        "c: %d new structure(s), none cross 64 KB" % len(estructuras),
                        "d: nada_se_movio (%d difference(s), 0 undeclared) "
                        "+ its negative" % len(difs),
                        "e: configcheck.revisar %d/%d"
                        % (len(pruebas_cfg), len(pruebas_cfg)),
                        "f: activities -- engine over %s, %d command(s) "
                        "that resolve, menu intact"
                        % (sorted(k1_act_after), len(cmds_act_after)),
                        "g: navigation cut + touch zones in sync on every "
                        "sheet of the 3 menus",
                        "h: sheet 1 rebuilt with ALL its rows reproduces "
                        "the original byte for byte"
                        if es_fabrica
                        else "h: not applicable (sheet 1 isn't touched)",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print("JSON report: %s" % a.json)

    if a.salida:
        pathlib.Path(a.salida).write_bytes(fresh)
        print("written %s" % a.salida)
        if a.ezhex:
            # by SUBPROCESS, same as `add_device.py`: the EZHex wrapper
            # (header + installer checksum) is built by `ezhex.py`, not
            # reimplemented here.
            if not a.plantilla:
                raise SystemExit("--ezhex needs --plantilla")
            import subprocess  # noqa: PLC0415

            import sys  # noqa: PLC0415

            r = subprocess.run(
                [sys.executable, "ezhex.py", "armar", a.plantilla, a.salida, a.ezhex],
                capture_output=True,
                text=True,
                cwd=str(pathlib.Path(__file__).parent),
                check=False,
            )
            print("ezhex.py armar:")
            for ln in (r.stdout or "").strip().splitlines():
                print("   " + ln)
            if r.returncode:
                print((r.stderr or "").strip())
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
