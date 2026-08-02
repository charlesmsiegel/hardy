#!/usr/bin/env sh
#
# Detects the operating system and runs the matching Hardy installer. It holds
# no installation logic of its own: scripts/install-linux.sh,
# scripts/install-macos.sh, and scripts/install-windows.ps1 are the real ones,
# and each is safe to run directly.
#
#   scripts/install.sh --yes                       # from a clone
#   curl -fsSL .../scripts/install.sh | sh         # without one
#
# Run on its own — downloaded, or piped from curl — it first fetches the rest of
# the installer from the published release into ~/.local/share/hardy/installers.
# Installing Hardy needs no clone: the installers come from the release, and so
# does the wheel they put in the virtual environment.
set -eu

directory=""
script_directory="$(dirname "$0")"
if [ -d "$script_directory" ]; then directory="$(cd "$script_directory" && pwd)"; fi

if [ ! -e "$directory/install-linux.sh" ]; then
	HARDY_REPO_URL="${HARDY_REPO_URL:-https://github.com/charlesmsiegel/hardy}"
	hardy_home="${HARDY_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/hardy}"

	# The published release first: its installers and its wheel are one version,
	# where main's installers and a released wheel need not be. Fetched afresh
	# every time — the bundle is a few kilobytes, and a kept copy would be last
	# release's installers reaching for this release's wheel.
	source_root="$hardy_home/installers"
	if [ -z "${HARDY_REPO_REF:-}" ] && command -v curl >/dev/null 2>&1; then
		if [ -n "${HARDY_RELEASE_BASE_URL:-}" ]; then
			release_base="${HARDY_RELEASE_BASE_URL%/}"
		elif [ -n "${HARDY_VERSION:-}" ]; then
			release_base="$HARDY_REPO_URL/releases/download/$HARDY_VERSION"
		else
			release_base="$HARDY_REPO_URL/releases/latest/download"
		fi
		printf '==> Fetching the Hardy installers from %s\n' "$release_base"
		rm -rf "$source_root"
		mkdir -p "$source_root"
		curl -fsSL "$release_base/hardy-installers.tar.gz" 2>/dev/null |
			tar xz -C "$source_root" 2>/dev/null ||
			rm -rf "$source_root"
	fi

	# The repository, when there is no release to fetch from.
	if [ ! -e "$source_root/scripts/install-linux.sh" ]; then
		HARDY_REPO_REF="${HARDY_REPO_REF:-main}"
		source_root="$hardy_home/src"
		if [ ! -e "$source_root/scripts/install-linux.sh" ]; then
			printf '==> Fetching Hardy into %s\n' "$source_root"
			rm -rf "$source_root"
			mkdir -p "$source_root"
			fetched=0
			if command -v curl >/dev/null 2>&1; then
				curl -fsSL "$HARDY_REPO_URL/archive/refs/heads/$HARDY_REPO_REF.tar.gz" 2>/dev/null |
					tar xz -C "$source_root" --strip-components=1 2>/dev/null && fetched=1
			fi
			if [ "$fetched" = 0 ] && command -v git >/dev/null 2>&1; then
				rm -rf "$source_root"
				mkdir -p "$source_root"
				git clone --depth 1 --branch "$HARDY_REPO_REF" "$HARDY_REPO_URL" "$source_root" && fetched=1
			fi
			[ "$fetched" = 1 ] || {
				printf 'error: could not fetch the Hardy installers from a release, nor the repository at %s (ref %s).\nCheck your network, or clone the repository yourself and run scripts/%s from the clone.\n' "$HARDY_REPO_URL" "$HARDY_REPO_REF" install.sh >&2
				exit 1
			}
		fi
	fi

	directory="$source_root/scripts"
	[ -e "$directory/install-linux.sh" ] ||
		{ printf 'error: %s does not carry the Hardy installers\n' "$source_root" >&2; exit 1; }
fi

case "$(uname -s)" in
Linux* | Darwin*)
	# Call bash explicitly: a downloaded copy may have lost its executable bit.
	command -v bash >/dev/null 2>&1 ||
		{ printf 'error: bash is required (on Alpine: apk add bash), then re-run this script\n' >&2; exit 1; }
	case "$(uname -s)" in
	Linux*) exec bash "$directory/install-linux.sh" "$@" ;;
	*) exec bash "$directory/install-macos.sh" "$@" ;;
	esac
	;;
MINGW* | MSYS* | CYGWIN* | Windows_NT)
	cat >&2 <<EOF
error: this looks like Windows.

Hardy's Windows installer is PowerShell, not shell. Run it from PowerShell:

  powershell -ExecutionPolicy Bypass -File $directory\\install-windows.ps1

WSL is not required. If you are deliberately installing inside a WSL
distribution, run $directory/install-linux.sh instead.
EOF
	exit 2
	;;
*)
	printf 'error: unsupported operating system: %s\n' "$(uname -s)" >&2
	printf 'Supported: Linux, macOS, and Windows (scripts/install-windows.ps1).\n' >&2
	exit 2
	;;
esac
