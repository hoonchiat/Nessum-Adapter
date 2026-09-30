#!/usr/bin/env python3
"""Factory programming: MAC from the run's block + common Nessum key, verify, lock.

Each production run has an ID, its own MAC address block and a unit quantity:

    factory_program.py --run R2026-10 --block 00:50:c2:aa:00:00-00:50:c2:aa:01:f3 \
        --quantity 500 --key-file nessum-common.key --log production-log.csv

Run it once per adapter. It programs the next free address in the block, the
common network key, verifies both, locks the unit, and stops once the run's
quantity is reached.

Safety rules, all enforced from the append-only CSV log:
  * An address is logged as 'reserved' *before* it is written, so a crash or
    unplug can never hand the same address out twice. Failed addresses are
    retired, never reused.
  * A run's block and quantity are fixed by its first unit. Later invocations with
    different values are refused, which catches typos mid-run.
  * A run's block may not overlap any other run's block in the log.
  * Every unit must get the same key (one common key). A key file whose fingerprint
    differs from earlier units is refused unless --new-key is given.

Backends: ``--backend c`` (selected design: hub + AX88772C + CP2102N, see
optionc.py) or ``--backend a`` (RT1062 MCU console protocol, see MANAGEMENT.md).

Keep ONE log for all runs (back it up; it is the only record of used addresses).
The key file holds the key as hex and must be mode 600. The log records only the
key's fingerprint, never the key.
"""

import argparse
import csv
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nessumctl  # noqa: E402
import optionc  # noqa: E402

LOG_FIELDS = ["timestamp", "run", "status", "mac", "serial", "key_fp", "detail"]
DONE = ("locked", "programmed")


class RunError(RuntimeError):
    pass


def mac_to_int(mac):
    return int(nessumctl.parse_mac(mac).replace(":", ""), 16)


def int_to_mac(value):
    h = f"{value:012x}"
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def parse_block(text):
    """'first-last' or 'base/prefixlen' -> (first_int, last_int)."""
    if "/" in text:
        base, plen = text.split("/", 1)
        plen = int(plen)
        if not 24 <= plen <= 47:
            raise ValueError("prefix length must be 24..47")
        size = 1 << (48 - plen)
        first = mac_to_int(base) & ~(size - 1)
        last = first + size - 1
    elif len(text) == 35 and text[17] == "-":  # xx:xx:xx:xx:xx:xx-xx:xx:xx:xx:xx:xx
        first, last = mac_to_int(text[:17]), mac_to_int(text[18:])
    else:
        raise ValueError("block must be 'first-last' (':' separators) or 'base/prefixlen'")
    if last < first:
        raise ValueError("block end is before block start")
    if (first >> 40) & 0x01:
        raise ValueError("block is in multicast address space")
    return first, last


