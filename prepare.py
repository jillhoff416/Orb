#!/usr/bin/env python3
"""
Export a self-contained viewer folder (to zip and share) from an ORCA TDDFT
output and cube files. For day-to-day viewing on the cluster use serve.py,
which reads the run folder directly; this module also holds the shared parsers.

Usage:
    python3 prepare.py RUN_DIR/nto.out [CUBES...] -o viewer_out

CUBES can be cube files and/or directories (default: the folder of nto.out).
Recognized cube naming (anything else is shown as a generic orbital):
    *.s<n>.mo<k>a.cube                        orca_plot output from nto.s<n>.nto
    *.mo<k>a.cube                             canonical MO k (orca_plot from .gbw)
    *_S<n>_hole.cube / *_S<n>_electron.cube   NTO pair for state n

Only the Python standard library is needed (Python >= 3.6).
"""
import argparse
import array
import base64
import glob
import gzip
import json
import os
import re
import shutil
import sys

BOHR_TO_ANG = 0.529177210903

ELEMENTS = (
    "X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe "
    "Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg "
    "Tl Pb Bi Po At Rn"
).split()

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# ORCA output parsing
# --------------------------------------------------------------------------

RE_STATE = re.compile(
    r"^STATE\s+(\d+):\s+E=\s+(-?[\d.]+)\s+au\s+(-?[\d.]+)\s+eV"
    r"(?:.*?Mult\s+(\d+))?"
)
RE_CONTRIB = re.compile(
    r"^\s+(\d+)([ab])\s+->\s+(\d+)([ab])\s+:\s+(-?[\d.]+)(?:\s+\(c=\s*(-?[\d.]+)\))?"
)
RE_NTO_HDR = re.compile(r"NATURAL TRANSITION ORBITALS FOR STATE\s+(\d+)")
RE_NTO_PAIR = re.compile(r"^\s+(\d+)([ab])\s+->\s+(\d+)([ab])\s+:\s+n=\s*(-?[\d.]+)")
RE_ORB = re.compile(r"^\s*(\d+)\s+([\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*$")


def parse_spectrum_row(line):
    """Return (state, value) from an ABSORPTION/CD table row (ORCA 5 or 6 layout)."""
    tok = line.split()
    try:
        if len(tok) > 3 and tok[1] == "->":  # ORCA 6: 0-1A  ->  1-1A  eV  cm-1  nm  value ...
            return int(tok[2].split("-")[0]), float(tok[6])
        return int(tok[0]), float(tok[3])  # ORCA 5: state  cm-1  nm  value ...
    except (ValueError, IndexError):
        return None


