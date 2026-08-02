#!/usr/bin/env bash
#
# One-shot Hardy install for Linux. Takes a clean machine to a working `hardy`
# command: Python, the Lean toolchain (elan/lake), a Mathlib project, pdflatex,
# and Hardy itself. Run it as an ordinary user; it uses sudo only for the
# distribution packages it has to install.
#
#   scripts/install-linux.sh              # interactive
#   scripts/install-linux.sh --yes        # unattended
#
# See scripts/lib/common.sh for the platform-independent steps and --help for
# the full option list.
set -euo pipefail

SCRIPT_NAME=install-linux.sh
REPO_ROOT=""
script_directory="$(dirname "${BASH_SOURCE[0]:-$0}")"
if [ -d "$script_directory" ]; then REPO_ROOT="$(cd "$script_directory/.." && pwd)"; fi

# A copy of this script downloaded on its own (or piped from curl) has no
# scripts/lib/common.sh beside it, so it fetches the rest of the installer and
# re-execs from there. The published release comes first: its installers and its
# wheel are one version, where main's installers and a released wheel need not
# be. Naming HARDY_REPO_REF asks for the repository instead, and the repository
# is also the fallback before the first release exists.

# Fetch the release's installer bundle into $1, checked against that release's
# own manifest — these are scripts about to run as this user. Returns 1 when the
# release could not be reached, and 2 when what it served does not match its
# manifest, which is never a reason to go and install something else.
hardy_fetch_installers() {
	staging="$1/installers.new"
	command -v curl >/dev/null 2>&1 || return 1
	# Staged, and left staged. The retained installers of an existing
	# installation are not touched until the wheel they came with is installed:
	# committing them first would leave release N's wheel under release N+1's
	# updater if anything after this failed. lib/common.sh commits them.
	rm -rf "$staging"
	mkdir -p "$staging/download" "$staging/tree"
	curl -fsSL "$release_base/SHA256SUMS" -o "$staging/download/SHA256SUMS" 2>/dev/null || return 1
	curl -fsSL "$release_base/hardy-installers.tar.gz" -o "$staging/download/bundle.tar.gz" 2>/dev/null || return 1
	expected="$(sed -n 's/^\([0-9a-fA-F]\{64\}\)[ *]*hardy-installers\.tar\.gz$/\1/p' "$staging/download/SHA256SUMS")"
	if command -v sha256sum >/dev/null 2>&1; then
		actual="$(sha256sum "$staging/download/bundle.tar.gz" | cut -d' ' -f1)"
	elif command -v shasum >/dev/null 2>&1; then
		actual="$(shasum -a 256 "$staging/download/bundle.tar.gz" | cut -d' ' -f1)"
	else
		printf 'error: neither sha256sum nor shasum is here, so the installers cannot be verified\n' >&2
		return 2
	fi
	if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
		printf 'error: the installer bundle at %s does not match that release manifest\n' "$release_shown" >&2
		return 2
	fi
	tar xz -C "$staging/tree" -f "$staging/download/bundle.tar.gz" 2>/dev/null || return 1
	rm -f "$staging/download/bundle.tar.gz"
	[ -e "$staging/tree/scripts/lib/common.sh" ] || return 1
	# Handed to the installer that runs next. It names the versioned assets, so
	# the wheel comes from the release these scripts came from even if another
	# is published while prerequisites are being installed.
	export HARDY_RELEASE_MANIFEST="$staging/download/SHA256SUMS"
}

