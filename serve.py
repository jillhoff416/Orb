#!/usr/bin/env python3
"""
Serve the orbital viewer for one ORCA run folder, reading nto.out and the
.cube files in place (nothing is written to the run folder).

Usage (on the cluster, from your interactive ssh session):
    python3 serve.py /path/to/run/dir [--port 8765] [--title "..."] [--stride 2]

Then on your own computer:
    ssh -O forward -L PORT:localhost:PORT CLUSTER    and open http://localhost:PORT

CLUSTER is your ssh alias for the cluster; set --ssh-host or $ORB_SSH_HOST so the
printed command can be pasted as is.

New cubes appear after a browser refresh. Stop with Ctrl-C.
"""
import argparse
import glob
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

sys.dont_write_bytecode = True  # no __pycache__ in the tool folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prepare  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class Run:
    """State for one run folder: parsed output and encoded cubes, cached by mtime."""

    def __init__(self, run_dir, orca_out, title, stride):
        self.dir = run_dir
        self.out = orca_out
        self.title = title
        self.stride = stride
        self.lock = threading.Lock()
        self._parsed = (None, None)  # (mtime, parsed)
        self._headers = {}           # name -> (mtime, header)
        self._payloads = {}          # name -> (mtime, js)

    def parsed(self):
        mt = os.path.getmtime(self.out)
        with self.lock:
            if self._parsed[0] != mt:
                self._parsed = (mt, prepare.parse_orca(self.out))
            return self._parsed[1]

    def cube_names(self):
        return sorted(os.path.basename(p) for p in glob.glob(os.path.join(self.dir, "*.cube")))

    def header(self, name):
        path = os.path.join(self.dir, name)
        mt = os.path.getmtime(path)
        hit = self._headers.get(name)
        if not hit or hit[0] != mt:
            hit = self._headers[name] = (mt, prepare.read_cube(path, header_only=True))
        return hit[1]

    def meta_js(self):
        parsed = self.parsed()
        orbitals, atoms = [], None
        for name in self.cube_names():
            try:
                hdr = self.header(name)
            except (ValueError, OSError, IndexError) as e:
                print("skipping %s: %s" % (name, e), file=sys.stderr)
                continue
            if atoms is None:
                atoms = hdr["atoms"]
            info = prepare.orbital_info(name, hdr["index"], parsed)
            info.update({"id": name, "file": "orb/%s.js" % name})
            orbitals.append(info)
        meta = prepare.build_meta(parsed, self.out, orbitals, prepare.xyz_from_atoms(atoms),
                                  self.title)
        return "window.VIEWER_META = %s;\n" % json.dumps(meta)

    def payload_js(self, name):
        if name not in self.cube_names():  # only plain cube files in the run folder
            return None
        path = os.path.join(self.dir, name)
        mt = os.path.getmtime(path)
        hit = self._payloads.get(name)
        if not hit or hit[0] != mt:
            cube = prepare.read_cube(path, self.stride)
            hit = self._payloads[name] = (mt, prepare.cube_payload_js(name, cube))
            print("encoded %s (%.1f MB)" % (name, len(hit[1]) / 1e6))
        return hit[1]


def make_handler(run):
    class Handler(BaseHTTPRequestHandler):
        def send(self, body, ctype, status=200):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = unquote(self.path.split("?")[0])
            try:
                if path in ("/", "/index.html"):
                    with open(os.path.join(HERE, "viewer.html"), "rb") as fh:
                        return self.send(fh.read(), "text/html; charset=utf-8")
                if path == "/data/meta.js":
                    return self.send(run.meta_js(), "application/javascript")
                if path.startswith("/data/orb/") and path.endswith(".js"):
                    js = run.payload_js(path[len("/data/orb/"):-len(".js")])
                    if js is not None:
                        return self.send(js, "application/javascript")
                self.send("not found", "text/plain", 404)
            except Exception as e:  # report instead of dropping the connection
                self.send("error: %s" % e, "text/plain", 500)
                raise

        def log_message(self, fmt, *args):
            pass  # keep the terminal quiet; encodes and errors are printed elsewhere

    return Handler


def find_out(run_dir, name):
    if name:
        return os.path.join(run_dir, name)
    if os.path.exists(os.path.join(run_dir, "nto.out")):
        return os.path.join(run_dir, "nto.out")
    outs = sorted(glob.glob(os.path.join(run_dir, "*.out")))
    outs = [o for o in outs if not os.path.basename(o).startswith("slurm-")]
    if len(outs) == 1:
        return outs[0]
    sys.exit("cannot pick the ORCA output in %s; use --out NAME" % run_dir)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="ORCA run folder with nto.out and .cube files")
    ap.add_argument("--out", help="ORCA output file name in run_dir (default nto.out)")
    ap.add_argument("--port", type=int, default=8765, help="first port to try (default 8765)")
    ap.add_argument("--title", help="title shown in the viewer (default: folder name)")
    ap.add_argument("--stride", type=int, default=1,
                    help="send every Nth grid point (faster over slow links; default 1)")
    ap.add_argument("--ssh-host", default=os.environ.get("ORB_SSH_HOST", "CLUSTER"),
                    help="ssh alias shown in the tunnel command (default $ORB_SSH_HOST)")
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        sys.exit("not a folder: %s" % run_dir)
    orca_out = find_out(run_dir, args.out)
    run = Run(run_dir, orca_out, args.title, args.stride)

    parsed = run.parsed()
    cubes = run.cube_names()
    print("%s: %d excited states, %d cube files" % (orca_out, len(parsed["states"]), len(cubes)))

    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(run))
            break
        except OSError:
            continue
    if server is None:
        sys.exit("no free port in %d-%d" % (args.port, args.port + 19))

    print("\nServing on %s, port %d. On your own computer run:\n"
          % (socket.gethostname().split(".")[0], port))
    print("    ssh -O forward -L %d:localhost:%d %s" % (port, port, args.ssh_host))
    print("\nthen open  http://localhost:%d   (Ctrl-C here to stop)\n" % port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
