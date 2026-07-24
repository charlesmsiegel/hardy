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

SCRIPT_NAME=install-macos.sh
REPO_ROOT=""
script_directory="$(dirname "${BASH_SOURCE[0]:-$0}")"
if [ -d "$script_directory" ]; then REPO_ROOT="$(cd "$script_directory/.." && pwd)"; fi

# Installing Hardy means installing this source tree, so a copy of the script
# downloaded on its own (or piped from curl) fetches the repository and re-execs
# from there. macOS ships curl, so this works before Xcode's git exists.
if [ ! -e "$REPO_ROOT/scripts/lib/common.sh" ]; then
	HARDY_REPO_URL="${HARDY_REPO_URL:-https://github.com/charlesmsiegel/hardy}"
	HARDY_REPO_REF="${HARDY_REPO_REF:-main}"
	REPO_ROOT="${HARDY_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/hardy}/src"
	if [ ! -e "$REPO_ROOT/scripts/lib/common.sh" ]; then
		printf '==> Fetching Hardy into %s\n' "$REPO_ROOT"
		rm -rf "$REPO_ROOT"
		mkdir -p "$REPO_ROOT"
		fetched=0
		if command -v curl >/dev/null 2>&1; then
			curl -fsSL "$HARDY_REPO_URL/archive/refs/heads/$HARDY_REPO_REF.tar.gz" 2>/dev/null |
				tar xz -C "$REPO_ROOT" --strip-components=1 2>/dev/null && fetched=1
		fi
		if [ "$fetched" = 0 ] && command -v git >/dev/null 2>&1; then
			rm -rf "$REPO_ROOT"
			mkdir -p "$REPO_ROOT"
			git clone --depth 1 --branch "$HARDY_REPO_REF" "$HARDY_REPO_URL" "$REPO_ROOT" && fetched=1
		fi
		[ "$fetched" = 1 ] || {
			printf 'error: could not fetch %s (ref %s).\nCheck your network, or clone the repository yourself and run scripts/%s from the clone.\n' "$HARDY_REPO_URL" "$HARDY_REPO_REF" "$SCRIPT_NAME" >&2
			exit 1
		}
	fi
	[ -e "$REPO_ROOT/scripts/lib/common.sh" ] ||
		{ printf 'error: %s does not look like the Hardy repository\n' "$REPO_ROOT" >&2; exit 1; }
	exec bash "$REPO_ROOT/scripts/$SCRIPT_NAME" "$@"
fi
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
. "$REPO_ROOT/scripts/lib/common.sh"

TEXBIN=/Library/TeX/texbin

os_label() { printf 'macOS %s (%s)' "$(sw_vers -productVersion 2>/dev/null || printf 'unknown')" "$(uname -m)"; }

# Must end in a success: a function whose last statement is a failed test
# returns non-zero, and `set -e` would kill the installer on a Mac that has no
# Homebrew yet — which is exactly the clean machine this script exists for.
use_homebrew_path() {
	local prefix
	for prefix in /opt/homebrew /usr/local; do
		if [ -x "$prefix/bin/brew" ]; then export PATH="$prefix/bin:$PATH"; fi
	done
	return 0
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
if [ -d "$TEXBIN" ]; then export PATH="$TEXBIN:$PATH"; fi
use_homebrew_path

hardy_install_main "$@"
