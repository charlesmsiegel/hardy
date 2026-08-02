#!/usr/bin/env bash
#
# Removes Hardy from Linux or macOS.
#
#   scripts/uninstall.sh                 # asks about each part
#   scripts/uninstall.sh --yes           # unattended, keeping what it did not ask about
#   scripts/uninstall.sh --yes --all     # unattended, taking everything Hardy installed
#
# Removal needs none of the per-distribution package logic the installers do,
# so Linux and macOS share this one script. Paths come from lib/common.sh, so
# an install moved with --prefix is removed from where it actually went.
#
# Left alone always: TeX, Node, and the Claude Code CLI. Hardy may have
# installed them, but they are ordinary shared tools and something else on the
# machine is likely to want them.
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Reached from Git Bash, this would remove POSIX paths that Windows never used
# and leave the real install untouched.
# HARDY_SKIP_PLATFORM_CHECK=1 is for the test suite, and for anyone who
# genuinely means to drive this script from such a shell.
case "${HARDY_SKIP_PLATFORM_CHECK:-0}:$(uname -s)" in
0:MINGW* | 0:MSYS* | 0:CYGWIN* | 0:Windows_NT)
	cat >&2 <<EOF
error: this looks like Windows.

Hardy's Windows uninstaller is PowerShell, not shell. Run it from PowerShell:

  powershell -ExecutionPolicy Bypass -File $script_directory\\uninstall-windows.ps1

If you are deliberately removing an install inside a WSL distribution, run this
script from within that distribution instead.
EOF
	exit 2
	;;
esac

# shellcheck source=lib/common.sh
. "$script_directory/lib/common.sh"

REMOVE_LEAN_PROJECT=0
REMOVE_CONFIG=0
REMOVE_TOOLCHAIN=0
ELAN_HOME="${ELAN_HOME:-$HOME/.elan}"
removed=0
kept=0