def parse_orca(path):
    states = {}  # key (mult, n) -> dict
    nto_pairs = {}  # n -> list
    fosc, rot = {}, {}
    orbitals = {"a": [], "b": []}

    mode, cur, spin = None, None, "a"
    triplets = False
    with open(path, errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if "EXCITED STATES (SINGLETS)" in line:
                mode, triplets, cur = "states", False, None
                continue
            if "EXCITED STATES (TRIPLETS)" in line:
                mode, triplets, cur = "states", True, None
                continue
            m = RE_NTO_HDR.search(line)
            if m:
                mode, cur = "nto", int(m.group(1))
                nto_pairs[cur] = []
                continue
            if "SPECTRUM" in line and ("ABSORPTION" in line or "CD" in line):
                if "VIA TRANSITION ELECTRIC DIPOLE" in line:
                    mode = "abs" if "ABSORPTION" in line else "cd"
                else:
                    mode = None
                continue
            if s == "ORBITAL ENERGIES":
                mode, spin = "orb", "a"
                orbitals = {"a": [], "b": []}
                continue

            if mode == "states":
                m = RE_STATE.match(line)
                if m:
                    n = int(m.group(1))
                    mult = int(m.group(4)) if m.group(4) else (3 if triplets else 1)
                    cur = states[(mult, n)] = {
                        "n": n, "mult": mult, "ev": float(m.group(3)), "contribs": [],
                    }
                    continue
                m = RE_CONTRIB.match(line)
                if m and cur is not None:
                    cur["contribs"].append({
                        "from": int(m.group(1)), "fs": m.group(2),
                        "to": int(m.group(3)), "ts": m.group(4),
                        "w": float(m.group(5)),
                        "c": float(m.group(6)) if m.group(6) else None,
                    })
                    continue
                if s.startswith("---") and cur is not None and not s.strip("-"):
                    mode = None  # end of block
            elif mode == "nto":
                m = RE_NTO_PAIR.match(line)
                if m:
                    nto_pairs[cur].append({
                        "from": int(m.group(1)), "to": int(m.group(3)),
                        "spin": m.group(2), "n": float(m.group(5)),
                    })
            elif mode in ("abs", "cd"):
                r = parse_spectrum_row(line)
                if r:
                    (fosc if mode == "abs" else rot)[r[0]] = r[1]
            elif mode == "orb":
                if "SPIN DOWN" in line:
                    spin = "b"
                    continue
                m = RE_ORB.match(line)
                if m:
                    orbitals[spin].append({
                        "i": int(m.group(1)), "occ": float(m.group(2)), "ev": float(m.group(4)),
                    })
                elif orbitals[spin] and s and "SPIN" not in line:
                    mode = None  # first unrelated line after the table

    out_states = []
    for (mult, n), st in sorted(states.items()):
        st["fosc"] = fosc.get(n) if mult == 1 else None
        st["rot"] = rot.get(n) if mult == 1 else None
        st["ntos"] = nto_pairs.get(n, []) if mult == 1 else []
        st["nm"] = 1239.84198 / st["ev"] if st["ev"] > 0 else None
        out_states.append(st)

    homo = {}
    for sp, orbs in orbitals.items():
        occ = [o["i"] for o in orbs if o["occ"] > 0.5]
        if occ:
            homo[sp] = max(occ)
    return {"states": out_states, "orbitals": orbitals, "homo": homo}


# --------------------------------------------------------------------------
# Cube handling
# --------------------------------------------------------------------------

def read_cube(path, stride=1, header_only=False):
    with open(path) as fh:
        fh.readline()
        c2 = fh.readline()
        t = fh.readline().split()
        natoms_signed = int(t[0])
        natoms = abs(natoms_signed)
        origin = [float(x) for x in t[1:4]]
        n, axes = [], []
        for _ in range(3):
            t = fh.readline().split()
            n.append(int(t[0]))
            axes.append([float(x) for x in t[1:4]])
        if any(v < 0 for v in n):
            raise ValueError("%s: Angstrom-unit cubes (negative voxel count) not supported" % path)
        atoms = []
        for _ in range(natoms):
            t = fh.readline().split()
            atoms.append((int(t[0]), [float(x) for x in t[2:5]]))
        mo_ids = []
        if natoms_signed < 0:
            t = fh.readline().split()
            nmo = int(t[0])
            mo_ids = [int(x) for x in t[1:]]
            while len(mo_ids) < nmo:
                mo_ids += [int(x) for x in fh.readline().split()]
            if nmo > 1:
                raise ValueError("%s: multi-orbital cubes not supported" % path)
        m = re.search(r"orbital\s+(\d+)", c2, re.I)
        index = mo_ids[0] if mo_ids else (int(m.group(1)) if m else None)
        cube = {"n": n, "origin": origin, "axes": axes, "atoms": atoms, "index": index}
        if header_only:
            return cube
        vals = array.array("f", (float(x) for x in fh.read().split()))

    nx, ny, nz = n
    if len(vals) != nx * ny * nz:
        raise ValueError("%s: expected %d values, found %d" % (path, nx * ny * nz, len(vals)))

    if stride > 1:
        sub = array.array("f")
        for i in range(0, nx, stride):
            for j in range(0, ny, stride):
                base = (i * ny + j) * nz
                sub.extend(vals[base:base + nz:stride])
        vals = sub
        cube["n"] = [len(range(0, nx, stride)), len(range(0, ny, stride)), len(range(0, nz, stride))]
        cube["axes"] = [[x * stride for x in ax] for ax in axes]
    cube["values"] = vals
    return cube


def encode_values(vals):
    """Quantize to int16 with a per-cube scale, gzip, base64."""
    vmax = max(abs(min(vals)), abs(max(vals))) or 1.0
    scale = vmax / 32767.0
    q = array.array("h", (int(round(v / scale)) for v in vals))
    if sys.byteorder != "little":
        q.byteswap()
    return scale, base64.b64encode(gzip.compress(q.tobytes(), 9)).decode("ascii")


def cube_payload_js(oid, cube):
    """JS file contents that hand one encoded cube to the viewer."""
    scale, b64 = encode_values(cube["values"])
    payload = {"n": cube["n"], "origin": cube["origin"], "axes": cube["axes"],
               "scale": scale, "data": b64}
    return "window.__orbData(%s,%s);\n" % (json.dumps(oid), json.dumps(payload))


def classify_cube(path, index, homo):
    name = os.path.basename(path)
    m = re.search(r"_S(\d+)_(hole|electron)\.cube$", name, re.I)
    if m:
        return {"kind": "nto", "state": int(m.group(1)), "role": m.group(2).lower()}
    m = re.search(r"\.s(\d+)\.mo(\d+)([ab]?)\.cube$", name, re.I)
    if m:
        idx = int(m.group(2))
        role = "hole" if homo is not None and idx <= homo else "electron"
        return {"kind": "nto", "state": int(m.group(1)), "role": role, "index": idx}
    m = re.search(r"\.mo(\d+)([ab]?)\.cube$", name, re.I)
    if m:
        return {"kind": "mo", "index": int(m.group(1))}
    return {"kind": "other"}


def orbital_info(path, index, parsed):
    """Viewer metadata (kind, state, role, label, n / energy) for one cube."""
    homo = parsed["homo"].get("a")
    info = classify_cube(path, index, homo)
    if info.get("index") is None:
        info["index"] = index
    idx = info["index"]
    if info["kind"] == "nto":
        label = "S%d %s" % (info["state"], info["role"])
        st = next((s for s in parsed["states"] if s["n"] == info["state"] and s["mult"] == 1), None)
        if st and idx is not None:
            for p in st["ntos"]:
                if idx in (p["from"], p["to"]):
                    info["n"] = p["n"]
                    break
    elif info["kind"] == "mo":
        label = "MO %d (%s)" % (idx, orbital_label(idx, homo))
        info["ev"] = next((o["ev"] for o in parsed["orbitals"]["a"] if o["i"] == idx), None)
    else:
        label = os.path.splitext(os.path.basename(path))[0]
    info.update({"label": label, "source": os.path.basename(path)})
    return info


def xyz_from_atoms(atoms):
    if not atoms:
        return ""
    lines = ["%d" % len(atoms), "from cube"]
    for z, (x, y, zz) in atoms:
        sym = ELEMENTS[z] if 0 < z < len(ELEMENTS) else "X"
        lines.append("%-2s %12.6f %12.6f %12.6f" % (sym, x * BOHR_TO_ANG, y * BOHR_TO_ANG,
                                                   zz * BOHR_TO_ANG))
    return "\n".join(lines) + "\n"


def build_meta(parsed, orca_out, orbitals, xyz, title=None):
    run_dir = os.path.dirname(os.path.abspath(orca_out))
    return {
        "title": title or os.path.basename(run_dir),
        "source": os.path.basename(orca_out),
        "basename": os.path.splitext(os.path.basename(orca_out))[0],
        "runDir": run_dir,
        "homo": parsed["homo"].get("a"),
        "orbitalEnergies": parsed["orbitals"]["a"],
        "states": parsed["states"],
        "orbitals": orbitals,
        "xyz": xyz,
    }


def collect_cubes(items):
    files = []
    for it in items:
        if os.path.isdir(it):
            files += sorted(glob.glob(os.path.join(it, "*.cube")))
        elif os.path.isfile(it):
            files.append(it)
        else:
            files += sorted(glob.glob(it))
    seen, out = set(), []
    for f in files:
        k = os.path.abspath(f)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def orbital_label(i, homo):
    if homo is None:
        return str(i)
    d = i - homo
    if d == 0:
        return "HOMO"
    if d == 1:
        return "LUMO"
    return "HOMO%d" % d if d < 0 else "LUMO+%d" % (d - 1)


# --------------------------------------------------------------------------
# Main: export a self-contained viewer folder (for zipping / sharing)
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("orca_out", help="ORCA TDDFT output file (e.g. nto.out)")
    ap.add_argument("cubes", nargs="*",
                    help="cube files or directories (default: the folder containing orca_out)")
    ap.add_argument("-o", "--outdir", default="viewer_out", help="output folder (default viewer_out)")
    ap.add_argument("--title", help="title shown in the viewer")
    ap.add_argument("--stride", type=int, default=1,
                    help="keep every Nth grid point to shrink files (default 1 = full grid)")
    args = ap.parse_args()

    parsed = parse_orca(args.orca_out)
    print("Parsed %d excited states, %d orbital energies, HOMO = %s"
          % (len(parsed["states"]), len(parsed["orbitals"]["a"]), parsed["homo"].get("a")))
    if not parsed["states"]:
        print("WARNING: no excited states found in %s" % args.orca_out)

    datadir = os.path.join(args.outdir, "data")
    os.makedirs(datadir, exist_ok=True)
    for f in glob.glob(os.path.join(datadir, "orb_*.js")):
        os.remove(f)

    cube_files = collect_cubes(args.cubes or [os.path.dirname(os.path.abspath(args.orca_out))])
    orbitals, atoms = [], None
    for k, path in enumerate(cube_files):
        print("  reading %s ..." % os.path.basename(path), end="", flush=True)
        try:
            cube = read_cube(path, args.stride)
        except ValueError as e:
            print(" skipped (%s)" % e)
            continue
        if atoms is None:
            atoms = cube["atoms"]
        info = orbital_info(path, cube["index"], parsed)
        info.update({"id": "orb%03d" % k, "file": "orb_%03d.js" % k})
        js = cube_payload_js(info["id"], cube)
        with open(os.path.join(datadir, info["file"]), "w") as fh:
            fh.write(js)
        orbitals.append(info)
        print(" %s  [%dx%dx%d, %.1f MB]" % (info["label"], cube["n"][0], cube["n"][1],
                                            cube["n"][2], len(js) / 1e6))
    if atoms is None:
        print("WARNING: no cubes read; the 3D panel will be empty")

    meta = build_meta(parsed, args.orca_out, orbitals, xyz_from_atoms(atoms), args.title)
    meta["runDir"] = None  # exports are for sharing: don't embed local paths
    with open(os.path.join(datadir, "meta.js"), "w") as fh:
        fh.write("window.VIEWER_META = %s;\n" % json.dumps(meta))
    shutil.copy(os.path.join(HERE, "viewer.html"), os.path.join(args.outdir, "index.html"))
    print("Wrote %s (open index.html in a browser)" % os.path.abspath(args.outdir))


if __name__ == "__main__":
    main()
