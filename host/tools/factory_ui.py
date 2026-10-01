#!/usr/bin/env python3
"""Browser UI for factory programming of the USB <-> Nessum adapter.

A small local web server that wraps factory_program.py, so the operator gets a
big PASS/FAIL screen, run progress and a live unit list, while MAC allocation, key
checks and the append-only log stay exactly as tested in factory_program.py.

    factory_ui.py --key-file nessum-common.key --log production-log.csv
    # then open http://127.0.0.1:8080 in a browser on the same PC

Backends as in factory_program.py: --backend c (default, hub + AX88772C + CP2102N)
or --backend a (RT1062 console). With option C the station also checks, after the
operator re-plugs a freshly programmed unit, that the MAC the kernel reports
matches the programmed one (logged as 'verified' / 'verify-failed').

Labelling rule (option C), enforced here and not just in the page: the MAC of a
unit is never sent to the browser before it is VERIFIED, and no other unit can
be programmed while one is waiting for its re-plug. "Mark for rework" logs the
waiting unit as verify-failed (MAC never shown) and frees the station.

Labels: printed only for a unit whose MAC may be shown (VERIFIED for option C,
programmed for option A); every print and reprint is logged ('label-printed').
--printer browser (default) prints from the page; zpl:tcp://HOST[:PORT],
zpl:/dev/usb/lp0 or file:DIR print from the server (see labels.py).

The key file is read by this server and never sent to the browser. The page only
sees the key fingerprint. The server listens on 127.0.0.1 only. Standard library
only, like the other tools.
"""

import argparse
import datetime
import http.server
import json
import os
import sys
import threading
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import factory_program as fp  # noqa: E402
import labels  # noqa: E402
import nessumctl  # noqa: E402

