#!/usr/bin/env python3
"""Reads the CONNECTED remote's raw config-flash baseline and derives, from
those bytes alone, the per-remote facts that today are hardcoded elsewhere
in this project (`config_work/add_device.py`'s `PANTALLAS_FABRICA_BASELINE`,
`config_work/list_devices.py`'s `K1_DE_FABRICA`, and -- everywhere -- the literal
path `backups/config_raw.bin`). See ESTADO.md / the audit this file is
part of: "el baseline esta hardcodeado... tiene que leer el baseline del
control conectado y derivar todo de ahi."

**READ-ONLY.** The only libconcord primitive this file calls that touches
the device at all is `get_identity()` (identification) and `read_flash_at()`
(a flash READ, never a write or erase -- same primitive
`config_work/flash_dump.c` already uses, which is how `backups/config_raw.bin`
itself was produced). Nothing here calls `update_configuration`,
`erase_config`, `erase_flash`, or any `write_*`/`erase_*` symbol.

## What IS verified by running this file, this session

    python3 read_flash_baseline.py --selftest

reads `backups/config_raw.bin` straight off disk (no USB at all) and runs
`derivar()` on it -- the exact function that would run on a live raw dump --
checking its output against the values this project has independently
measured by hand many times over (GSPM magic, PTYY close, tabla[6] count
156, section [5] device count 3, and a stable sha256). This is a REAL run,
today, with a REAL assertion, not a description of intent.

## What is NOT verified this session

**The live path** (`read_raw_baseline()`, and `main()` without
`--selftest`) needs a Harmony One plugged in. No remote is connected in
this session (see the project brief's PROHIBIDO section), so:

  * whether `read_flash_at()` behaves the same across the WHOLE config
    region on this specific unit as `flash_dump.c` measured for the
    ranges it was tried on,
  * whether `get_config_bytes_total()` reports a length that actually
    covers the full GSPM...PTYY extent (assumed here, not measured),
  * and whether `get_serial()` returns a non-empty value on a Harmony One
    at all (its signature takes `p` in {1,2,3} and concatenates them; some
    remotes may not populate `ri.serial1..3` over this protocol)

are all **[SUPUESTO]**. Marked here instead of silently assumed.

Usage:
    python3 read_flash_baseline.py --selftest
    python3 read_flash_baseline.py --salida baseline.bin   # LIVE, needs the remote
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import add_device as D  # only for u16/u24/BASE/MAESTRO_S5/MAESTRO_T6 -- format helpers
import write  # only for LIB (the dylib path) and CB (the progress callback type)

ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKUPS = ROOT / "backups"

#: `read_flash_at` reads in one shot easily enough per call, but
#: `config_work/flash_dump.c` chunks it (16 KiB) "so a failure localises to
#: a range instead of losing everything". Copied, not reinvented.
CHUNK = 0x4000


def _cargar_lectura():
    """Loads libconcord with ONLY the read-only symbols this file needs
    declared. Deliberately separate from `grabar.cargar()`: that loader is
    scoped to the write path on purpose (its own docstring: "the only tool
    in the project that writes to the device"), and mixing `read_flash_at`
    into it would blur that boundary for no benefit -- this file is the one
    new place that needs it.
    """
    lib = ctypes.CDLL(write.LIB)
    lib.init_concord.restype = ctypes.c_int
    lib.deinit_concord.restype = ctypes.c_int
    lib.get_identity.argtypes = [write.CB, ctypes.c_void_p]
    lib.get_identity.restype = ctypes.c_int
    lib.lc_strerror.argtypes = [ctypes.c_int]
    lib.lc_strerror.restype = ctypes.c_char_p
    for f in ("get_arch", "get_skin", "get_fw_ver_maj", "get_fw_ver_min"):
        getattr(lib, f).restype = ctypes.c_int
    for f in ("get_config_bytes_used", "get_config_bytes_total"):
        getattr(lib, f).restype = ctypes.c_int
    lib.is_config_dump_supported.restype = ctypes.c_int
    lib.get_serial.argtypes = [ctypes.c_int]
    lib.get_serial.restype = ctypes.c_char_p
    lib.read_flash_at.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_ubyte),
    ]
    lib.read_flash_at.restype = ctypes.c_int
    return lib


def read_live_identity(timeout: float = 30.0) -> dict:
    """Identifies the connected remote. Read-only (`init_concord` +
    `get_identity`, same as `config_work/read_config.py`). Does NOT read flash.

    Returns `{'arch', 'skin', 'fw_mayor', 'fw_menor', 'config_usada',
    'config_total', 'volcado_soportado', 'serial'}`. Raises `RuntimeError`
    if no remote is found or identification fails.
    """
    lib = _cargar_lectura()
    if lib.init_concord():
        raise RuntimeError("could not start libconcord (init_concord)")
    try:
        if lib.get_identity(write.CB(lambda *x: None), None):
            raise RuntimeError("remote not found (get_identity)")
        serial = "".join(
            (lib.get_serial(p) or b"").decode(errors="replace") for p in (1, 2, 3)
        )
        return {
            "arch": lib.get_arch(),
            "skin": lib.get_skin(),
            "fw_mayor": lib.get_fw_ver_maj(),
            "fw_menor": lib.get_fw_ver_min(),
            "config_usada": lib.get_config_bytes_used(),
            "config_total": lib.get_config_bytes_total(),
            "dump_supported": bool(lib.is_config_dump_supported()),
            "serial": serial,
        }
    finally:
        lib.deinit_concord()


#: Prefix of the MEASURED progress lines this file prints while it reads
#: (`app/progress.py::parsear_linea_lectura()` parses them, and
#: `app/remote_status.py` pipes them through). Deliberately NOT a substring of
#: anything else this file prints: the final `for k, v in info.items()` dump
#: uses `"%-24s %s"`, so a key would have to be literally named `LEIDO` to
#: collide, and the JSON line always starts with `{`.
#:
#:     ETAPA <slug>: <text for a human>
#:     LEIDO <bytes so far>/<bytes total>
#:
#: `LEIDO` is emitted ONCE PER 16 KiB CHUNK, straight after the chunk landed
#: -- the number is bytes that actually came back over USB, not a timer. That
#: is the whole point: the bar has to be measured, not animated.
PREFIJO_ETAPA = "ETAPA "
PREFIJO_LEIDO = "LEIDO "


def read_raw_baseline(
    target: pathlib.Path,
    *,
    longitud: int | None = None,
    avisar=None,
) -> dict:
    """LIVE read of the config-flash region (`BASE=0x040000` onward) via
    `read_flash_at`, chunked exactly like `config_work/flash_dump.c`.
    Writes the raw bytes to `target` and returns
    `read_live_identity()`'s dict plus `{'bytes_leidos', 'chunks_fallidos',
    'destino', 'valido', 'problemas', 'motivo'}` (`derivar()`'s keys are
    merged in too: `'magia_ok'`, `'close'`, `'ptyy_ok'`, `'sha256'`, ...).

    `avisar(text)` -- optional -- is called with one already-formatted
    progress line per milestone and per 16 KiB chunk (see `PREFIJO_ETAPA` /
    `PREFIJO_LEIDO`). `main()` passes a printer, so a caller reading this
    process's stdout line by line sees the read advance in real time instead
    of staring at nothing for the ~80 USB transactions it takes. It never
    raises out of here: a broken `avisar` must not be able to abort a read.

    ALWAYS RETURNS -- never raises for "the dump doesn't validate" (chunks
    failed, bad magic, bad close): that comes back as `valido=False` with
    `reason` in plain language, so a caller can tell "remote not found"
    apart from "found it, but what it handed back isn't a config" -- two
    different facts a caller needs told apart. The ONLY thing that still
    raises `RuntimeError` here is not finding/identifying the remote at all
    (`init_concord`/`get_identity` failing) or an unexpected arch: those mean
    there was nobody to read FROM in the first place.

    `longitud` defaults to the device's own `get_config_bytes_total()` --
    NOT a hardcoded size (the old `backups/config_raw.bin` is exactly
    `config_total` bytes for THIS remote; a different remote reports its
    own `config_total`, and that is what sizes the read for it).

    UNVERIFIED THIS SESSION -- see this module's docstring. Never call this
    against a remote you have not confirmed you intend to read from; it
    does not ask for confirmation itself (that belongs one layer up, in
    whatever calls this from the app).
    """

    def _decir(text: str) -> None:
        if avisar is None:
            return
        try:
            avisar(text)
        except Exception:  # noqa: BLE001
            pass  # informar el avance NUNCA puede romper la lectura

    _decir(PREFIJO_ETAPA + "buscar: looking for your remote over USB")
    lib = _cargar_lectura()
    if lib.init_concord():
        raise RuntimeError("could not start libconcord (init_concord)")
    try:
        if lib.get_identity(write.CB(lambda *x: None), None):
            raise RuntimeError("remote not found (get_identity)")
        _decir(
            PREFIJO_ETAPA
            + "identidad: found it -- arch %d, skin %d, firmware %d.%d"
            % (
                lib.get_arch(),
                lib.get_skin(),
                lib.get_fw_ver_maj(),
                lib.get_fw_ver_min(),
            )
        )
        if lib.get_arch() != 12:
            raise RuntimeError(
                "arch %d is not 12: this reader was only measured against arch 12 "
                "(Harmony One). [ASSUMED] not tested on another arch." % lib.get_arch()
            )
        serial = "".join(
            (lib.get_serial(p) or b"").decode(errors="replace") for p in (1, 2, 3)
        )
        total = longitud if longitud is not None else lib.get_config_bytes_total()
        if not total or total <= 0:
            raise RuntimeError(
                "get_config_bytes_total() returned %r: nothing to size "
                "la lectura cruda" % total
            )

        buf = (ctypes.c_ubyte * total)()
        fallidos = []
        # The total goes out BEFORE the first chunk: whoever is drawing the
        # bar needs the denominator to exist before the numerator moves, or
        # the first thing on screen is a jump from nothing to 1/80.
        _decir(PREFIJO_ETAPA + "leer: reading its memory")
        _decir("%s0/%d" % (PREFIJO_LEIDO, total))
        for off in range(0, total, CHUNK):
            n = min(CHUNK, total - off)
            e = lib.read_flash_at(
                D.BASE + off,
                n,
                ctypes.cast(ctypes.byref(buf, off), ctypes.POINTER(ctypes.c_ubyte)),
            )
            if e:
                fallidos.append((off, e))
            # AFTER the transaction, with the bytes that actually came back.
            _decir("%s%d/%d" % (PREFIJO_LEIDO, off + n, total))

        datos = bytes(buf)
        target = pathlib.Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _decir(PREFIJO_ETAPA + "validar: checking what came back")
        target.write_bytes(datos)

        # A short or garbled read must not leave a plausible-looking .bin
        # behind for the rest of the pipeline to derive from. The blob
        # declares its own extent, so the check is free: the arch-12 cookie
        # (`remote_info.h`: 0x4D505347 == b"GSPM") and the close it points at.
        # Verified offline against `backups/config_raw.bin`: cierre=1316662,
        # +4 == 1316666 == the file's exact size.
        d = derivar(datos)
        problemas = []
        if fallidos:
            problemas.append("%d chunk(s) fallaron: %r" % (len(fallidos), fallidos[:5]))
        if not d["magia_ok"]:
            problemas.append("does not start with the GSPM cookie (%r)" % datos[:4])
        if not d["ptyy_ok"]:
            problemas.append(
                "the declared close (%r) does not lead to a PTYY marker" % d["close"]
            )

        info = {
            "arch": lib.get_arch(),
            "skin": lib.get_skin(),
            "fw_mayor": lib.get_fw_ver_maj(),
            "fw_menor": lib.get_fw_ver_min(),
            "config_usada": lib.get_config_bytes_used(),
            "config_total": lib.get_config_bytes_total(),
            "dump_supported": bool(lib.is_config_dump_supported()),
            "serial": serial,
            "bytes_leidos": len(datos),
            "chunks_fallidos": fallidos,
            "target": str(target),
            "valido": not problemas,
            "problemas": problemas,
        }
        info.update(d)
        if problemas:
            # CAMBIO (RE-HARMONY, estado real de 3 situaciones): antes esta
            # used to `raise RuntimeError(...)`, which CONTRADICTED this
            # function's docstring ("returns ... plus {...}") and left
            # `main()` with nothing to catch -- a dump that does not validate
            # ended in a raw traceback instead of a manageable result.
            # It is exactly the "remote plugged in, could not be
            # read" case that the caller has to be able to tell apart from
            # "disconnected": now it is RETURNED (never raised) with
            # `valido=False` and `reason` in plain text, and the caller decides.
            info["reason"] = (
                "the raw dump is not a coherent config -- %s. The bytes "
                "were left in %s for diagnosis, but they are not a baseline: do not "
                "use them as a reference or record them as one."
                % ("; ".join(problemas), target)
            )
        return info
    finally:
        lib.deinit_concord()


def parece_de_fabrica(datos: bytes) -> bool | None:
    """Whether this dump looks like it was NEVER relocated -- i.e. whether
    its counts can honestly be called "factory".

    This matters because a remote that has been written to at all is no
    longer its own baseline, and NOTHING in the counts says so: the grabbed
    config on this project's remote reports 158 screens and 5 devices just
    as confidently as the factory dump reports 156 and 3.

    The discriminator is a side effect of how this project grows a config:
    `reubicar.relocate()` moves section [9] to the tail and leaves the old
    bytes behind, but `table[6]`'s `keyreg` pointers were never repointed, so
    after any addition most of them resolve into the DEAD copy. Measured:

        backups/config_raw.bin            206/206 keyregs inside the live [9]
        output/config_empaquetada   14/226  (212 into the dead copy)

    So: all keyregs live => untouched dump; any stale one => already edited.
    Returns None when the question cannot be answered (no section [6]/[9], or
    no keyregs found), which callers must treat as "unknown", not as "yes".
    """
    try:
        import relocate

        sec = relocate.sections(datos)
        if 9 not in sec or 6 not in sec:
            return None
        a9, z9 = sec[9]
        objetivos = [
            t
            for _fo, t, etq in relocate.table6_chain(datos, sec)
            if etq == "tabla[6] slot keyreg"
        ]
        if not objetivos:
            return None
        return all(a9 <= t < z9 for t in objetivos)
    except Exception:
        return None


def derivar(datos: bytes) -> dict:
    """Facts derived FROM THE BYTES of a raw baseline dump, dynamically --
    no hardcoded ordinal counts or device counts. This is the function
    `--selftest` exercises against `backups/config_raw.bin`, and the one a
    live read hands its bytes to.

    Returns `{'magia_ok', 'cierre', 'ptyy_ok', 'sha256', 'n_pantallas_actual',
    'n_dispositivos_actual', 'parece_de_fabrica'}`. Does not raise on a
    malformed blob -- callers decide what to do with `magia_ok=False`/
    `ptyy_ok=False`; this function only measures.

    **The counts are `_actual`, not `_fabrica`, and the rename is the point.**
    An earlier version called them `n_pantallas_fabrica` /
    `n_dispositivos_fabrica` and computed them over whatever blob it was
    handed. Run against the config that is grabbed on this project's remote
    today, that reports "factory: 158 screens, 5 devices" -- which is false:
    the factory numbers are 156 and 3, and the extra two screens and two
    devices are this project's own additions. Since `app/history.py` stores
    the first sighting and never revises it, one live read of an
    already-written remote would have frozen a fiction. `parece_de_fabrica`
    is what says whether the counts may be read as factory ones at all; it
    is None when unknown, and a caller must not treat None as True.
    """
    magia_ok = datos[:4] == b"GSPM"
    # `close` (relative to BASE, same convention `grabar.nothing_moved` uses):
    # the 2-byte XOR checksum lives at [cierre-2:cierre], and the "PTYY" close
    # marker starts EXACTLY at [cierre:cierre+4] -- measured here against
    # `backups/config_raw.bin`, where `close+4 == len(datos)` to the byte.
    close = int.from_bytes(datos[4:7], "little") - D.BASE if magia_ok else -1
    ptyy_ok = (
        magia_ok
        and 0 <= close - 2
        and close + 4 <= len(datos)
        and datos[close : close + 4] == b"PTYY"
    )
    # sha256 over EXACTLY the declared GSPM extent (up to and including the
    # PTYY close), same definition `ezhex.split()`/`registro.record()` use
    # elsewhere for "the GSPM binary" -- not over any padding beyond it.
    extent = (
        datos[: close + 4] if magia_ok and 0 <= close + 4 <= len(datos) else datos
    )
    sha256 = hashlib.sha256(extent).hexdigest()

    n_pantallas_actual = None
    n_dispositivos_actual = None
    if magia_ok and len(datos) > D.MAESTRO_T6 + 4:
        t6 = D.u24(datos, D.MAESTRO_T6) - D.BASE
        if 0 <= t6 < len(datos) - 3:
            n_pantallas_actual = D.u16(datos, t6)
    if magia_ok and len(datos) > D.MAESTRO_S5 + 4:
        s5 = D.u24(datos, D.MAESTRO_S5) - D.BASE
        if 0 <= s5 < len(datos):
            n_dispositivos_actual = datos[s5]

    return {
        "magia_ok": magia_ok,
        "close": close,
        "ptyy_ok": ptyy_ok,
        "sha256": sha256,
        "n_pantallas_actual": n_pantallas_actual,
        "n_dispositivos_actual": n_dispositivos_actual,
        "parece_de_fabrica": parece_de_fabrica(datos) if magia_ok else None,
    }


def _selftest() -> int:
    ref = BACKUPS / "config_raw.bin"
    if not ref.is_file():
        print("SELFTEST: FAILED -- %s no existe" % ref, file=sys.stderr)
        return 1
    datos = ref.read_bytes()
    d = derivar(datos)
    print("derivar(%s):" % ref.name)
    for k, v in d.items():
        print("  %-24s %s" % (k, v))

    fallas = []
    if not d["magia_ok"]:
        fallas.append("magia_ok esperado True")
    if not d["ptyy_ok"]:
        fallas.append("ptyy_ok esperado True")
    if d["n_pantallas_actual"] != 156:
        fallas.append(
            "n_pantallas_actual esperado 156 (valor medido a mano en ESTADO.md "
            "para este blob), dio %r" % d["n_pantallas_actual"]
        )
    if d["n_dispositivos_actual"] != 3:
        fallas.append(
            "n_dispositivos_actual esperado 3 (TV/DVR/Home, ESTADO.md), "
            "dio %r" % d["n_dispositivos_actual"]
        )
    if d["parece_de_fabrica"] is not True:
        fallas.append(
            "parece_de_fabrica esperado True sobre el volcado virgen, dio %r"
            % d["parece_de_fabrica"]
        )

    # NEGATIVE: truncating the blob before its own declared close has to
    # break ptyy_ok, and corrupting the magic has to break magia_ok -- a
    # `derivar()` that reports "ok" on garbage would be worthless.
    trunco = derivar(datos[: d["close"] - 1])
    if trunco["ptyy_ok"]:
        fallas.append(
            "NEGATIVE FAILED: truncating 1 B before the close still gave ptyy_ok=True"
        )
    corrupto = derivar(b"XXXX" + datos[4:])
    if corrupto["magia_ok"]:
        fallas.append("NEGATIVO FALLIDO: magia corrupta igual dio magia_ok=True")

    # NEGATIVE that matters most: the config GRABBED on this remote today is
    # NOT a factory baseline, and must not be mistakable for one. It reports
    # 158 screens / 5 devices with a straight face; only `parece_de_fabrica`
    # tells them apart.
    grabado = ROOT / "output" / "config_empaquetada.bin"
    if grabado.is_file():
        g = derivar(grabado.read_bytes())
        print(
            "  control sobre lo GRABADO hoy: pantallas=%s dispositivos=%s "
            "parece_de_fabrica=%s"
            % (
                g["n_pantallas_actual"],
                g["n_dispositivos_actual"],
                g["parece_de_fabrica"],
            )
        )
        if g["parece_de_fabrica"] is not False:
            fallas.append(
                "NEGATIVE FAILED: the ALREADY EDITED blob (written today) gave "
                "parece_de_fabrica=%r, it had to give False" % g["parece_de_fabrica"]
            )

    print()
    if fallas:
        print("SELFTEST: FAILED")
        for f in fallas:
            print("  - %s" % f)
        return 1
    print("SELFTEST: PASSED (contra %s, offline, sin USB)" % ref.name)
    print(
        "  negatives OK: truncating before the close -> ptyy_ok=False; "
        "magia corrupta -> magia_ok=False"
    )
    print(
        "\nNOTE: this does NOT exercise read_flash_at() or get_identity() against "
        "real hardware -- there is no remote connected in this session. See "
        "the module's docstring."
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--salida", help="where to write the raw dump (LIVE)")
    ap.add_argument("--longitud", type=lambda x: int(x, 0), default=None)
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="corre derivar() contra backups/config_raw.bin, offline, sin USB",
    )
    ap.add_argument(
        "--identidad",
        action="store_true",
        help=(
            "SOLO identifica el mando (init_concord + get_identity) y sale. "
            "NO lee el flash: es la sonda barata de '¿sigue enchufado?' que "
            "usa la app al volver a la pantalla Control, para que el estado "
            "conectado sea MEDIDO cada vez y no una bandera que quedo "
            "prendida -- sin pagar la lectura entera de ~80 transacciones."
        ),
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help=(
            "besides the usual text, prints ONE JSON line (the last "
            "line of stdout) with the whole dict -- so that a subprocess "
            "caller (app/remote_status.py) no tenga que raspar '%%-24s %%s'"
        ),
    )
    a = ap.parse_args()

    if a.selftest:
        return _selftest()

    if a.identidad:
        # La sonda barata. Misma disciplina de salida que el camino largo:
        # una linea JSON al final, `encontrado` true/false, NUNCA un
        # traceback -- quien la llama la corre cada vez que se vuelve a la
        # pantalla Control y necesita una respuesta, no una excepcion.
        try:
            ident = read_live_identity()
        except RuntimeError as exc:
            print(
                "remote not found / could not be identified: %s" % exc,
                file=sys.stderr,
            )
            if a.json:
                print(
                    json.dumps(
                        {"encontrado": False, "reason": str(exc)}, ensure_ascii=False
                    ),
                    flush=True,
                )
            return 1
        for k, v in ident.items():
            print("%-24s %s" % (k, v))
        if a.json:
            print(
                json.dumps(
                    dict(ident, encontrado=True), ensure_ascii=False, default=str
                ),
                flush=True,
            )
        return 0

    if not a.salida:
        ap.error("--salida es obligatorio fuera de --selftest/--identidad")

    # `read_raw_baseline` NO LONGER raises for "the dump does not validate"
    # (see its docstring) -- only for not finding/identifying the remote at
    # all. That is the "disconnected" case, and before this change it came out
    # as a raw traceback on stderr with exit code 1 "by accident" (Python's
    # default when the exception is not caught), in practice indistinguishable
    # from any other crash. Now it is told apart EXPLICITLY.
    #
    # `flush=True` EN CADA LINEA DE AVANCE, y no es decorativo: cuando este
    # proceso corre con un pipe (que es como lo corre `app/remote_status.py`)
    # el stdout de Python queda en bloque de 8 KiB, y ~80 lineas de avance
    # no llenan un bloque -- llegarian TODAS JUNTAS AL FINAL, o sea la barra
    # se quedaria en 0% y saltaria a 100%. Exactamente la barra falsa que
    # esto viene a evitar.
    try:
        info = read_raw_baseline(
            pathlib.Path(a.salida),
            longitud=a.longitud,
            avisar=lambda texto: print(text, flush=True),
        )
    except RuntimeError as exc:
        print(
            "remote not found / could not be identified: %s" % exc,
            file=sys.stderr,
        )
        if a.json:
            print(
                json.dumps(
                    {"encontrado": False, "reason": str(exc)}, ensure_ascii=False
                )
            )
        return 1

    for k, v in info.items():
        print("%-24s %s" % (k, v))
    if a.json:
        # `default=str` covers whatever is not directly serializable (e.g.
        # chunks_fallidos's (offset, codigo) tuples are already lists/ints
        # via ctypes, but being robust against a future type costs nothing).
        print(json.dumps(dict(info, encontrado=True), ensure_ascii=False, default=str))

    hubo_problema = False
    if info["chunks_fallidos"]:
        hubo_problema = True
        print(
            "\nWARNING: %d chunk(s) failed during the read -- the "
            "dump has gaps" % len(info["chunks_fallidos"]),
            file=sys.stderr,
        )
    if not info["magia_ok"] or not info["ptyy_ok"]:
        hubo_problema = True
        print(
            "\nWARNING: the dump does not close as a valid GSPM "
            "(magia_ok=%s, ptyy_ok=%s)" % (info["magia_ok"], info["ptyy_ok"]),
            file=sys.stderr,
        )
    return 1 if hubo_problema else 0


if __name__ == "__main__":
    raise SystemExit(main())
