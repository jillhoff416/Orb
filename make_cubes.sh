#!/bin/bash
# Generate cube files with orca_plot in an ORCA run folder
# (run on the cluster, after `module load orca/6.1.0`).
#
#   make_cubes.sh RUN_DIR 1              NTO cubes for S1 (pairs with n >= 0.1, from nto.out)
#   make_cubes.sh RUN_DIR 1-10 15        NTO cubes for S1..S10 and S15
#   make_cubes.sh -m RUN_DIR 240-249     canonical MOs 240..249 from nto.gbw
#
# Cubes are written into RUN_DIR next to the .nto/.gbw files, with orca_plot's
# names (nto.s3.mo244a.cube, nto.mo245a.cube). Existing cubes are skipped
# unless -f is given. orca_plot also writes a small nto.sN.xyz per state.
#
# Options:
#   -b BASE  ORCA base name (default: nto)
#   -w W     minimum NTO occupation n to include a pair (default: 0.1)
#   -m       numbers are canonical MO indices (uses BASE.gbw), not states
#   -g GRID  grid intervals (default: 80, or $GRID)
#   -f       regenerate cubes that already exist
#   -n       dry run: print what would be generated, run nothing
#
# Menu numbers are for ORCA 6.1 orca_plot (2 = orbital no, 4 = grid,
# 11 = generate, 12 = exit; MO plot and Cube format are the defaults).
set -e

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

BASE=nto MINW=0.1 MO=0 GRID=${GRID:-80} FORCE=0 DRY=0
ORCA_PLOT=${ORCA_PLOT:-orca_plot}

while getopts "b:w:mg:fnh" opt; do
  case $opt in
    b) BASE=$OPTARG ;; w) MINW=$OPTARG ;; m) MO=1 ;;
    g) GRID=$OPTARG ;; f) FORCE=1 ;; n) DRY=1 ;; *) usage ;;
  esac
done
shift $((OPTIND - 1))
[ $# -ge 2 ] || usage

DIR=$1; shift
[ -d "$DIR" ] || { echo "not a folder: $DIR" >&2; exit 1; }
DIR=$(cd "$DIR" && pwd)

# "1-3,7 9" -> 1 2 3 7 9
expand() {
  local part
  for part in $(echo "$*" | tr ',' ' '); do
    if [[ $part =~ ^([0-9]+)-([0-9]+)$ ]]; then seq "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    elif [[ $part =~ ^[0-9]+$ ]]; then echo "$part"
    else echo "bad number or range: $part" >&2; exit 1
    fi
  done
}

# NTO orbital indices for state $1 with n >= MINW (always at least the top pair)
nto_orbitals() {
  awk -v s="$1" -v w="$MINW" '
    /NATURAL TRANSITION ORBITALS FOR STATE/ { on = ($NF == s); k = 0; next }
    on && / -> / && /n=/ {
      f = $1; t = $3; sub(/[ab]$/, "", f); sub(/[ab]$/, "", t)
      if (k++ == 0 || $NF + 0 >= w) printf "%s %s ", f, t
    }' "$DIR/$BASE.out"
}

# run_plot FILE ORB... : one orca_plot call (inside DIR) for all requested orbitals of FILE
run_plot() {
  local file=$1; shift
  local stem=${file%.*}
  local todo=() orb
  for orb in "$@"; do
    if [ $FORCE = 0 ] && [ -s "$DIR/$stem.mo${orb}a.cube" ]; then
      echo "exists:  $stem.mo${orb}a.cube"
    else
      todo+=("$orb")
    fi
  done
  [ ${#todo[@]} -gt 0 ] || return 0
  if [ $DRY = 1 ]; then
    echo "would make: $(printf "$stem.mo%sa.cube " "${todo[@]}")"
    return 0
  fi
  if [ ! -e "$DIR/$file" ]; then echo "not found: $DIR/$file" >&2; return 1; fi

  local log; log=$(mktemp "${TMPDIR:-/tmp}/make_cubes_$stem.XXXX.log")
  (
    cd "$DIR"
    {
      echo 4; echo "$GRID"
      for orb in "${todo[@]}"; do echo 2; echo "$orb"; echo 11; done
      echo 12
    } | "$ORCA_PLOT" "$file" -i
  ) > "$log" 2>&1 || true

  local bad=0
  for orb in "${todo[@]}"; do
    if [ -s "$DIR/$stem.mo${orb}a.cube" ]; then echo "made:    $stem.mo${orb}a.cube"
    else echo "MISSING: $stem.mo${orb}a.cube" >&2; bad=1; fi
  done
  if [ $bad = 1 ]; then echo "orca_plot log kept at $log" >&2; else rm -f "$log"; fi
}

if [ $DRY = 0 ] && ! command -v "$ORCA_PLOT" >/dev/null; then
  echo "orca_plot not found; run 'module load orca/6.1.0' first" >&2
  exit 1
fi

echo "run folder: $DIR"
if [ $MO = 1 ]; then
  run_plot "$BASE.gbw" $(expand "$@")
  exit
fi

[ -f "$DIR/$BASE.out" ] || { echo "cannot find $DIR/$BASE.out (use -b BASE)" >&2; exit 1; }
for s in $(expand "$@"); do
  orbs=$(nto_orbitals "$s")
  if [ -z "$orbs" ]; then echo "S$s: no NTOs in $BASE.out, skipped" >&2; continue; fi
  echo "S$s: orbitals $orbs"
  run_plot "$BASE.s$s.nto" $orbs || true
done
