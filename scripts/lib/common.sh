# shellcheck shell=bash
#
# Shared implementation behind Hardy's per-OS installers.
#
# An OS script sources this file, defines the three hooks below, and calls
# hardy_install_main "$@":
#
#   os_label                  human-readable platform name
#   os_install_prerequisites  install python (>= 3.11), git, curl
#   os_install_latex          install a TeX distribution providing pdflatex
#
# Everything platform independent lives here: the virtual environment, the
# `hardy` shim, elan (which supplies lake), the shared Mathlib project, the
# config file, and the final `hardy doctor` verification.

HARDY_HOME="${HARDY_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/hardy}"
HARDY_BIN_DIR="${HARDY_BIN_DIR:-$HOME/.local/bin}"
HARDY_CONFIG="${HARDY_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/hardy/config.toml}"
VENV="$HARDY_HOME/venv"
LEAN_PROJECT="$HARDY_HOME/lean"
LEAN_PACKAGE=hardymath
MATHLIB_TOOLCHAIN="leanprover-community/mathlib4:lean-toolchain"

# Whether the repository was chosen or merely defaulted. An install records the
# repository its release came from, and only an explicit choice now may override
# what the installation already says about itself.
HARDY_REPO_URL_CHOSEN="${HARDY_REPO_URL:+1}"
HARDY_REPO_URL="${HARDY_REPO_URL:-https://github.com/charlesmsiegel/hardy}"
# Which release to install: a tag, or empty for whatever is current.
HARDY_VERSION="${HARDY_VERSION:-}"
# Where Hardy's code comes from. `release` downloads the published wheel and
# needs no source tree at all; `source` installs the tree this script came from,
# editable, which is what a developer running it from a clone wants. Empty means
# "decide from what is actually here" — see resolve_install_source.
INSTALL_FROM="${HARDY_INSTALL_FROM:-}"

ASSUME_YES=0
SKIP_MATHLIB=0
SKIP_LATEX=0
FULL_LATEX=0
WRITE_CONFIG=1
PYTHON=""
USE_UV=0

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
say() { printf '    %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
fail() {
	printf '\033[31merror:\033[0m %s\n' "$*" >&2
	exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

confirm() {
	[ "$ASSUME_YES" = 1 ] && return 0
	[ -t 0 ] || return 0
	local reply
	read -r -p "$1 [Y/n] " reply
	case "$reply" in "" | y | Y | yes | YES) return 0 ;; *) return 1 ;; esac
}

# Run a command as root, using sudo only when we are not root already.
as_root() {
	if [ "$(id -u)" -eq 0 ]; then
		"$@"
	elif have sudo; then
		say "sudo $*"
		sudo "$@"
	else
		fail "need root to run: $*   (install sudo, or re-run this script as root)"
	fi
}

