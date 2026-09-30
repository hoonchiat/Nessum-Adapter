#!/usr/bin/env python3
"""Browser UI for factory programming of the USB <-> Nessum adapter.

A small local web server that wraps factory_program.py, so the operator gets a
big PASS/FAIL screen, run progress and a live unit list, while MAC allocation, key
checks and the append-only log stay exactly as tested in factory_program.py.

    factory_ui.py --key-file nessum-common.key --log production-log.csv
    # then open http://127.0.0.1:8080 in a browser on the same PC

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
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import factory_program as fp  # noqa: E402
import nessumctl  # noqa: E402

UI_FILE = os.path.join(HERE, "factory_ui.html")
RECENT = 20


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Station:
    """Programming-station state shared by the HTTP handler and the auto loop."""

    def __init__(self, device, key, log, lock_units=True):
        self.device = device
        self.key = key
        self.key_fp = nessumctl.key_fingerprint(key)
        self.log = log
        self.lock_units = lock_units
        self.run = None          # {"id", "block", "first", "last", "quantity"}
        self.auto = False
        self.busy = False
        self.last = None         # last programming attempt, for the big result tile
        self.handled_serial = None
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
                runs[r["run"]] = {"id": r["run"], "block": block, "quantity": qty, "done": 0, "failed": 0}
        for r in rows:
            if r["run"] in runs:
                if r["status"] in fp.DONE:
                    runs[r["run"]]["done"] += 1
                elif r["status"] == "failed":
                    runs[r["run"]]["failed"] += 1
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
            self.run = None
            self.auto = False
            self.last = None

    # --- programming ---------------------------------------------------------
    def present(self):
        return os.path.exists(self.device)

    def program(self):
        """Program the unit that is plugged in now. Returns the result dict."""
        with self._mutex:
            if self.busy:
                return {"outcome": "error", "message": "already programming"}
            if not self.run:
                return {"outcome": "error", "message": "select a production run first"}
            self.busy = True
            run = dict(self.run)
        res = {}
        try:
            with nessumctl.Console(self.device) as con:
                rc, msg = fp.program_one(con, self.log, run["id"], run["first"], run["last"],
                                         run["quantity"], self.key, lock=self.lock_units, result=res)
        except OSError as e:
            res, msg = {"outcome": "error", "serial": None, "mac": None}, f"cannot open {self.device}: {e.strerror}"
        except (nessumctl.ProtocolError, TimeoutError, ConnectionError) as e:
            res["outcome"] = "error"
            msg = f"adapter not responding correctly: {e}"
        res.update(message=msg, time=now())
        with self._mutex:
            self.busy = False
            self.last = res
            self.handled_serial = res.get("serial") or self.handled_serial
        return res

    def auto_step(self):
        """One iteration of auto mode: program a newly inserted, not-yet-handled unit."""
        if not self.present():
            self._need_check = True     # unplugged (or re-enumerating after REBOOT)
            return None
        if not (self.auto and self.run and not self.busy and self._need_check):
            return None
        self._need_check = False
        try:
            with nessumctl.Console(self.device, timeout=2) as con:
                serial = nessumctl.merged(con.command("VERSION")).get("serial")
        except (OSError, nessumctl.ProtocolError, TimeoutError, ConnectionError):
            self._need_check = True     # not ready yet; try again next tick
            return None
        if serial and serial == self.handled_serial:
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
                "station": {"device": self.device, "present": self.present(), "key_fp": self.key_fp,
                            "log": os.path.abspath(self.log), "auto": self.auto, "busy": self.busy,
                            "lock_units": self.lock_units},
                "last": self.last,
            }
        runs = self.known_runs(rows)
        st["runs"] = runs
        if run:
            info = next((r for r in runs if r["id"] == run["id"]), {"done": 0, "failed": 0})
            used = [fp.mac_to_int(r["mac"]) for r in rows if r.get("mac") and r["run"] == run["id"]]
            next_int = (max(used) + 1) if used else run["first"]
            run.update(done=info["done"], failed=info["failed"],
                       spare_addresses=max(0, run["last"] - next_int + 1),
                       complete=info["done"] >= run["quantity"])
            del run["first"], run["last"]
            st["recent"] = [
                {k: r.get(k, "") for k in ("timestamp", "status", "mac", "serial", "detail")}
                for r in rows if r["run"] == run["id"] and r["status"] in fp.DONE + ("failed",)
            ][-RECENT:][::-1]
        else:
            st["recent"] = []
        st["run"] = run
        return st


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
                st.program()
            elif self.path == "/api/auto":
                if body.get("on") and not st.run:
                    raise ValueError("select a production run first")
                st.auto = bool(body.get("on"))
                st.handled_serial = None if st.auto else st.handled_serial
            else:
                return self._send(404, {"error": "not found"})
        except (ValueError, fp.RunError, nessumctl.MacError) as e:
            return self._send(400, {"error": str(e)})
        return self._send(200, st.state())


def make_server(station, port=8080):
    handler = type("BoundHandler", (Handler,), {"station": station})
    return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("-d", "--device", default=os.environ.get("NESSUM_DEVICE", nessumctl.DEFAULT_DEVICE))
    p.add_argument("--key-file", required=True, help="the common Nessum network key as hex (mode 600)")
    p.add_argument("--log", required=True, help="append-only CSV log shared by all runs")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--no-lock", action="store_true", help="engineering units only: do not lock")
    args = p.parse_args(argv)

    try:
        key = nessumctl.read_key_file(args.key_file)
    except (nessumctl.KeyError_, OSError) as e:
        print(f"factory_ui: bad --key-file: {e}", file=sys.stderr)
        return 2

    station = Station(args.device, key, args.log, lock_units=not args.no_lock)
    stop = threading.Event()
    threading.Thread(target=station.auto_loop, args=(stop,), daemon=True).start()
    server = make_server(station, args.port)
    print(f"Nessum production station: http://127.0.0.1:{args.port}  (key {station.key_fp}, Ctrl-C to stop)")
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