usage() {
	cat <<EOF
Usage: $0 [options]

Removes Hardy: the virtual environment, whatever the installer fetched (a
source tree, or the installer scripts of a release install), the \`hardy\`
command, and the PATH lines the installer added.

Asks before removing anything expensive to rebuild or personal. With --yes and
no other flag the answer to each of those questions is no.

Options:
  --yes, -y              non-interactive; keep whatever was not asked for by flag
  --all                  also remove the Lean project, the config, and elan
  --remove-lean-project  also remove $LEAN_PROJECT
  --remove-config        also remove $HARDY_CONFIG
  --remove-toolchain     also remove elan and the Lean toolchain ($ELAN_HOME)
  --prefix DIR           where Hardy was installed (default $HARDY_HOME)
  --bin-dir DIR          where the \`hardy\` command was linked (default $HARDY_BIN_DIR)
  -h, --help             show this message

TeX, Node, and the Claude Code CLI are never removed.
EOF
}

parse_uninstall_arguments() {
	while [ $# -gt 0 ]; do
		case "$1" in
		--yes | -y) ASSUME_YES=1 ;;
		--all)
			REMOVE_LEAN_PROJECT=1
			REMOVE_CONFIG=1
			REMOVE_TOOLCHAIN=1
			;;
		--remove-lean-project) REMOVE_LEAN_PROJECT=1 ;;
		--remove-config) REMOVE_CONFIG=1 ;;
		--remove-toolchain) REMOVE_TOOLCHAIN=1 ;;
		--prefix)
			[ $# -ge 2 ] || fail "--prefix needs a directory"
			HARDY_HOME="$2"
			shift
			;;
		--bin-dir)
			[ $# -ge 2 ] || fail "--bin-dir needs a directory"
			HARDY_BIN_DIR="$2"
			shift
			;;
		-h | --help)
			usage
			exit 0
			;;
		*) fail "unknown option: $1 (try --help)" ;;
		esac
		shift
	done
	VENV="$HARDY_HOME/venv"
	LEAN_PROJECT="$HARDY_HOME/lean"
}

# Nothing here may fail when its target is already gone: uninstalling twice, or
# after an install that stopped halfway, has to end cleanly rather than abort on
# the first missing directory.
drop() {
	local what="$1" path="$2"
	if [ -e "$path" ] || [ -L "$path" ]; then
		rm -rf "$path"
		say "removed $what ($path)"
		removed=$((removed + 1))
	fi
}

keeping() {
	say "keeping $1 ($2)"
	kept=$((kept + 1))
}

# A question only worth asking when there is something to remove. `confirm`
# answers yes to everything under --yes, which is the wrong default here, so
# the flag decides before the prompt is ever reached.
wanted() {
	local flag="$1" question="$2" path="$3"
	[ -e "$path" ] || return 1
	[ "$flag" = 1 ] && return 0
	[ "$ASSUME_YES" = 1 ] && return 1
	confirm "$question"
}

human_size() {
	du -sh "$1" 2>/dev/null | cut -f1 || printf '?'
}

remove_path_entries() {
	local file line found=0
	local -a targets=("$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.bash_profile")
	line="added by the Hardy installer"
	for file in "${targets[@]}"; do
		[ -f "$file" ] || continue
		grep -Fq "$line" "$file" 2>/dev/null || continue
		# Only lines carrying the installer's own marker: a PATH entry the user
		# wrote for the same directory is theirs, not ours to delete.
		local temporary
		temporary="$(mktemp)"
		grep -Fv "$line" "$file" >"$temporary"
		cat "$temporary" >"$file"
		rm -f "$temporary"
		say "removed the PATH line from $file"
		found=1
	done
	[ "$found" = 1 ] && removed=$((removed + 1))
	return 0
}

hardy_uninstall_main() {
	parse_uninstall_arguments "$@"
	step "Removing Hardy"
	say "prefix: $HARDY_HOME"

	drop "the hardy command" "$HARDY_BIN_DIR/hardy"
	drop "the virtual environment" "$VENV"
	drop "the fetched source tree" "$HARDY_HOME/src"
	# What a release install leaves behind instead of a source tree: the
	# installer scripts it was run from, and any half-finished download.
	drop "the fetched installers" "$HARDY_HOME/installers"
	drop "the installers an update displaced" "$HARDY_HOME/installers.previous"
	drop "an interrupted download" "$HARDY_HOME/download"
	drop "an interrupted installer refresh" "$HARDY_HOME/installers.new"
	remove_path_entries

	if wanted "$REMOVE_LEAN_PROJECT" "Remove the Lean project at $LEAN_PROJECT ($(human_size "$LEAN_PROJECT"))? Rebuilding it is a multi-gigabyte download." "$LEAN_PROJECT"; then
		drop "the Lean project" "$LEAN_PROJECT"
	elif [ -e "$LEAN_PROJECT" ]; then
		keeping "the Lean project" "$LEAN_PROJECT"
	fi

	if wanted "$REMOVE_CONFIG" "Remove the config file at $HARDY_CONFIG? It holds your model choice." "$HARDY_CONFIG"; then
		drop "the config" "$HARDY_CONFIG"
		rmdir "$(dirname "$HARDY_CONFIG")" 2>/dev/null || true
	elif [ -e "$HARDY_CONFIG" ]; then
		keeping "the config" "$HARDY_CONFIG"
	fi

	if wanted "$REMOVE_TOOLCHAIN" "Remove elan and the Lean toolchain at $ELAN_HOME? Other Lean projects on this machine use it too." "$ELAN_HOME"; then
		drop "elan and the Lean toolchain" "$ELAN_HOME"
	elif [ -e "$ELAN_HOME" ]; then
		keeping "elan and the Lean toolchain" "$ELAN_HOME"
	fi

	# Only when empty: --prefix may point at a directory that was not ours alone.
	if rmdir "$HARDY_HOME" 2>/dev/null; then
		say "removed $HARDY_HOME"
	fi

	printf '\n'
	if [ "$removed" = 0 ] && [ "$kept" = 0 ]; then
		say "nothing to remove: Hardy was not installed at $HARDY_HOME"
	else
		say "Hardy is uninstalled."
		[ "$kept" -gt 0 ] && say "$kept item(s) kept, listed above; --all removes them."
	fi
	say "TeX, Node, and the Claude Code CLI were left alone."
	printf 'Open a new shell so the removed PATH entry stops applying.\n'
}

hardy_uninstall_main "$@"
