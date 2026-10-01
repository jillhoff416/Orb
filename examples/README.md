# Example: Si QD ethyl

**This folder is an example**, a snapshot exported with `prepare.py`, to show what the
viewer looks like. Open `Si_QD_ethyl/index.html` in a browser; no server or Python needed
(internet access is needed for the plotting libraries).

- System: Si_QD_ethyl, a 78-atom Si quantum dot model
- Method: ORCA 6.1, HSE06 / def2-TZVP, RIJCOSX, CPCM(toluene), TDA-TDDFT, 50 singlet roots
- Included orbitals: the dominant NTO pair of S1 only (hole 244 → electron 245, n = 0.996).
  The other 49 states appear in the spectrum and state panel but have no cubes, so
  the viewer shows the `make_cubes.sh` command that would generate them.
