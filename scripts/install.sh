#!/usr/bin/env bash
# One-shot developer/user setup for Hardy.
#
# Platform policy (DESIGN.md Component 7): WSL is never required. This script
# runs on Linux, macOS, and Windows Git Bash; it is the *convenience wrapper* —
# the cross-platform Python port (scripts/install.py) is the tracked
# first-class path and this file must never grow logic the port lacks.
#
# What it does, in order (each step skippable):
#   1. verify python >= 3.12 and git
#   2. install elan (Lean toolchain manager) if missing
#   3. pip install -e .[dev]
#   4. scripts/setup_lean.sh  — build lean_project (Mathlib) + the repl
#   5. unit-test smoke check  (pytest, unit tier only)
#   6. [--sandbox] fetch/build the sandbox images (Docker required)
#
# Flags: --skip-lean   skip steps 2 and 4 (agent-loop-only development)
#        --sandbox     also do step 6
#        --yes         non-interactive (assume yes for installs)
set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_LEAN=0
WITH_SANDBOX=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --skip-lean) SKIP_LEAN=1 ;;
    --sandbox)   WITH_SANDBOX=1 ;;
    --yes)       ASSUME_YES=1 ;;
    *) echo "unknown flag: $arg (known: --skip-lean --sandbox --yes)"; exit 2 ;;
  esac
done

say()  { printf '\n==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  read -r -p "$1 [y/N] " reply
  [ "$reply" = y ] || [ "$reply" = Y ]
}

# --- 1. prerequisites -------------------------------------------------------
say "Checking prerequisites"
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || fail "python not found (need >= 3.12)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
  || fail "python >= 3.12 required, found $("$PY" --version 2>&1)"
command -v git >/dev/null 2>&1 || fail "git not found"
echo "python: $("$PY" --version 2>&1)  git: $(git --version)"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ON_WINDOWS=1 ;;
  *)                    ON_WINDOWS=0 ;;
esac

# --- 2. elan / Lean toolchain ----------------------------------------------
if [ "$SKIP_LEAN" = 0 ]; then
  if command -v elan >/dev/null 2>&1; then
    say "elan present: $(elan --version)"
  elif [ "$ON_WINDOWS" = 1 ]; then
    # elan's shell installer targets POSIX; on native Windows use the
    # official installer so we never depend on WSL.
    fail "elan not found. Install it natively (no WSL needed):
       winget install leanprover.elan
     (or download elan-init.exe from https://github.com/leanprover/elan/releases)
     then re-run this script."
  else
    say "Installing elan"
    confirm "Download and run the elan installer?" || fail "elan is required (or pass --skip-lean)"
    curl -sSfL https://elan.lean-lang.org/elan-init.sh | sh -s -- -y
    export PATH="$HOME/.elan/bin:$PATH"
    command -v elan >/dev/null 2>&1 || fail "elan install did not land on PATH"
  fi
fi

# --- 3. python package ------------------------------------------------------
say "Installing hardy (editable, with dev extras)"
"$PY" -m pip install -e ".[dev]"

# --- 4. Lean project + repl -------------------------------------------------
if [ "$SKIP_LEAN" = 0 ]; then
  say "Building lean_project (Mathlib cache) + the repl — this is the long step"
  bash scripts/setup_lean.sh
else
  say "Skipping Lean toolchain/build (--skip-lean)"
fi

# --- 5. smoke check ---------------------------------------------------------
say "Running the unit test tier"
"$PY" -m pytest -m "not lean and not tex and not docker" -q

# --- 6. sandbox images (optional) ------------------------------------------
if [ "$WITH_SANDBOX" = 1 ]; then
  say "Sandbox images"
  command -v docker >/dev/null 2>&1 \
    || fail "--sandbox needs Docker (any backend; on Windows, Docker Desktop's Hyper-V backend suffices — WSL not required)"
  # Preferred path: CI-published, digest-pinned images (docker pull). Until CI
  # publishing lands, fall back to a local Nix build where Nix exists.
  if docker image inspect hardy-tex:dev >/dev/null 2>&1 \
     && docker image inspect hardy-lean:dev >/dev/null 2>&1; then
    echo "images already present: hardy-tex:dev, hardy-lean:dev"
  elif command -v nix-build >/dev/null 2>&1; then
    say "Building images from the Nix store (maintainer path; large)"
    nix-build nix/tex-image.nix  && docker load < result
    nix-build nix/lean-image.nix && docker load < result
  else
    fail "sandbox images not present and Nix is unavailable on this host.
     CI-published images are the intended path (docker pull, no Nix, no WSL);
     until that lands, build them on a Linux/macOS host with Nix:
       nix-build nix/tex-image.nix  && docker load < result
       nix-build nix/lean-image.nix && docker load < result"
  fi
fi

say "Done"
cat <<'NEXT'
Next steps:
  pytest -m lean                                    # real-toolchain tests
  scripts/bench_throughput.py --imports "import Mathlib.Tactic"
  scripts/sample_writeup.py                         # writeup pipeline check
  # with --sandbox images present:
  scripts/bench_throughput.py --sandbox             # M0's remaining exit clause
NEXT