UI_FILE = os.path.join(HERE, "factory_ui.html")
RECENT = 20


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Station:
    """Programming-station state shared by the HTTP handler and the auto loop."""

    def __init__(self, backend, key, log, lock_units=True, printer=None, label_size=(50, 25),
                 label_title="Nessum Adapter", qr_template=labels.DEFAULT_QR):
        # A plain path means an option A console (kept for existing callers/tests).
        self.backend = fp.BackendA(backend) if isinstance(backend, str) else backend
        self.key = key
        self.key_fp = nessumctl.key_fingerprint(key)
        self.log = log
        self.lock_units = lock_units
        self.run = None          # {"id", "block", "first", "last", "quantity"}
        self.auto = False
        self.busy = False
        self.last = None         # last programming attempt, for the big result tile
        self.handled_serial = None
        self._pending_verify = None  # option C: {serial, mac, run} until the unit is re-plugged
        self.printer = printer or labels.BrowserPrinter()
        self.label_size = label_size
        self.label_title = label_title
        self.qr_template = qr_template
        self.auto_print = False
        self._need_check = True
        self._mutex = threading.Lock()
        self._log_cache = (None, [])

    # --- log ---------------------------------------------------------------
    def rows(self):
        try:
            st = os.stat(self.log)
            sig = (st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            return []
        if self._log_cache[0] != sig:
            self._log_cache = (sig, fp.read_log(self.log))
        return self._log_cache[1]

    def known_runs(self, rows):
        runs = {}
        for r in rows:
            if r["status"] == "run-open":
                block = r["detail"].split(";")[0].split("=", 1)[1]
                qty = int(r["detail"].split(";")[1].split("=", 1)[1])
                runs[r["run"]] = {"id": r["run"], "block": block, "quantity": qty, "done": 0, "failed": 0,
                                  "verify_failed": 0}
        for r in rows:
            if r["run"] in runs:
                if r["status"] in fp.DONE:
                    runs[r["run"]]["done"] += 1
                elif r["status"] == "failed":
                    runs[r["run"]]["failed"] += 1
                elif r["status"] == "verify-failed":
                    runs[r["run"]]["verify_failed"] += 1
        return list(runs.values())

    # --- run selection -------------------------------------------------------
    def set_run(self, run_id, block, quantity):
        run_id = (run_id or "").strip()
        if not run_id or any(c in run_id for c in ",;\n\r\"") or len(run_id) > 40:
            raise ValueError("run ID must be 1-40 characters without , ; or quotes")
        first, last = fp.parse_block((block or "").strip())
        quantity = int(quantity)
        # Validate against the log now (overlap, key, size, mid-run changes) without
        # recording; the run-open row is written with the first programmed unit.
        fp.open_run(self.log, fp.read_log(self.log), run_id, first, last, quantity,
                    self.key_fp, record=False)
        with self._mutex:
            self.run = {"id": run_id, "block": fp.fmt_block(first, last),
                        "first": first, "last": last, "quantity": quantity}
            self.last = None

    def clear_run(self):
        with self._mutex:
            if self._pending_verify:
                raise ValueError(self._replug_first_msg())
            self.run = None
            self.auto = False
            self.last = None

    # --- programming ---------------------------------------------------------
    def present(self):
        return self.backend.present()

    def program(self):
        """Program the unit that is plugged in now. Returns the result dict."""
        with self._mutex:
            if self.busy:
                return {"outcome": "error", "message": "already programming"}
            if not self.run:
                return {"outcome": "error", "message": "select a production run first"}
            if self._pending_verify:
                return {"outcome": "blocked", "message": self._replug_first_msg()}
            self.busy = True
            run = dict(self.run)
        res = {}
        try:
            unit = self.backend.open_unit()
        except fp.PREFLIGHT_ERRORS as e:
            res, msg = {"outcome": "error", "serial": None, "mac": None}, f"adapter not ready: {e}"
        else:
            try:
                rc, msg = fp.program_unit(unit, self.log, run["id"], run["first"], run["last"],
                                          run["quantity"], self.key, lock=self.lock_units, result=res)
            finally:
                unit.close()
        res.update(message=msg, time=now())
        with self._mutex:
            self.busy = False
            self.last = res
            self.handled_serial = res.get("serial") or self.handled_serial
            # The unit must disappear (unplug, or its REBOOT) before it is looked at again.
            self._need_check = False
            if res["outcome"] == "ok" and self.backend.name == "c":
                self._pending_verify = {"serial": res["serial"], "mac": res["mac"], "run": run["id"]}
        return res

    # --- labels ------------------------------------------------------------------
    def print_label(self, serial):
        """Print the label of a unit whose MAC may be shown; logged. Raises ValueError /
        labels.PrintError when not allowed or the printer fails."""
        return fp.print_label(self.log, self.printer, serial, self._needs_verify(),
                              self.label_size, self.label_title, self.qr_template)

    def label_svg(self, serial):
        rec = fp.labelable_record(fp.read_log(self.log), serial, self._needs_verify())
        return labels.svg_label(labels.label_fields(rec, self.label_title, qr_template=self.qr_template),
                                self.label_size)

    def _replug_first_msg(self):
        return (f"unit {self._pending_verify['serial']} is waiting for verification: re-plug it "
                "first, or mark it for rework")

    def abandon_verification(self):
        """Operator gives up on the waiting unit: log verify-failed; its MAC is never shown."""
        with self._mutex:
            pv = self._pending_verify
            if not pv:
                raise ValueError("no unit is waiting for verification")
            fp.append_log(self.log, run=pv["run"], status="verify-failed", mac=pv["mac"],
                          serial=pv["serial"], key_fp=self.key_fp,
                          detail="not re-plugged - marked for rework by the operator")
            self._pending_verify = None
            self.last = {"outcome": "failed", "serial": pv["serial"], "mac": None, "verified": False,
                         "time": now(), "message": "Marked for rework: MAC not verified - do not label, set aside"}

    def verify_applied(self, serial):
        """Option C: after a re-plug, compare the MAC the kernel reports with the programmed one."""
        pv = self._pending_verify
        applied = self.backend.applied_mac(serial)
        ok = fp.log_verification(self.log, pv["run"], serial, pv["mac"], self.key_fp, applied)
        res = {"outcome": "ok" if ok else "failed", "serial": serial, "mac": pv["mac"] if ok else None,
               "verified": ok, "time": now(),
               "message": "MAC verified in hardware" if ok else
               f"MAC mismatch after re-plug: hardware reports {applied}, which differs from the "
               "programmed MAC (check the EEPROM layout) - do not label, set this unit aside"}
        with self._mutex:
            self._pending_verify = None
            self.last = res
        return res

    def auto_step(self):
        """One loop tick: verify a re-plugged unit (option C), and in auto mode program a
        newly inserted, not-yet-handled unit."""
        if not self.present():
            self._need_check = True     # unplugged (or re-enumerating after REBOOT)
            return None
        if self.busy or not self._need_check:
            return None
        if not (self._pending_verify or (self.auto and self.run)):
            return None
        serial = self.backend.peek_serial()
        if serial is None:
            return None                 # not ready yet; try again next tick
        self._need_check = False
        if self._pending_verify:
            if serial == self._pending_verify["serial"]:
                return self.verify_applied(serial)
            with self._mutex:   # a different unit: refuse until the waiting one is verified
                self.last = {"outcome": "blocked", "serial": serial, "mac": None, "time": now(),
                             "message": self._replug_first_msg()}
            return self.last
        if not (self.auto and self.run):
            return None
        if serial == self.handled_serial:
            return None                 # same unit coming back after its REBOOT
        return self.program()

    def auto_loop(self, stop, interval=0.5):
        while not stop.is_set():
            try:
                self.auto_step()
            except Exception as e:  # keep the station alive; show the error
                with self._mutex:
                    self.busy = False
                    self.last = {"outcome": "error", "message": f"internal error: {e}", "time": now()}
            stop.wait(interval)

    # --- state for the page -------------------------------------------------------
    def state(self):
        rows = self.rows()
        with self._mutex:
            run = dict(self.run) if self.run else None
            st = {
                "station": {"device": self.backend.describe(), "backend": self.backend.name,
                            "present": self.present(), "key_fp": self.key_fp,
                            "log": os.path.abspath(self.log), "auto": self.auto, "busy": self.busy,
                            "lock_units": self.lock_units, "warnings": self.backend.warnings(),
                            "awaiting_replug": bool(self._pending_verify),
                            "printer": self.printer.describe(), "printer_mode": self.printer.mode,
                            "auto_print": self.auto_print,
                            "label_size": list(self.label_size)},
                "last": self._public_last(rows),
            }
        runs = self.known_runs(rows)
        st["runs"] = runs
        if run:
            info = next((r for r in runs if r["id"] == run["id"]), {"done": 0, "failed": 0, "verify_failed": 0})
            used = [fp.mac_to_int(r["mac"]) for r in rows if r.get("mac") and r["run"] == run["id"]]
            next_int = (max(used) + 1) if used else run["first"]
            labelled = {(r["serial"], r["mac"]) for r in rows
                        if r["run"] == run["id"] and r["status"] == "label-printed"}
            run.update(done=info["done"], failed=info["failed"], verify_failed=info["verify_failed"],
                       labelled=len(labelled),
                       spare_addresses=max(0, run["last"] - next_int + 1),
                       complete=info["done"] >= run["quantity"])
            del run["first"], run["last"]
            st["recent"] = self._public_rows([
                {k: r.get(k, "") for k in ("timestamp", "status", "mac", "serial", "detail")}
                for r in rows if r["run"] == run["id"]
                and r["status"] in fp.DONE + ("failed", "verified", "verify-failed")
            ], rows)[-RECENT:][::-1]
        else:
            st["recent"] = []
        st["run"] = run
        return st


    # --- labelling rule: no MAC leaves the server before it is verified (option C) ----
    def _needs_verify(self):
        return self.backend.name == "c"

    def _public_last(self, rows=()):
        last = self.last
        if last and self._needs_verify() and last.get("outcome") in ("ok", "already") \
                and not last.get("verified"):
            last = dict(last, mac=None)
        if last and last.get("outcome") == "ok" and last.get("mac"):
            # MAC is shown, so the unit may be labelled: say how often it has been.
            last = dict(last, printable=True, labels=fp.labels_printed(rows, last["serial"], last["mac"]))
        return last

    def _public_rows(self, recent, rows):
        if not self._needs_verify():
            return [dict(r, printable=r["status"] in fp.DONE,
                         labels=fp.labels_printed(rows, r["serial"], r["mac"]) if r["status"] in fp.DONE else 0)
                    for r in recent]
        verified = {(r["serial"], r["mac"]) for r in rows if r["status"] == "verified"}
        pv = self._pending_verify
        out = []
        for r in recent:
            r = dict(r, awaiting=False)
            if r["status"] in fp.DONE and (r["serial"], r["mac"]) not in verified:
                waiting = bool(pv) and (pv["serial"], pv["mac"]) == (r["serial"], r["mac"])
                r.update(mac="", awaiting=waiting,
                         detail="awaiting re-plug verification" if waiting else "not verified")
            elif r["status"] == "verify-failed":
                r["mac"] = ""
            r["printable"] = r["status"] == "verified"
            r["labels"] = fp.labels_printed(rows, r["serial"], r["mac"]) if r["printable"] else 0
            out.append(r)
        return out


class Handler(http.server.BaseHTTPRequestHandler):
    station = None  # set by make_server

    def log_message(self, fmt, *args):
        pass  # keep the console quiet; the CSV log is the record

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        # Reject DNS-rebinding requests: only answer to a local Host header.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost", "[::1]")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        if self.path in ("/", "/index.html"):
            with open(UI_FILE, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._send(200, self.station.state())
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/label.svg":
            serial = dict(urllib.parse.parse_qsl(url.query)).get("serial", "")
            try:
                return self._send(200, self.station.label_svg(serial).encode(), "image/svg+xml")
            except ValueError as e:
                return self._send(403, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        # JSON body + custom header forces a CORS preflight, which this server never
        # grants, so other web pages cannot drive the station (CSRF).
        if not self._host_ok() or self.headers.get("X-Station") != "1" \
                or not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._send(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad JSON"})
        st = self.station
        try:
            if self.path == "/api/run":
                st.set_run(body.get("id"), body.get("block"), body.get("quantity"))
            elif self.path == "/api/run/close":
                st.clear_run()
            elif self.path == "/api/program":
                if not st.run:
                    raise ValueError("select a production run first")
                if st._pending_verify:
                    raise ValueError(st._replug_first_msg())
                st.program()
            elif self.path == "/api/verify/abandon":
                st.abandon_verification()
            elif self.path == "/api/label/print":
                out = st.print_label(str(body.get("serial", "")))
                return self._send(200, {"print": out, "state": st.state()})
            elif self.path == "/api/label/auto":
                st.auto_print = bool(body.get("on"))
            elif self.path == "/api/auto":
                if body.get("on") and not st.run:
                    raise ValueError("select a production run first")
                st.auto = bool(body.get("on"))
                st.handled_serial = None if st.auto else st.handled_serial
            else:
                return self._send(404, {"error": "not found"})
        except (ValueError, fp.RunError, nessumctl.MacError, labels.PrintError) as e:
            return self._send(400, {"error": str(e)})
        return self._send(200, st.state())


def make_server(station, port=8080):
    handler = type("BoundHandler", (Handler,), {"station": station})
    return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    fp.add_backend_args(p)
    p.add_argument("--key-file", required=True, help="the common Nessum network key as hex (mode 600)")
    p.add_argument("--log", required=True, help="append-only CSV log shared by all runs")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--no-lock", action="store_true", help="engineering units only: do not lock")
    p.add_argument("--printer", default="browser",
                   help="browser (default) | zpl:tcp://HOST[:PORT] | zpl:/dev/usb/lp0 | file:DIR")
    p.add_argument("--label-size", default="50x25", help="label size in mm (default 50x25)")
    p.add_argument("--label-title", default="Nessum Adapter", help="first line of the label")
    p.add_argument("--qr-content", default=labels.DEFAULT_QR,
                   help="QR code template: {barcode} {mac} {serial} {run} {date} {title} "
                        f"(default {labels.DEFAULT_QR!r})")
    p.add_argument("--dpmm", type=int, default=8, choices=(6, 8, 12, 24),
                   help="ZPL printer resolution in dots/mm (8 = 203 dpi, 12 = 300 dpi)")
    args = p.parse_args(argv)

    try:
        key = nessumctl.read_key_file(args.key_file)
    except (nessumctl.KeyError_, OSError) as e:
        print(f"factory_ui: bad --key-file: {e}", file=sys.stderr)
        return 2

    try:
        printer = labels.make_printer(args.printer, args.dpmm)
        size = labels.parse_size(args.label_size)
        # fail at start-up, not at the first label, on a bad template
        labels.label_fields({"mac": "00:00:00:00:00:01", "serial": "X" * 32, "run": "R" * 24},
                            args.label_title, qr_template=args.qr_content)
    except ValueError as e:
        print(f"factory_ui: {e}", file=sys.stderr)
        return 2
    station = Station(fp.make_backend(args), key, args.log, lock_units=not args.no_lock,
                      printer=printer, label_size=size, label_title=args.label_title,
                      qr_template=args.qr_content)
    stop = threading.Event()
    threading.Thread(target=station.auto_loop, args=(stop,), daemon=True).start()
    server = make_server(station, args.port)
    print(f"Nessum production station (option {station.backend.name.upper()}): "
          f"http://127.0.0.1:{args.port}  (key {station.key_fp}, Ctrl-C to stop)")
    for w in station.backend.warnings():
        print(f"warning: {w}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
