#!/usr/bin/env bash
#
# One-shot Hardy install for macOS (Apple silicon and Intel). Takes a clean Mac
# to a working `hardy` command: Homebrew, Python, the Lean toolchain
# (elan/lake), a Mathlib project, pdflatex, and Hardy itself.
#
#   scripts/install-macos.sh              # interactive
#   scripts/install-macos.sh --yes        # unattended
#
# See scripts/lib/common.sh for the platform-independent steps and --help for
# the full option list.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
. "$REPO_ROOT/scripts/lib/common.sh"

TEXBIN=/Library/TeX/texbin

os_label() { printf 'macOS %s (%s)' "$(sw_vers -productVersion 2>/dev/null || printf 'unknown')" "$(uname -m)"; }

use_homebrew_path() {
	local prefix
	for prefix in /opt/homebrew /usr/local; do
		[ -x "$prefix/bin/brew" ] && export PATH="$prefix/bin:$PATH"
	done
}

ensure_homebrew() {
	use_homebrew_path
	have brew && return 0
	confirm "Install Homebrew from https://brew.sh (it manages Python and TeX here)?" ||
		fail "Homebrew is required on macOS; install Python 3.11+ and MacTeX yourself, then re-run"
	# The Homebrew installer prompts for sudo itself and needs the Command Line Tools.
	NONINTERACTIVE=$([ "$ASSUME_YES" = 1 ] && echo 1 || echo "") \
		/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
	use_homebrew_path
	have brew || fail "Homebrew installed but brew is not on PATH; open a new shell and re-run"
}

os_install_prerequisites() {
	step "Installing base prerequisites with Homebrew"
	ensure_homebrew
	brew install python@3.12 git curl
	# Homebrew's python is keg-only under its own name; expose python3.12 first.
	use_homebrew_path
}

# BasicTeX is a few hundred megabytes and already carries amsmath, amsthm,
# amssymb, geometry, and hyperref. --full-latex installs MacTeX (~6 GB).
os_install_latex() {
	step "Installing LaTeX with Homebrew"
	ensure_homebrew
	if [ "$FULL_LATEX" = 1 ]; then
		brew install --cask mactex-no-gui
	else
		brew install --cask basictex
	fi
	[ -d "$TEXBIN" ] && export PATH="$TEXBIN:$PATH"
	if have tlmgr && [ "$FULL_LATEX" = 0 ]; then
		say "adding the packages Hardy's writeups use"
		as_root tlmgr update --self || warn "tlmgr update failed; continuing"
		as_root tlmgr install amsmath amsfonts amscls geometry hyperref latexmk || warn "tlmgr install failed; some LaTeX packages may be missing"
	fi
	ensure_path_entry "$TEXBIN"
}

# BasicTeX/MacTeX put pdflatex in /Library/TeX/texbin, which is not on a fresh
# shell's PATH until the next login.
[ -d "$TEXBIN" ] && export PATH="$TEXBIN:$PATH"
use_homebrew_path

hardy_install_main "$@"
