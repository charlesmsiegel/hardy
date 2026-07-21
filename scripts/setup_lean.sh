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
  # locally modified OR stray-untracked repl silently breaks reproducibility
  # (an untracked .lean/lakefile could even shadow an imported module, and the
  # same files get COPYed into the Docker image). Ignore only .lake/ build
  # output; reject every other modified or untracked path.
  dirty="$(git status --porcelain | grep -vE '^\?\? \.lake/' || true)"
  if [ -n "$dirty" ]; then
    echo "ERROR: vendor/repl has local modifications or stray untracked files;"
    echo "       refusing to build an unreproducible repl. Offending paths:"
    echo "$dirty"
    echo "Delete vendor/repl and re-run."
    exit 1
  fi
  lake build )

echo "OK: repl binary at vendor/repl/.lake/build/bin/repl"
