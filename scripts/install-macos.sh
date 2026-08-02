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

# A copy of this script downloaded on its own (or piped from curl) has no
# scripts/lib/common.sh beside it, so it fetches the rest of the installer and
# re-execs from there. macOS ships curl, so this works before Xcode's git
# exists. The published release comes first: its installers and its wheel are
# one version, where main's installers and a released wheel need not be. Naming
# HARDY_REPO_REF asks for the repository instead, and the repository is also the
# fallback before the first release exists.

# Fetch the release's installer bundle into $1, checked against that release's
# own manifest — these are scripts about to run as this user. Returns 1 when the
# release could not be reached, and 2 when what it served does not match its
# manifest, which is never a reason to go and install something else.
hardy_fetch_installers() {
	target="$1"
	command -v curl >/dev/null 2>&1 || return 1
	rm -rf "$target"
	mkdir -p "$target"
	curl -fsSL "$release_base/SHA256SUMS" -o "$target/SHA256SUMS" 2>/dev/null || return 1
	curl -fsSL "$release_base/hardy-installers.tar.gz" -o "$target/bundle.tar.gz" 2>/dev/null || return 1
	expected="$(sed -n 's/^\([0-9a-fA-F]\{64\}\)[ *]*hardy-installers\.tar\.gz$/\1/p' "$target/SHA256SUMS")"
	if command -v sha256sum >/dev/null 2>&1; then
		actual="$(sha256sum "$target/bundle.tar.gz" | cut -d' ' -f1)"
	elif command -v shasum >/dev/null 2>&1; then
		actual="$(shasum -a 256 "$target/bundle.tar.gz" | cut -d' ' -f1)"
	else
		printf 'error: neither sha256sum nor shasum is here, so the installers cannot be verified\n' >&2
		return 2
	fi
	if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
		printf 'error: the installer bundle at %s does not match that release manifest\n' "$release_base" >&2
		return 2
	fi
	tar xz -C "$target" -f "$target/bundle.tar.gz" 2>/dev/null || return 1
	rm -f "$target/bundle.tar.gz" "$target/SHA256SUMS"
	[ -e "$target/scripts/lib/common.sh" ] || return 1
}

if [ ! -e "$REPO_ROOT/scripts/lib/common.sh" ]; then
	HARDY_REPO_URL="${HARDY_REPO_URL:-https://github.com/charlesmsiegel/hardy}"
	hardy_home="${HARDY_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/hardy}"
	if [ -n "${HARDY_RELEASE_BASE_URL:-}" ]; then
		release_base="${HARDY_RELEASE_BASE_URL%/}"
	elif [ -n "${HARDY_VERSION:-}" ]; then
		release_base="$HARDY_REPO_URL/releases/download/$HARDY_VERSION"
	else
		release_base="$HARDY_REPO_URL/releases/latest/download"
	fi
	# Naming a release means that release. Quietly installing a branch in its
	# place would put code nobody asked for on the machine, under a version
	# number saying otherwise.
	named_a_release=0
	if [ -n "${HARDY_RELEASE_BASE_URL:-}" ] || [ -n "${HARDY_VERSION:-}" ]; then named_a_release=1; fi

	# Fetched afresh every time, unlike a clone: the bundle is a few kilobytes,
	# and a kept copy would be last release's installers reaching for this
	# release's wheel — the mismatch it exists to prevent.
	REPO_ROOT="$hardy_home/installers"
	status=0
	if [ -n "${HARDY_REPO_REF:-}" ]; then
		# A ref names a branch or a tag, which only the repository serves. It
		# also outranks a named release: someone who asked for a ref is asking
		# for the repository, whatever else is set.
		status=1
		named_a_release=0
	else
		printf '==> Fetching the Hardy installers from %s\n' "$release_base"
		hardy_fetch_installers "$REPO_ROOT" || status=$?
	fi
	if [ "$status" != 0 ]; then
		rm -rf "$REPO_ROOT"
		if [ "$status" = 2 ] || [ "$named_a_release" = 1 ]; then
			printf 'error: could not install the release at %s, and will not install something else in its place.\nUnset HARDY_VERSION to take whatever the repository has instead.\n' "$release_base" >&2
			exit 1
		fi
	fi

	# The repository: before the first release exists, and whenever a ref was
	# named. Always re-fetched, so that changing HARDY_REPO_URL or
	# HARDY_REPO_REF cannot silently reinstall the previous one.
	if [ ! -e "$REPO_ROOT/scripts/lib/common.sh" ]; then
		HARDY_REPO_REF="${HARDY_REPO_REF:-main}"
		REPO_ROOT="$hardy_home/src"
		printf '==> Fetching Hardy into %s (ref %s)\n' "$REPO_ROOT" "$HARDY_REPO_REF"
		rm -rf "$REPO_ROOT"
		mkdir -p "$REPO_ROOT"
		fetched=0
		if command -v curl >/dev/null 2>&1; then
			# A ref may be a branch or a tag, GitHub keeps the two in separate
			# namespaces, and a clean machine has no git to ask which this is.
			for namespace in heads tags; do
				curl -fsSL "$HARDY_REPO_URL/archive/refs/$namespace/$HARDY_REPO_REF.tar.gz" 2>/dev/null |
					tar xz -C "$REPO_ROOT" --strip-components=1 2>/dev/null && fetched=1
				if [ "$fetched" = 1 ]; then break; fi
			done
		fi
		if [ "$fetched" = 0 ] && command -v git >/dev/null 2>&1; then
			rm -rf "$REPO_ROOT"
			mkdir -p "$REPO_ROOT"
			git clone --depth 1 --branch "$HARDY_REPO_REF" "$HARDY_REPO_URL" "$REPO_ROOT" && fetched=1
		fi
		[ "$fetched" = 1 ] || {
			printf 'error: could not fetch the Hardy installers from %s, nor the repository at %s (ref %s).\nCheck your network, or clone the repository yourself and run scripts/%s from the clone.\n' "$release_base" "$HARDY_REPO_URL" "$HARDY_REPO_REF" "$SCRIPT_NAME" >&2
			exit 1
		}
	fi

	[ -e "$REPO_ROOT/scripts/lib/common.sh" ] ||
		{ printf 'error: %s does not carry the Hardy installers\n' "$REPO_ROOT" >&2; exit 1; }
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
