#!/usr/bin/env bash
# Builds the pinned Lean project (with Mathlib's prebuilt oleans) and the
# matching leanprover-community/repl binary. Idempotent; safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

TOOLCHAIN="$(cat lean_project/lean-toolchain)"     # e.g. leanprover/lean4:v4.15.0
TAG="${TOOLCHAIN#*:}"                              # e.g. v4.15.0

echo "==> Building lean_project (toolchain ${TOOLCHAIN})"
( cd lean_project
  lake exe cache get
  lake build )

echo "==> Building repl at ${TAG}"
mkdir -p vendor
if [ ! -d vendor/repl ]; then
  git clone https://github.com/leanprover-community/repl vendor/repl
fi
( cd vendor/repl
  git fetch --tags
  if ! git checkout --detach "${TAG}" 2>/dev/null; then
    echo "ERROR: repl has no tag ${TAG}. Find the newest commit whose"
    echo "lean-toolchain matches ours and check it out explicitly:"
    git log --oneline --all -20 -- lean-toolchain
    exit 1
  fi
  # Reproducibility gate: the repl MUST be built at our exact toolchain.
  if ! diff -q lean-toolchain ../../lean_project/lean-toolchain; then
    echo "ERROR: repl toolchain ($(cat lean-toolchain)) != project toolchain (${TOOLCHAIN})"
    exit 1
  fi
  # ...and from pristine sources: checkout does not discard local edits, and a
  # locally modified repl silently breaks benchmark reproducibility.
  # (--untracked-files=no ignores build artifacts like .lake/.)
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "ERROR: vendor/repl has local modifications; refusing to build an"
    echo "       unreproducible repl. Delete vendor/repl and re-run."
    exit 1
  fi
  lake build )

echo "OK: repl binary at vendor/repl/.lake/build/bin/repl"