if [ ! -e "$REPO_ROOT/scripts/lib/common.sh" ]; then
	# Whether the repository was chosen, before the default hides the answer.
	repo_chosen="${HARDY_REPO_URL:+1}"
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
	# An existing installation says which repository it came from, and this
	# bootstrap has to agree with the installer it is about to hand over to:
	# staging the official bundle and then letting that installer ask a fork
	# for the wheel names in it is a manifest that does not describe the
	# release being installed.
	if [ -z "$repo_chosen" ] && [ -r "$hardy_home/release-origin" ]; then
		recorded_repo="$(sed -n 's/^repo=//p' "$hardy_home/release-origin" | head -1)"
		if [ -n "$recorded_repo" ]; then HARDY_REPO_URL="$recorded_repo"; fi
	fi
	HARDY_REPO_URL="${HARDY_REPO_URL:-https://github.com/charlesmsiegel/hardy}"
	if [ -n "${HARDY_RELEASE_BASE_URL:-}" ]; then
		release_base="${HARDY_RELEASE_BASE_URL%/}"
	elif [ -n "${HARDY_VERSION:-}" ]; then
		release_base="$HARDY_REPO_URL/releases/download/$HARDY_VERSION"
	else
		release_base="$HARDY_REPO_URL/releases/latest/download"
	fi
	# A private fork's URL can carry credentials; what goes on screen and into a
	# CI log is the URL without them.
	release_shown="$(printf '%s' "$release_base" | sed -e 's#://[^/@]*@#://***@#')"
	repo_shown="$(printf '%s' "$HARDY_REPO_URL" | sed -e 's#://[^/@]*@#://***@#')"
	# Naming a release means that release. Quietly installing a branch in its
	# place would put code nobody asked for on the machine, under a version
	# number saying otherwise.
	named_a_release=0
	if [ -n "${HARDY_RELEASE_BASE_URL:-}" ] || [ -n "${HARDY_VERSION:-}" ]; then named_a_release=1; fi

	# Fetched afresh every time, unlike a clone: the bundle is a few kilobytes,
	# and a kept copy would be last release's installers reaching for this
	# release's wheel — the mismatch it exists to prevent.
	installers="$hardy_home/installers.new"
	REPO_ROOT=""
	status=0
	if [ -n "${HARDY_REPO_REF:-}" ]; then
		# A ref names a branch or a tag, which only the repository serves. It
		# also outranks a named release: someone who asked for a ref is asking
		# for the repository, whatever else is set.
		status=1
		named_a_release=0
	else
		printf '==> Fetching the Hardy installers from %s\n' "$release_shown"
		if hardy_fetch_installers "$hardy_home"; then REPO_ROOT="$installers/tree"; else status=$?; fi
	fi
	if [ "$status" != 0 ]; then
		# Only the staging directory. A retained copy from an earlier install is
		# this installation's updater and uninstaller, and a failed download is
		# no reason to take those away — but it is not used either, since last
		# release's installers reaching for this release's wheel is the skew the
		# bundle exists to prevent.
		rm -rf "$installers"
		if [ "$status" = 2 ] || [ "$named_a_release" = 1 ]; then
			printf 'error: could not install the release at %s, and will not install something else in its place.\nUnset HARDY_VERSION to take whatever the repository has instead.\n' "$release_shown" >&2
			exit 1
		fi
	fi

	# The repository: before the first release exists, and whenever a ref was
	# named. Always re-fetched, so that changing HARDY_REPO_URL or
	# HARDY_REPO_REF cannot silently reinstall the previous one.
	if [ -z "$REPO_ROOT" ]; then
		HARDY_REPO_REF="${HARDY_REPO_REF:-main}"
		REPO_ROOT="$hardy_home/src"
		printf '==> Fetching Hardy into %s (ref %s)\n' "$REPO_ROOT" "$HARDY_REPO_REF"
		# Into a sibling, never over the tree in place. An editable installation
		# points at this directory, so removing it before a fetch that then
		# failed would break the installed `hardy` outright.
		staging="$REPO_ROOT.new"
		rm -rf "$staging"
		fetched=0
		# git first when it is here: a clone records the ref and the commit it
		# resolved to, so what was installed can still be said afterwards. An
		# unpacked archive keeps neither, and is the fallback for the clean
		# machine that has no git yet.
		if command -v git >/dev/null 2>&1; then
			git clone --depth 1 --branch "$HARDY_REPO_REF" "$HARDY_REPO_URL" "$staging" && fetched=1
		fi
		if [ "$fetched" = 0 ] && command -v curl >/dev/null 2>&1; then
			rm -rf "$staging"
			mkdir -p "$staging"
			# A ref may be a branch or a tag, GitHub keeps the two in separate
			# namespaces, and a machine with no git cannot ask which this is.
			for namespace in heads tags; do
				curl -fsSL "$HARDY_REPO_URL/archive/refs/$namespace/$HARDY_REPO_REF.tar.gz" 2>/dev/null |
					tar xz -C "$staging" --strip-components=1 2>/dev/null && fetched=1
				if [ "$fetched" = 1 ]; then break; fi
			done
		fi
		if [ "$fetched" = 1 ] && [ -e "$staging/scripts/lib/common.sh" ]; then
			rm -rf "$REPO_ROOT"
			mv "$staging" "$REPO_ROOT"
		else
			rm -rf "$staging"
			printf 'error: could not fetch the Hardy installers from %s, nor the repository at %s (ref %s).\nCheck your network, or clone the repository yourself and run scripts/%s from the clone.\n' "$release_shown" "$repo_shown" "$HARDY_REPO_REF" "$SCRIPT_NAME" >&2
			exit 1
		fi
	fi

	[ -e "$REPO_ROOT/scripts/lib/common.sh" ] ||
		{ printf 'error: %s does not carry the Hardy installers\n' "$REPO_ROOT" >&2; exit 1; }
	exec bash "$REPO_ROOT/scripts/$SCRIPT_NAME" "$@"
