# Orb — ORCA orbital & spectrum viewer

A small web viewer for ORCA TDDFT results: overlay any number of MOs / NTOs on the
structure and click through the absorption or CD spectrum to see which orbitals
each excited state involves.

- 3D isosurfaces (3Dmol.js) with per-orbital toggles and colours, isovalue/opacity sliders
- Spectrum (Plotly): sticks + adjustable Gaussian envelope, absorption or CD, eV or nm
- Click a state → its NTO pairs (n) and canonical MO excitations (weights, coefficients),
  with the dominant NTO pair shown automatically
- Built for HPC use: cubes stay in the ORCA run folder on the cluster; the viewer is
  served from there and opened in your local browser over an SSH tunnel
- Python 3 standard library only — nothing to install

Tested with ORCA 6.1 output (TDA singlets, `DoNTO true`); the parser also reads
the ORCA 5 spectrum layout.

**Try it:** open [`examples/Si_QD_ethyl/index.html`](examples/Si_QD_ethyl) in a browser
(see [examples/README.md](examples/README.md)).

| file            | what it does                                                        |
|-----------------|---------------------------------------------------------------------|
| `make_cubes.sh` | runs orca_plot to make cubes inside a run folder                    |
| `serve.py`      | serves the viewer for a run folder (reads the .out and cubes in place) |
| `viewer.html`   | the web page (served by serve.py, or copied by prepare.py)          |
| `prepare.py`    | exports a self-contained copy to zip and share; shared parsers      |

## Setup

Clone or copy this folder to the cluster. In the examples below, `CLUSTER` is your
ssh alias for it and `RUN` is an ORCA run folder containing `nto.out`, `nto.gbw`
and `nto.s*.nto`.

If your cluster needs a password/MFA at every login, connection sharing saves
logging in repeatedly and makes the tunnel reach the same login node. Add this to
the cluster's `Host` block in `~/.ssh/config` on your computer:

    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 8h

## 1. Log in (on your computer)

    ssh CLUSTER

Keep this window open.

## 2. Make cubes (on the cluster)

    module load orca               # or however orca_plot gets on your PATH
    RUN=/path/to/orca/run

    ./make_cubes.sh -n $RUN 1-10     # dry run: which cubes would be made
    ./make_cubes.sh $RUN 1-10        # NTO pairs with n >= 0.1 for S1..S10
    ./make_cubes.sh -m $RUN 240-249  # canonical MOs from nto.gbw (optional)

Cubes are written into `$RUN` with orca_plot's names (`nto.s3.mo244a.cube`).
Numbers can be lists and ranges (`1-5,12 20`). Existing cubes are skipped
(`-f` regenerates); `-w 0.05` changes the NTO cutoff; `-g 100` the grid
(default 80); `-b NAME` for outputs not called `nto.*`; `-h` lists all options.

## 3. Start the viewer server (on the cluster)

    python3 serve.py $RUN --title "My system"

It prints the port (8765, or the next free one) and the tunnel command. Leave it
running; Ctrl-C stops it. Nothing is written to the run folder. Set
`export ORB_SSH_HOST=CLUSTER` (your alias) so the printed command can be pasted as is.
To keep it running in the background:

    nohup python3 serve.py $RUN > ~/serve.log 2>&1 &
    # stop later with:  pkill -f serve.py

## 4. Open the tunnel and the page (on your computer, a second window)

    ssh -O forward -L 8765:localhost:8765 CLUSTER     # with ControlMaster
    ssh -N -L 8765:localhost:8765 CLUSTER             # without; leave it open

then open **http://localhost:8765**. After making more cubes, just refresh.

## Sharing a snapshot

    python3 prepare.py $RUN/nto.out -o my_viewer

writes a self-contained folder (~1 MB per orbital) to zip and send; open
`index.html` directly, no server needed. `--stride 2` shrinks it further.

## Using the viewer

- Click a stick or peak in the spectrum, or use the state dropdown/arrows.
  With *auto-show NTOs*, the dominant NTO pair of that state is displayed.
- The state panel lists NTO pairs and canonical MO excitations, with show/hide
  buttons where cubes exist and the `make_cubes.sh` command where they don't.
- Tick any orbitals in the list to overlay them. The colour swatch sets the + lobe;
  the − lobe is a lighter tint.

## Cube naming understood

`nto.s3.mo244a.cube` (NTO of S3), `nto.mo245a.cube` (canonical MO),
`*_S3_hole.cube` / `*_S3_electron.cube`; anything else is listed under "Other".

## Notes

- `make_cubes.sh` drives `orca_plot -i` with the ORCA 6.1 menu numbers
  (2 orbital, 4 grid, 11 generate, 12 exit). Other versions may number them differently.
- The page loads 3Dmol.js and Plotly from their CDNs, so the browser needs internet access.
- Restricted (closed-shell) calculations only for now.

## License

MIT — see [LICENSE](LICENSE).
