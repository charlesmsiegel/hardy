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

# Fetch the release's installer bundle into $1, checked against that release's
# own manifest — these are scripts about to run as this user. Returns 1 when the
# release could not be reached, and 2 when what it served does not match its
# manifest, which is never a reason to go and install something else.
hardy_fetch_installers() {
	target="$1"
	staging="$target.new"
	command -v curl >/dev/null 2>&1 || return 1
	# Staged beside the retained copy, never over it. Re-running the installer
	# on an existing installation must not leave it without its updater and
	# uninstaller because a download failed halfway.
	rm -rf "$staging"
	mkdir -p "$staging"
	curl -fsSL "$release_base/SHA256SUMS" -o "$staging/SHA256SUMS" 2>/dev/null || return 1
	curl -fsSL "$release_base/hardy-installers.tar.gz" -o "$staging/bundle.tar.gz" 2>/dev/null || return 1
	expected="$(sed -n 's/^\([0-9a-fA-F]\{64\}\)[ *]*hardy-installers\.tar\.gz$/\1/p' "$staging/SHA256SUMS")"
	if command -v sha256sum >/dev/null 2>&1; then
		actual="$(sha256sum "$staging/bundle.tar.gz" | cut -d' ' -f1)"
	elif command -v shasum >/dev/null 2>&1; then
		actual="$(shasum -a 256 "$staging/bundle.tar.gz" | cut -d' ' -f1)"
	else
		printf 'error: neither sha256sum nor shasum is here, so the installers cannot be verified\n' >&2
		return 2
	fi
	if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
		printf 'error: the installer bundle at %s does not match that release manifest\n' "$release_base" >&2
		return 2
	fi
	tar xz -C "$staging" -f "$staging/bundle.tar.gz" 2>/dev/null || return 1
	rm -f "$staging/bundle.tar.gz"
	[ -e "$staging/scripts/install-linux.sh" ] || return 1
	rm -rf "$target"
	mv "$staging" "$target"
	# Kept, and handed to the installer that runs next. It names the versioned
	# assets, so the wheel comes from the release these scripts came from even
	# if another is published while prerequisites are being installed.
	export HARDY_RELEASE_MANIFEST="$target/SHA256SUMS"
}

if [ ! -e "$directory/install-linux.sh" ]; then
	HARDY_REPO_URL="${HARDY_REPO_URL:-https://github.com/charlesmsiegel/hardy}"
	# --prefix is parsed by the shared argument parser, long after this; but the
	# installers are kept beside the installation they manage, so the bootstrap
	# has to honour it too or an update would later look for them under the
	# default prefix and find nothing.
	hardy_home="${HARDY_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/hardy}"
	take_prefix=0
	for argument in "$@"; do
		if [ "$take_prefix" = 1 ]; then
			hardy_home="$argument"
			take_prefix=0
			continue
		fi
		if [ "$argument" = "--prefix" ]; then take_prefix=1; fi
	done
	export HARDY_HOME="$hardy_home"
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

	# The published release first: its installers and its wheel are one version,
	# where main's installers and a released wheel need not be. Fetched afresh
	# every time — the bundle is a few kilobytes, and a kept copy would be last
	# release's installers reaching for this release's wheel.
	installers="$hardy_home/installers"
	source_root=""
	status=0
	if [ -n "${HARDY_REPO_REF:-}" ]; then
		# A ref names a branch or a tag, which only the repository serves. It
		# also outranks a named release: someone who asked for a ref is asking
		# for the repository, whatever else is set.
		status=1
		named_a_release=0
	else
		printf '==> Fetching the Hardy installers from %s\n' "$release_base"
		if hardy_fetch_installers "$installers"; then source_root="$installers"; else status=$?; fi
	fi
	if [ "$status" != 0 ]; then
		# Only the staging directory. A retained copy from an earlier install is
		# this installation's updater and uninstaller, and a failed download is
		# no reason to take those away — but it is not used either, since last
		# release's installers reaching for this release's wheel is the skew the
		# bundle exists to prevent.
		rm -rf "$installers.new"
		if [ "$status" = 2 ] || [ "$named_a_release" = 1 ]; then
			printf 'error: could not install the release at %s, and will not install something else in its place.\nUnset HARDY_VERSION to take whatever the repository has instead.\n' "$release_base" >&2
			exit 1
		fi
	fi

	# The repository: before the first release exists, and whenever a ref was
	# named. Always re-fetched, so that changing HARDY_REPO_URL or HARDY_REPO_REF
	# cannot silently reinstall the previous one.
	if [ -z "$source_root" ]; then
		HARDY_REPO_REF="${HARDY_REPO_REF:-main}"
		source_root="$hardy_home/src"
		printf '==> Fetching Hardy into %s (ref %s)\n' "$source_root" "$HARDY_REPO_REF"
		# Into a sibling, never over the tree in place. An editable installation
		# points at this directory, so removing it before a fetch that then
		# failed would break the installed `hardy` outright.
		staging="$source_root.new"
		rm -rf "$staging"
		mkdir -p "$staging"
		fetched=0
		if command -v curl >/dev/null 2>&1; then
			# A ref may be a branch or a tag, GitHub keeps the two in separate
			# namespaces, and a clean machine has no git to ask which this is.
			for namespace in heads tags; do
				curl -fsSL "$HARDY_REPO_URL/archive/refs/$namespace/$HARDY_REPO_REF.tar.gz" 2>/dev/null |
					tar xz -C "$staging" --strip-components=1 2>/dev/null && fetched=1
				if [ "$fetched" = 1 ]; then break; fi
			done
		fi
		if [ "$fetched" = 0 ] && command -v git >/dev/null 2>&1; then
			rm -rf "$staging"
			mkdir -p "$staging"
			git clone --depth 1 --branch "$HARDY_REPO_REF" "$HARDY_REPO_URL" "$staging" && fetched=1
		fi
		if [ "$fetched" = 1 ] && [ -e "$staging/scripts/install-linux.sh" ]; then
			rm -rf "$source_root"
			mv "$staging" "$source_root"
		else
			rm -rf "$staging"
			printf 'error: could not fetch the Hardy installers from %s, nor the repository at %s (ref %s).\nCheck your network, or clone the repository yourself and run scripts/%s from the clone.\n' "$release_base" "$HARDY_REPO_URL" "$HARDY_REPO_REF" install.sh >&2
			exit 1
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