def read_log(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def append_log(path, **row):
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    row.setdefault("timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in LOG_FIELDS})
        f.flush()
        os.fsync(f.fileno())


def fmt_block(first, last):
    return f"{int_to_mac(first)}-{int_to_mac(last)}"


def open_run(log, rows, run, first, last, quantity, key_fp, new_key=False, record=True):
    """Validate this run against the log, recording it on first use (if record).

    Returns the rows belonging to this run.
    """
    if quantity < 1:
        raise RunError("--quantity must be at least 1")
    if quantity > last - first + 1:
        raise RunError(f"block {fmt_block(first, last)} holds {last - first + 1} addresses, "
                       f"fewer than --quantity {quantity}")

    old_fps = {r["key_fp"] for r in rows if r.get("key_fp") and r["status"] in DONE}
    if old_fps and key_fp not in old_fps and not new_key:
        raise RunError(f"key fingerprint {key_fp} differs from earlier units ({', '.join(sorted(old_fps))}); "
                       "wrong key file? Units with a different key cannot talk to existing ones. "
                       "Use --new-key only if you are deliberately changing the common key.")

    declared = {}
    for row in rows:
        if row["status"] == "run-open":
            f, l = parse_block(row["detail"].split(";")[0].split("=", 1)[1])
            q = int(row["detail"].split(";")[1].split("=", 1)[1])
            declared[row["run"]] = (f, l, q)

    for other, (f, l, _) in declared.items():
        if other != run and f <= last and first <= l:
            raise RunError(f"block {fmt_block(first, last)} overlaps run {other} ({fmt_block(f, l)})")
    for row in rows:
        if row.get("mac") and row["run"] != run and first <= mac_to_int(row["mac"]) <= last:
            raise RunError(f"address {row['mac']} in this block was already used by run {row['run']}")

    if run in declared:
        if declared[run] != (first, last, quantity):
            f, l, q = declared[run]
            raise RunError(f"run {run} was opened with block {fmt_block(f, l)} and quantity {q}; "
                           "refusing different values mid-run")
    elif record:
        append_log(log, run=run, status="run-open", key_fp=key_fp,
                   detail=f"block={fmt_block(first, last)};quantity={quantity}")
    return [r for r in rows if r["run"] == run]


def next_address(rows, first, last):
    """Lowest address in the block above every address ever reserved in the log."""
    used = [mac_to_int(r["mac"]) for r in rows if r.get("mac")]
    in_block = [u for u in used if first <= u <= last]
    candidate = max(in_block) + 1 if in_block else first
    # Never hand out the all-ones host part of a block edge case like ff:ff:ff:ff:ff:ff.
    while candidate <= last:
        mac = int_to_mac(candidate)
        try:
            nessumctl.parse_mac(mac)
            return mac
        except nessumctl.MacError:
            candidate += 1
    return None


# Errors that stop a unit before any address is reserved (nothing logged).
PREFLIGHT_ERRORS = (nessumctl.ProtocolError, TimeoutError, ConnectionError, OSError,
                    optionc.NotReady, NotImplementedError)
# Errors during programming: the reserved address is retired.
STEP_ERRORS = (nessumctl.ProtocolError, RuntimeError, TimeoutError, ConnectionError, OSError,
               NotImplementedError)


class McuUnit:
    """Option A: one adapter reached through the RT1062 console (docs/MANAGEMENT.md)."""

    def __init__(self, con):
        self.con = con

    def identify(self):
        serial = nessumctl.merged(self.con.command("VERSION")).get("serial", "?")
        info = nessumctl.merged(self.con.command("MAC GET"))
        programmed = info.get("programmed", "none")
        return {"serial": serial, "programmed": None if programmed == "none" else programmed,
                "locked": info.get("locked") == "yes"}

    def write_mac(self, mac):
        self.con.command(f"MAC SET {mac}")
        got = nessumctl.merged(self.con.command("MAC GET")).get("programmed")
        if got != mac:
            raise RuntimeError(f"read-back mismatch: {got}")

    def write_key(self, key):
        self.con.command(f"NKEY SET {key.hex()}")
        if nessumctl.merged(self.con.command("NKEY GET")).get("fp") != nessumctl.key_fingerprint(key):
            raise RuntimeError("network key fingerprint mismatch after write")

    def lock(self):
        self.con.command("LOCK")
        if nessumctl.merged(self.con.command("MAC GET")).get("locked") != "yes":
            raise RuntimeError("lock did not take effect")

    def finish(self):
        self.con.command("REBOOT")
        return None

    def close(self):
        self.con.close()


class BackendA:
    """Station backend for option A: the MCU console at `device`."""

    name = "a"

    def __init__(self, device):
        self.device = device

    def warnings(self):
        return []

    def describe(self):
        return self.device

    def present(self):
        return os.path.exists(self.device)

    def peek_serial(self):
        try:
            with nessumctl.Console(self.device, timeout=2) as con:
                return nessumctl.merged(con.command("VERSION")).get("serial")
        except PREFLIGHT_ERRORS:
            return None

    def open_unit(self):
        return McuUnit(nessumctl.Console(self.device))

    def applied_mac(self, serial):
        return None  # the MCU applies the MAC itself on REBOOT; nothing to re-check


def program_unit(unit, log, run, first, last, quantity, key, lock=True, force=False, new_key=False,
                 result=None):
    """Program one adapter's MAC and network key. Returns (exit_code, message).

    `unit` provides identify / write_mac / write_key / lock / finish (McuUnit for
    option A, optionc.OptionCUnit for option C). If `result` is a dict it is filled
    with: outcome ('ok', 'already', 'complete', 'failed', 'error'), serial and mac.
    """
    res = result if result is not None else {}
    res.update(outcome="error", serial=None, mac=None)
    key_fp = nessumctl.key_fingerprint(key)
    try:
        run_rows = open_run(log, read_log(log), run, first, last, quantity, key_fp, new_key)
    except (RunError, ValueError, nessumctl.MacError) as e:
        return 2, f"run {run}: {e}"
    done_count = sum(1 for r in run_rows if r["status"] in DONE)
    if done_count >= quantity:
        res["outcome"] = "complete"
        return 1, f"run {run} is complete ({done_count}/{quantity} units) - nothing done"

    try:
        info = unit.identify()
    except PREFLIGHT_ERRORS as e:
        return 2, f"adapter not ready: {e}"
    serial = res["serial"] = info["serial"]

    if info["locked"]:
        res.update(outcome="already", mac=info["programmed"])
        return 1, f"{serial}: already locked with {info['programmed']} - nothing done"
    if info["programmed"] and not force:
        res.update(outcome="already", mac=info["programmed"])
        return 1, f"{serial}: already programmed with {info['programmed']} (use --force to replace)"

    mac = next_address(read_log(log), first, last)
    if mac is None:
        return 1, f"run {run}: address block exhausted after {done_count}/{quantity} units (too many failures?)"
    res["mac"] = mac

    append_log(log, run=run, status="reserved", mac=mac, serial=serial, key_fp=key_fp)
    try:
        unit.write_mac(mac)
        unit.write_key(key)
        if lock:
            unit.lock()
        note = unit.finish()
    except STEP_ERRORS as e:
        append_log(log, run=run, status="failed", mac=mac, serial=serial, key_fp=key_fp, detail=str(e))
        res["outcome"] = "failed"
        return 1, f"{serial}: FAILED programming {mac}: {e} (address retired, not reused)"

    append_log(log, run=run, status="locked" if lock else "programmed", mac=mac, serial=serial, key_fp=key_fp)
    res["outcome"] = "ok"
    done = "programmed + locked" if lock else "programmed"
    shown = "MAC hidden until verified" if getattr(unit, "verify_after_replug", False) else mac
    msg = f"{serial}: {shown}, key {key_fp}, {done} - run {run} unit {done_count + 1}/{quantity}"
    if note:
        res["note"] = note
        msg += f" - {note}"
    return 0, msg


def program_one(con, log, run, first, last, quantity, key, **kw):
    """Option A convenience wrapper: program the unit behind an open MCU console."""
    return program_unit(McuUnit(con), log, run, first, last, quantity, key, **kw)


def log_verification(log, run, serial, mac, key_fp, applied):
    """Record the post-replug check of the MAC the hardware actually reports."""
    ok = applied == mac
    append_log(log, run=run, status="verified" if ok else "verify-failed", mac=mac, serial=serial,
               key_fp=key_fp, detail="" if ok else f"hardware reports {applied}")
    return ok


def verify_unit(backend, log):
    """Option C, after the re-plug: check the MAC the hardware reports for the unit
    that is plugged in against its latest programmed record. Returns (rc, message);
    the MAC is only printed once it is verified (it goes on the label)."""
    serial = backend.peek_serial()
    if not serial:
        return 2, "plug in exactly one adapter to verify"
    rows = read_log(log)
    done = [(i, r) for i, r in enumerate(rows) if r["serial"] == serial and r["status"] in DONE]
    if not done:
        return 2, f"{serial}: no programmed record in {log}"
    i, rec = done[-1]
    later = [r for r in rows[i + 1:] if r["serial"] == serial and r["mac"] == rec["mac"]
             and r["status"] in ("verified", "verify-failed")]
    if later and later[-1]["status"] == "verify-failed":
        return 1, f"{serial}: verification already FAILED ({later[-1]['detail']}) - set aside, do not label"
    applied = backend.applied_mac(serial)
    if later:
        ok = applied == rec["mac"]  # re-check only; already logged as verified
    else:
        ok = log_verification(log, rec["run"], serial, rec["mac"], rec["key_fp"], applied)
    if ok:
        return 0, f"{serial}: VERIFIED {rec['mac']} - write the label"
    return 1, f"{serial}: VERIFY FAILED - hardware reports {applied} - set aside, do not label"


def make_backend(args):
    if args.backend == "a":
        return BackendA(args.device)
    return optionc.BackendC(sysfs=args.sysfs, engineering=args.engineering)


def add_backend_args(p):
    p.add_argument("--backend", choices=("c", "a"), default="c",
                   help="c = hub + AX88772C + CP2102N (selected design, default); a = RT1062 MCU")
    p.add_argument("-d", "--device", default=os.environ.get("NESSUM_DEVICE", nessumctl.DEFAULT_DEVICE),
                   help="option A: the MCU console tty")
    p.add_argument("--engineering", action="store_true",
                   help="option C: allow the not-yet-verified AX88772C EEPROM layout (engineering units)")
    p.add_argument("--sysfs", default="/sys", help=argparse.SUPPRESS)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_backend_args(p)
    p.add_argument("--verify", action="store_true",
                   help="option C: after the re-plug, verify the unit's MAC in hardware "
                        "(only needs --log); the MAC is printed only once verified")
    p.add_argument("--run", help="production run ID, e.g. R2026-10")
    p.add_argument("--block",
                   help="this run's MAC block: 'first-last' or 'base/prefixlen' (e.g. 00:50:c2:aa:00:00/39)")
    p.add_argument("--quantity", type=int, help="number of units in this run")
    p.add_argument("--key-file", help="the common Nessum network key as hex (mode 600)")
    p.add_argument("--log", required=True, help="append-only CSV log shared by all runs")
    p.add_argument("--new-key", action="store_true",
                   help="allow a key different from earlier units (deliberate common-key change only)")
    p.add_argument("--no-lock", action="store_true", help="program without locking (e.g. engineering units)")
    p.add_argument("--force", action="store_true", help="replace an existing unlocked programmed address")
    args = p.parse_args(argv)

    if args.verify:
        if args.backend != "c":
            p.error("--verify is for option C (option A verifies on the MCU)")
        rc, msg = verify_unit(make_backend(args), args.log)
        print(msg, file=sys.stdout if rc == 0 else sys.stderr)
        return rc
    missing = [n for n in ("run", "block", "quantity", "key_file") if getattr(args, n) is None]
    if missing:
        p.error("the following arguments are required: " + ", ".join("--" + m.replace("_", "-") for m in missing))

    try:
        first, last = parse_block(args.block)
    except (ValueError, nessumctl.MacError) as e:
        print(f"factory_program: bad --block: {e}", file=sys.stderr)
        return 2

    try:
        key = nessumctl.read_key_file(args.key_file)
    except (nessumctl.KeyError_, OSError) as e:
        print(f"factory_program: bad --key-file: {e}", file=sys.stderr)
        return 2

    backend = make_backend(args)
    for w in backend.warnings():
        print(f"factory_program: warning: {w}", file=sys.stderr)
    try:
        unit = backend.open_unit()
    except PREFLIGHT_ERRORS as e:
        print(f"factory_program: {e}", file=sys.stderr)
        return 2
    try:
        rc, msg = program_unit(unit, args.log, args.run, first, last, args.quantity, key,
                               lock=not args.no_lock, force=args.force, new_key=args.new_key)
    finally:
        unit.close()
    print(msg, file=sys.stdout if rc == 0 else sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