usage() {
	cat <<EOF
Usage: $0 [options]

Installs everything Hardy needs on $(os_label): Python, the Lean toolchain
(elan/lake), a Mathlib project, pdflatex, and the \`hardy\` command itself.

Options:
  --yes             non-interactive; accept every install and skip prompts
  --skip-mathlib    install lake but do not create/build the shared Mathlib project
  --skip-latex      do not install a TeX distribution
  --full-latex      install the full TeX distribution instead of a LaTeX subset
  --no-config       do not write $HARDY_CONFIG
  --from-release    install the published wheel even when run from a clone
  --from-source     install this source tree, editable (the default from a clone)
  --prefix DIR      where Hardy keeps its venv and Lean project (default $HARDY_HOME)
  --bin-dir DIR     where the \`hardy\` command is linked (default $HARDY_BIN_DIR)
  -h, --help        show this message

Environment: HARDY_MODEL, when set, is written to the config file without
prompting. Authentication is your Claude Code login, not an API key.
HARDY_VERSION selects a release tag to install instead of the current one.
EOF
}

parse_arguments() {
	while [ $# -gt 0 ]; do
		case "$1" in
		--yes | -y) ASSUME_YES=1 ;;
		--skip-mathlib) SKIP_MATHLIB=1 ;;
		--skip-latex) SKIP_LATEX=1 ;;
		--full-latex) FULL_LATEX=1 ;;
		--no-config) WRITE_CONFIG=0 ;;
		--from-release) INSTALL_FROM=release ;;
		--from-source) INSTALL_FROM=source ;;
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

check_disk_space() {
	local needed=10 available
	[ "$SKIP_MATHLIB" = 1 ] && needed=2
	mkdir -p "$HARDY_HOME"
	available=$(df -Pk "$HARDY_HOME" 2>/dev/null | awk 'NR==2 {print int($4 / 1048576)}') || return 0
	[ -n "$available" ] || return 0
	say "free space at $HARDY_HOME: ${available}G (about ${needed}G needed)"
	if [ "$available" -lt "$needed" ]; then
		warn "less than ${needed}G free; Mathlib's cache alone is several gigabytes"
		confirm "Continue anyway?" || fail "stopped: not enough free disk space"
	fi
}

# --- releases ---------------------------------------------------------------
#
# Installing Hardy means putting a released wheel into a virtual environment,
# not obtaining a copy of the repository. Everything a machine needs is reached
# through one directory of release assets, and nothing downloaded from it is
# used before its digest has been checked against the manifest.

# Which repository this installation's releases come from. Recorded at install
# time so that an install made from a fork is updated from that fork: the
# updater running later has none of the environment the installer was given, and
# without this would quietly move the installation to the official repository.
#
# Derived when asked for, not stored: --prefix moves HARDY_HOME during argument
# parsing, and a path fixed before that would point at some other installation's
# record, or at nothing.
release_origin_file() { printf '%s/release-origin' "$HARDY_HOME"; }

# The repository actually used, not the variable's default: re-running the
# retained installer on a fork's installation resolves to that fork, and writing
# HARDY_REPO_URL here would replace the record with the official repository and
# send the next update somewhere else.
# Mode 600 before anything is written, as the config file is: a private fork's
# URL can carry credentials (https://user:token@host/repo), and this file sits
# in a directory other local users can walk into.
record_release_origin() {
	local record repository
	record="$(release_origin_file)"
	# Resolved before the file is emptied: the answer may be *in* that file.
	repository="$(release_repo_url)"
	mkdir -p "$HARDY_HOME"
	: >"$record"
	chmod 600 "$record"
	printf 'repo=%s\n' "$repository" >>"$record"
}

recorded_repo_url() {
	local record
	record="$(release_origin_file)"
	[ -r "$record" ] || return 1
	sed -n 's/^repo=//p' "$record" | head -1 | grep . || return 1
}

# The repository to reach for: chosen now, else whatever this installation was
# made from, else Hardy's own.
release_repo_url() {
	if [ -n "$HARDY_REPO_URL_CHOSEN" ]; then
		printf '%s' "$HARDY_REPO_URL"
	else
		recorded_repo_url 2>/dev/null || printf '%s' "$HARDY_REPO_URL"
	fi
}

# Where the release assets live. HARDY_RELEASE_BASE_URL replaces the location
# wholesale, which is how the installer's own CI exercises this path against a
# release it built moments earlier, before one has ever been published. It is
# deliberately not recorded: it names a place for one run, where the repository
# names where this installation's code comes from for good.
release_base_url() {
	local repository
	repository="$(release_repo_url)"
	if [ -n "${HARDY_RELEASE_BASE_URL:-}" ]; then
		printf '%s' "${HARDY_RELEASE_BASE_URL%/}"
	elif [ -n "$HARDY_VERSION" ]; then
		printf '%s/releases/download/%s' "$repository" "$HARDY_VERSION"
	else
		printf '%s/releases/latest/download' "$repository"
	fi
}

# Linux ships coreutils' sha256sum and macOS ships shasum; nothing ships both
# reliably, and a machine with neither cannot verify a download at all.
file_sha256() {
	if have sha256sum; then
		sha256sum "$1" | cut -d' ' -f1
	elif have shasum; then
		shasum -a 256 "$1" | cut -d' ' -f1
	else
		return 1
	fi
}

# Find one asset in a SHA256SUMS manifest by the end of its name, printing
# "<digest> <name>". The version lives in the filename, so this is also how the
# installer learns which release it is about to install without being told.
release_asset() {
	local manifest="$1" suffix="$2" digest name
	while read -r digest name; do
		# sha256sum marks binary-mode entries with a leading asterisk.
		name="${name#\*}"
		case "$name" in
		*"$suffix")
			printf '%s %s\n' "$digest" "$name"
			return 0
			;;
		esac
	done <"$manifest"
	return 1
}

# Download one asset and refuse it unless it matches the manifest. The wheel is
# code that will run as this user, so a machine with no way to check the digest
# stops here rather than installing something it could not verify.
download_release_asset() {
	local suffix="$1" directory="$2" base entry digest name actual
	base="$(release_base_url)"
	have curl || fail "curl is required to install Hardy from a release"
	mkdir -p "$directory"
	# The manifest the bootstrap already fetched, when there is one. It names
	# the versioned assets, so reusing it keeps one install run on one release
	# even if another is published while prerequisites are being installed.
	if [ -n "${HARDY_RELEASE_MANIFEST:-}" ] && [ -r "${HARDY_RELEASE_MANIFEST}" ]; then
		cp "$HARDY_RELEASE_MANIFEST" "$directory/SHA256SUMS"
	else
		curl -fsSL "$base/SHA256SUMS" -o "$directory/SHA256SUMS" ||
			fail "could not fetch $base/SHA256SUMS — is there a published release yet? (HARDY_VERSION selects one, --from-source installs a clone instead)"
	fi
	entry="$(release_asset "$directory/SHA256SUMS" "$suffix")" ||
		fail "the release at $base has no $suffix asset"
	digest="${entry%% *}"
	name="${entry#* }"
	curl -fsSL "$base/$name" -o "$directory/$name" || fail "could not download $base/$name"
	actual="$(file_sha256 "$directory/$name")" ||
		fail "neither sha256sum nor shasum is available, so $name cannot be verified"
	[ "$actual" = "$digest" ] ||
		fail "checksum mismatch for $name: the release says $digest, the download is $actual"
	printf '%s\n' "$directory/$name"
}

# A release install keeps the installer scripts it was run from, and they are
# what updates and removes it later. They have to move with the wheel: after an
# update to release N+1, release N's uninstaller would otherwise be the one that
# runs, knowing nothing of any path N+1 introduced.
# Downloaded, verified and unpacked, but not yet in place. Staging is separate
# from committing so that an update can have both artifacts in hand before it
# changes either: a bundle that fails here leaves the old installers *and* the
# old wheel, where a failure after the wheel had moved would leave release N's
# uninstaller looking at release N+1 — the skew the bundle exists to prevent.
stage_installers() {
	local target="$HARDY_HOME/installers" staging="$HARDY_HOME/installers.new" bundle
	step "Fetching the installers for this release"
	# Last run's displaced copy, cleared now rather than at the end: this script
	# may be running out of the directory about to be replaced.
	rm -rf "$target.previous" "$staging"
	bundle="$(download_release_asset hardy-installers.tar.gz "$staging/download")"
	mkdir -p "$staging/tree"
	tar xz -C "$staging/tree" -f "$bundle" || fail "could not unpack $(basename "$bundle")"
	[ -e "$staging/tree/scripts/lib/common.sh" ] ||
		fail "$(basename "$bundle") does not carry the Hardy installers"
	say "verified $(basename "$bundle") against the release manifest"
	# One manifest for both artifacts, so an update cannot take its installers
	# from one release and its wheel from the next.
	export HARDY_RELEASE_MANIFEST="$staging/download/SHA256SUMS"
}

# Swapped whole. A half-written installers directory is worse than an old one.
commit_installers() {
	local target="$HARDY_HOME/installers" staging="$HARDY_HOME/installers.new"
	[ -d "$staging/tree" ] || return 0
	# A first install has nothing to displace.
	if [ -e "$target" ]; then mv "$target" "$target.previous"; fi
	mv "$staging/tree" "$target"
	rm -rf "$staging"
	say "the installers in $target now match the installed release"
}

# `release` unless there is a source tree here to install, which is what a
# developer running this from a clone means. An unpacked installer bundle is
# not one: it carries scripts/ and no pyproject.toml, precisely so that a
# machine with no clone lands on the release.
resolve_install_source() {
	case "$INSTALL_FROM" in
	release | source) return 0 ;;
	"") ;;
	*) fail "HARDY_INSTALL_FROM must be 'release' or 'source', not '$INSTALL_FROM'" ;;
	esac
	if [ -n "${REPO_ROOT:-}" ] && [ -e "$REPO_ROOT/pyproject.toml" ]; then
		INSTALL_FROM=source
	else
		INSTALL_FROM=release
	fi
}

# --- python -----------------------------------------------------------------

python_is_recent() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; }

find_python() {
	local candidate
	for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
		if have "$candidate" && python_is_recent "$candidate"; then
			PYTHON="$(command -v "$candidate")"
			return 0
		fi
	done
	return 1
}

# uv is the fallback when the system cannot supply Python 3.11+.
install_uv_python() {
	step "Installing a private Python 3.12 with uv"
	if ! have uv; then
		confirm "Download and run the uv installer from https://astral.sh/uv?" || fail "Python 3.11+ is required"
		curl -LsSf https://astral.sh/uv/install.sh | sh
		export PATH="$HOME/.local/bin:$PATH"
	fi
	have uv || fail "uv installed but is not on PATH"
	uv python install 3.12
	USE_UV=1
	PYTHON="uv"
}

ensure_python() {
	step "Checking for Python 3.11 or newer"
	if find_python; then
		say "using $PYTHON ($("$PYTHON" --version 2>&1))"
		return
	fi
	os_install_prerequisites
	if find_python; then
		say "using $PYTHON ($("$PYTHON" --version 2>&1))"
		return
	fi
	warn "no system Python 3.11+ found after installing prerequisites"
	install_uv_python
}

# Every path from here needs curl — elan's installer is fetched with it, and so
# is the release. A minimal image can carry Python 3.11 and no curl at all, and
# ensure_python leaves the prerequisite hook unrun when it finds a Python it
# likes, so asking for it here is what keeps the promise to install what is
# missing rather than complaining about it.
ensure_curl() {
	have curl && return 0
	step "Installing curl"
	os_install_prerequisites
	have curl || fail "curl is required to download elan and Hardy's release; install it and re-run"
}

# One installer for both kinds of environment: `uv pip` when the private Python
# came from uv, the environment's own pip otherwise.
environment_install() {
	if [ "$USE_UV" = 1 ]; then
		uv pip install --python "$VENV/bin/python" "$@"
	else
		"$VENV/bin/python" -m pip install "$@"
	fi
}

create_environment() {
	step "Installing Hardy into $VENV"
	mkdir -p "$HARDY_HOME"
	if [ "$USE_UV" = 1 ]; then
		uv venv --python 3.12 "$VENV"
	else
		"$PYTHON" -m venv --upgrade-deps "$VENV" 2>/dev/null || "$PYTHON" -m venv "$VENV" ||
			fail "could not create a virtual environment (on Debian/Ubuntu install python3-venv)"
		"$VENV/bin/python" -m pip install --upgrade pip
	fi
	case "$INSTALL_FROM" in
	release) install_released_wheel ;;
	*) install_source_tree ;;
	esac
	[ -x "$VENV/bin/hardy" ] || fail "the hardy command was not installed into $VENV"
}

install_source_tree() {
	[ -e "$REPO_ROOT/pyproject.toml" ] ||
		fail "no Hardy source tree at $REPO_ROOT to install; drop --from-source to install the published release"
	environment_install -e "$REPO_ROOT" || fail "could not install Hardy from $REPO_ROOT"
	say "installed hardy (editable, from $REPO_ROOT)"
}

# The download lands under HARDY_HOME rather than in a temporary directory: a
# wheel that failed verification is worth being able to look at, and the next
# run replaces it either way.
install_released_wheel() {
	local wheel directory="$HARDY_HOME/download"
	rm -rf "$directory"
	# The bootstrap fetches the bundle itself and hands the manifest over, and
	# that is the only case where the installers on disk are already this
	# release's. Every other route here — a checkout with --from-release, or the
	# retained installer re-run on an existing installation — has to fetch them,
	# or an installation moving to release N+1 would keep release N's updater
	# and uninstaller.
	#
	# Staged before the wheel is fetched, not after: staging leaves its manifest
	# behind for the wheel to be found in, so both artifacts come from one
	# release rather than from two resolutions of `latest` minutes apart, and
	# neither is put in place before both have been verified.
	if [ "${HARDY_RELEASE_MANIFEST:-}" != "$HARDY_HOME/installers.new/download/SHA256SUMS" ]; then
		stage_installers
	fi
	wheel="$(download_release_asset .whl "$directory")"
	say "verified $(basename "$wheel") against the release manifest"
	environment_install "$wheel" || fail "could not install $(basename "$wheel") into $VENV"
	say "installed hardy from $(basename "$wheel")"
	rm -rf "$directory"
	record_release_origin
	# Last, and only now: until the wheel is in, the installers on disk are the
	# ones that match what is installed.
	commit_installers
}

ensure_path_entry() {
	local directory="$1" file line candidate
	local -a targets=("$HOME/.profile")
	line="export PATH=\"$directory:\$PATH\"  # added by the Hardy installer"
	case ":$PATH:" in *":$directory:"*) return 0 ;; esac
	export PATH="$directory:$PATH"
	# zsh — the macOS default — never reads ~/.profile, so create its rc file
	# when zsh is the login shell. Other startup files are only appended to.
	case "${SHELL:-}" in *zsh) targets+=("$HOME/.zshrc") ;; esac
	for candidate in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.bash_profile"; do
		if [ -e "$candidate" ] && [[ ! " ${targets[*]} " == *" $candidate "* ]]; then
			targets+=("$candidate")
		fi
	done
	for file in "${targets[@]}"; do
		if ! grep -Fq "$directory" "$file" 2>/dev/null; then
			printf '\n%s\n' "$line" >>"$file"
			say "added $directory to PATH in $file"
		fi
	done
	warn "open a new shell (or 'export PATH=\"$directory:\$PATH\"') before running hardy"
	return 0
}

link_command() {
	step "Linking the hardy command into $HARDY_BIN_DIR"
	mkdir -p "$HARDY_BIN_DIR"
	ln -sf "$VENV/bin/hardy" "$HARDY_BIN_DIR/hardy"
	say "$HARDY_BIN_DIR/hardy -> $VENV/bin/hardy"
	ensure_path_entry "$HARDY_BIN_DIR"
}

# --- lean -------------------------------------------------------------------

ensure_elan() {
	step "Checking for the Lean toolchain (lake)"
	[ -d "$HOME/.elan/bin" ] && export PATH="$HOME/.elan/bin:$PATH"
	if have lake; then
		say "lake present: $(lake --version 2>&1 | head -1)"
		return
	fi
	confirm "Install elan (the Lean toolchain manager, which provides lake)?" ||
		fail "lake is required; re-run with --skip-mathlib only if you will install Lean yourself"
	have curl || fail "curl is required to install elan"
	curl -sSfL https://elan.lean-lang.org/elan-init.sh | sh -s -- -y --default-toolchain stable
	export PATH="$HOME/.elan/bin:$PATH"
	have lake || fail "elan was installed but lake is not on PATH; open a new shell and re-run"
	say "installed $(elan --version 2>&1 | head -1)"
}

lean_smoke_test() {
	local directory status=0
	directory="$(mktemp -d)"
	printf 'import Mathlib\n\nexample : 2 + 2 = 4 := by norm_num\n' >"$directory/Probe.lean"
	if ! (cd "$LEAN_PROJECT" && lake env lean "$directory/Probe.lean") >/dev/null 2>&1; then
		status=1
	fi
	rm -rf "$directory"
	return "$status"
}

ensure_lean_project() {
	if [ "$SKIP_MATHLIB" = 1 ]; then
		step "Skipping the Mathlib project (--skip-mathlib)"
		warn "run hardy from your own Lake project, or set lean_project in $HARDY_CONFIG"
		return
	fi
	step "Preparing the shared Mathlib project at $LEAN_PROJECT"
	if [ ! -e "$LEAN_PROJECT/lakefile.toml" ] && [ ! -e "$LEAN_PROJECT/lakefile.lean" ]; then
		mkdir -p "$LEAN_PROJECT"
		[ -z "$(ls -A "$LEAN_PROJECT")" ] || fail "$LEAN_PROJECT exists and is not a Lake project; move it aside"
		say "creating a Lake project pinned to Mathlib's toolchain"
		(cd "$LEAN_PROJECT" && lake "+$MATHLIB_TOOLCHAIN" init "$LEAN_PACKAGE" math) ||
			fail "lake init failed; see the output above"
	else
		say "reusing the existing project"
	fi
	if lean_smoke_test; then
		say "Mathlib already builds here; nothing to download"
		return
	fi
	say "fetching Mathlib and its prebuilt cache — several gigabytes, typically 10-30 minutes"
	(
		cd "$LEAN_PROJECT" &&
			lake update &&
			lake exe cache get &&
			lake build
	) || fail "building the Mathlib project failed; see the output above"
	lean_smoke_test || fail "the Lean project was built but 'import Mathlib' still fails in $LEAN_PROJECT"
	say "Mathlib is ready"
}

# --- latex ------------------------------------------------------------------

ensure_latex() {
	if [ "$SKIP_LATEX" = 1 ]; then
		step "Skipping LaTeX (--skip-latex)"
		return
	fi
	step "Checking for pdflatex"
	if have pdflatex; then
		say "pdflatex present: $(pdflatex --version 2>&1 | head -1)"
		return
	fi
	confirm "Install a TeX distribution providing pdflatex?" || {
		warn "continuing without pdflatex; Hardy's writeup tools will fail"
		return
	}
	# os_install_latex reads FULL_LATEX to choose between the two sizes.
	say "installing the $([ "$FULL_LATEX" = 1 ] && printf 'full' || printf 'minimal') TeX distribution"
	os_install_latex
	have pdflatex || fail "pdflatex is still not on PATH after installing TeX; open a new shell and re-run"
	say "installed $(pdflatex --version 2>&1 | head -1)"
}

ensure_claude_cli() {
	step "Checking the Claude Code CLI"
	if command -v claude >/dev/null 2>&1; then
		say "claude already installed: $(command -v claude)"
	elif command -v npm >/dev/null 2>&1; then
		say "installing @anthropic-ai/claude-code"
		npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 ||
			warn "npm could not install @anthropic-ai/claude-code; install it yourself"
	else
		# Node is not Hardy's to install, and guessing a package manager here
		# would be worse than saying plainly what is missing.
		warn "Node.js/npm not found: install Node, then 'npm install -g @anthropic-ai/claude-code'"
	fi
	command -v claude >/dev/null 2>&1 &&
		{ claude auth status 2>/dev/null | grep -q '"loggedIn": *true' || say "run 'claude login' to sign in with your subscription"; }
	true
}

# --- configuration ----------------------------------------------------------

toml_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

write_config() {
	if [ "$WRITE_CONFIG" = 0 ]; then
		step "Skipping the config file (--no-config)"
		return
	fi
	step "Writing $HARDY_CONFIG"
	local model="${HARDY_MODEL:-}"
	if [ -e "$HARDY_CONFIG" ]; then
		say "config already exists; leaving your model and key untouched"
		if [ "$SKIP_MATHLIB" = 0 ] && ! grep -q '^[[:space:]]*lean_project' "$HARDY_CONFIG"; then
			printf 'lean_project = "%s"\n' "$(toml_escape "$LEAN_PROJECT")" >>"$HARDY_CONFIG"
			say "recorded lean_project = $LEAN_PROJECT"
		fi
		return
	fi
	if [ -z "$model" ] && [ "$ASSUME_YES" = 0 ] && [ -t 0 ]; then
		printf '\nHardy talks to Claude through your Claude Code subscription.\n'
		# shellcheck disable=SC2016  # the backticks quote a command for the reader
		printf 'There is no API key to supply; sign in once with `claude login`.\n'
		read -r -p "Model identity [claude-opus-5]: " model
	fi
	mkdir -p "$(dirname "$HARDY_CONFIG")"
	# Create the file empty and lock it down before the key is written to it.
	: >"$HARDY_CONFIG"
	chmod 600 "$HARDY_CONFIG"
	{
		printf '# Written by the Hardy installer. Every value can be overridden by a\n'
		printf '# HARDY_* environment variable or a command-line flag.\n'
		# Only settings the parser accepts: anything else makes every later
		# Hardy invocation fail with "unknown settings".
		[ -n "$model" ] && printf 'model = "%s"\n' "$(toml_escape "$model")"
		[ "$SKIP_MATHLIB" = 0 ] && printf 'lean_project = "%s"\n' "$(toml_escape "$LEAN_PROJECT")"
		true
	} >>"$HARDY_CONFIG"
	CONFIGURED_MODEL="$model"
	say "wrote $HARDY_CONFIG (mode 600)"
}

# --- verification -----------------------------------------------------------

have_model() {
	[ -n "${CONFIGURED_MODEL:-}${HARDY_MODEL:-}" ] && return 0
	grep -q '^[[:space:]]*model' "$HARDY_CONFIG" 2>/dev/null
}

verify() {
	step "Verifying the installation"
	# doctor checks the whole installation, so its verdict is only binding when
	# nothing was deliberately skipped.
	local strict=1
	[ "$SKIP_LATEX" = 1 ] && strict=0
	[ "$SKIP_MATHLIB" = 1 ] && strict=0
	have_model || strict=0
	if HARDY_CONFIG="$HARDY_CONFIG" "$VENV/bin/hardy" doctor; then
		return 0
	fi
	[ "$strict" = 1 ] && fail "hardy doctor reported failures (see above)"
	have_model || warn "no model configured yet: add one to $HARDY_CONFIG or export HARDY_MODEL"
	warn "some checks did not pass; see what was skipped below"
}

summary() {
	local skipped_suffix=""
	[ "$SKIP_MATHLIB" = 1 ] && skipped_suffix=" (skipped)"
	cat <<EOF

$(printf '\033[1mHardy is installed.\033[0m')

  command      $HARDY_BIN_DIR/hardy
  environment  $VENV
  installed    $([ "$INSTALL_FROM" = source ] && printf 'editable, from %s' "$REPO_ROOT" || printf 'from the published release')
  lean project ${LEAN_PROJECT}${skipped_suffix}
  config       $HARDY_CONFIG

Start doing mathematics with an agent:

  hardy

Other useful commands:

  hardy doctor --deep     check Lean, Mathlib, LaTeX, and the model end to end
  hardy chat --workspace ./my-project
  hardy prove             stage one claim from statement to a checked document
  hardy accept --force-budget-exhaustion-test
                          check the whole pipeline with no model and no network

This installer sets up Lean, Mathlib and pdflatex. Staged \`hardy prove\` runs
also build their documents with Tectonic against a checksum-pinned bundle:

  hardy setup             find, install and record the pinned toolchain
EOF
	[ "$(command -v hardy 2>/dev/null)" = "$HARDY_BIN_DIR/hardy" ] ||
		printf '\nOpen a new shell first, so that %s is on your PATH.\n' "$HARDY_BIN_DIR"
}

hardy_install_main() {
	parse_arguments "$@"
	resolve_install_source
	step "Installing Hardy on $(os_label)"
	if [ "$INSTALL_FROM" = source ]; then
		say "source tree: $REPO_ROOT"
	else
		say "release: $(release_base_url)"
	fi
	check_disk_space
	ensure_python
	ensure_curl
	create_environment
	link_command
	ensure_elan
	ensure_lean_project
	ensure_latex
	ensure_claude_cli
	write_config
	verify
	summary
}