fi
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
. "$REPO_ROOT/scripts/lib/common.sh"

PACKAGE_MANAGER=""

detect_package_manager() {
	local candidate
	for candidate in apt-get dnf yum pacman zypper apk; do
		if have "$candidate"; then
			PACKAGE_MANAGER="$candidate"
			return 0
		fi
	done
	return 1
}

os_label() {
	local name=""
	[ -r /etc/os-release ] && name="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-$NAME}")"
	printf 'Linux%s' "${name:+ ($name)}"
}

install_packages() {
	detect_package_manager || fail "no supported package manager found (apt-get, dnf, yum, pacman, zypper, apk)"
	say "installing with $PACKAGE_MANAGER: $*"
	case "$PACKAGE_MANAGER" in
	apt-get)
		as_root env DEBIAN_FRONTEND=noninteractive apt-get update
		as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
		;;
	dnf | yum) as_root "$PACKAGE_MANAGER" install -y "$@" ;;
	pacman)
		as_root pacman -Sy --noconfirm --needed "$@"
		;;
	zypper) as_root zypper --non-interactive install "$@" ;;
	apk) as_root apk add --no-cache "$@" ;;
	esac
}

os_install_prerequisites() {
	step "Installing base prerequisites"
	detect_package_manager || fail "no supported package manager found; install Python 3.11+, git, and curl yourself, then re-run"
	confirm "Install Python, git, and curl with $PACKAGE_MANAGER?" || fail "prerequisites are required"
	case "$PACKAGE_MANAGER" in
	apt-get) install_packages python3 python3-venv python3-pip git curl ca-certificates ;;
	dnf | yum) install_packages python3 python3-pip git curl ca-certificates ;;
	pacman) install_packages python python-pip git curl ca-certificates ;;
	zypper) install_packages python3 python3-pip git curl ca-certificates ;;
	apk) install_packages python3 py3-pip git curl ca-certificates bash ;;
	esac
}

# A LaTeX subset large enough for Hardy's writeups (amsmath, amsthm, amssymb,
# geometry, hyperref). --full-latex installs the distribution's everything meta
# package instead, which is several gigabytes.
os_install_latex() {
	step "Installing LaTeX"
	detect_package_manager || fail "no supported package manager found; install a TeX distribution yourself"
	case "$PACKAGE_MANAGER" in
	apt-get)
		if [ "$FULL_LATEX" = 1 ]; then
			install_packages texlive-full
		else
			install_packages texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended texlive-science
		fi
		;;
	dnf | yum)
		if [ "$FULL_LATEX" = 1 ]; then
			install_packages texlive-scheme-full
		else
			install_packages texlive-scheme-basic texlive-collection-latexrecommended texlive-collection-mathscience
		fi
		;;
	pacman)
		if [ "$FULL_LATEX" = 1 ]; then
			install_packages texlive-meta
		else
			install_packages texlive-basic texlive-latex texlive-latexrecommended texlive-latexextra texlive-fontsrecommended texlive-mathscience
		fi
		;;
	zypper)
		if [ "$FULL_LATEX" = 1 ]; then
			install_packages texlive-scheme-full
		else
			install_packages texlive-latex texlive-collection-latexrecommended texlive-amsmath texlive-amsfonts
		fi
		;;
	apk)
		if [ "$FULL_LATEX" = 1 ]; then
			install_packages texlive-full
		else
			install_packages texlive texmf-dist-latexextra texmf-dist-fontsrecommended
		fi
		;;
	esac
}

hardy_install_main "$@"
