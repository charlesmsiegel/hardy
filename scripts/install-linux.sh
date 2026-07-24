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

# Installing Hardy means installing this source tree, so a copy of the script
# downloaded on its own (or piped from curl) fetches the repository and re-execs
# from there. HARDY_REPO_URL and HARDY_REPO_REF override what is fetched.
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
