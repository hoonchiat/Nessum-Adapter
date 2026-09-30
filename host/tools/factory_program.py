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


def open_run(log, rows, run, first, last, quantity, key_fp, new_key=False):
    """Validate this run against the log, recording it on first use.

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
    else:
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


def program_one(con, log, run, first, last, quantity, key, lock=True, force=False, new_key=False):
    """Program one adapter's MAC and network key. Returns (exit_code, message)."""
    key_fp = nessumctl.key_fingerprint(key)
    try:
        run_rows = open_run(log, read_log(log), run, first, last, quantity, key_fp, new_key)
    except (RunError, ValueError, nessumctl.MacError) as e:
        return 2, f"run {run}: {e}"
    done_count = sum(1 for r in run_rows if r["status"] in DONE)
    if done_count >= quantity:
        return 1, f"run {run} is complete ({done_count}/{quantity} units) - nothing done"

    ver = nessumctl.merged(con.command("VERSION"))
    serial = ver.get("serial", "?")
    info = nessumctl.merged(con.command("MAC GET"))

    if info.get("locked") == "yes":
        return 1, f"{serial}: already locked with {info.get('programmed')} - nothing done"
    if info.get("programmed", "none") != "none" and not force:
        return 1, f"{serial}: already programmed with {info['programmed']} (use --force to replace)"

    mac = next_address(read_log(log), first, last)
    if mac is None:
        return 1, f"run {run}: address block exhausted after {done_count}/{quantity} units (too many failures?)"

    append_log(log, run=run, status="reserved", mac=mac, serial=serial, key_fp=key_fp)
    try:
        con.command(f"MAC SET {mac}")
        check = nessumctl.merged(con.command("MAC GET"))
        if check.get("programmed") != mac:
            raise RuntimeError(f"read-back mismatch: {check.get('programmed')}")
        con.command(f"NKEY SET {key.hex()}")
        if nessumctl.merged(con.command("NKEY GET")).get("fp") != key_fp:
            raise RuntimeError("network key fingerprint mismatch after write")
        if lock:
            con.command("LOCK")
            if nessumctl.merged(con.command("MAC GET")).get("locked") != "yes":
                raise RuntimeError("lock did not take effect")
        con.command("REBOOT")
    except (nessumctl.ProtocolError, RuntimeError, TimeoutError, ConnectionError) as e:
        append_log(log, run=run, status="failed", mac=mac, serial=serial, key_fp=key_fp, detail=str(e))
        return 1, f"{serial}: FAILED programming {mac}: {e} (address retired, not reused)"

    append_log(log, run=run, status="locked" if lock else "programmed", mac=mac, serial=serial, key_fp=key_fp)
    done = "programmed + locked" if lock else "programmed"
    return 0, f"{serial}: {mac}, key {key_fp}, {done} - run {run} unit {done_count + 1}/{quantity}"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("-d", "--device", default=os.environ.get("NESSUM_DEVICE", nessumctl.DEFAULT_DEVICE))
    p.add_argument("--run", required=True, help="production run ID, e.g. R2026-10")
    p.add_argument("--block", required=True,
                   help="this run's MAC block: 'first-last' or 'base/prefixlen' (e.g. 00:50:c2:aa:00:00/39)")
    p.add_argument("--quantity", type=int, required=True, help="number of units in this run")
    p.add_argument("--key-file", required=True, help="the common Nessum network key as hex (mode 600)")
    p.add_argument("--log", required=True, help="append-only CSV log shared by all runs")
    p.add_argument("--new-key", action="store_true",
                   help="allow a key different from earlier units (deliberate common-key change only)")
    p.add_argument("--no-lock", action="store_true", help="program without locking (e.g. engineering units)")
    p.add_argument("--force", action="store_true", help="replace an existing unlocked programmed address")
    args = p.parse_args(argv)

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

    try:
        con = nessumctl.Console(args.device)
    except OSError as e:
        print(f"factory_program: cannot open {args.device}: {e.strerror}", file=sys.stderr)
        return 2

    with con:
        try:
            rc, msg = program_one(con, args.log, args.run, first, last, args.quantity, key,
                                  lock=not args.no_lock, force=args.force, new_key=args.new_key)
        except (nessumctl.ProtocolError, TimeoutError, ConnectionError) as e:
            rc, msg = 2, f"adapter not responding correctly: {e}"
    print(msg, file=sys.stdout if rc == 0 else sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
