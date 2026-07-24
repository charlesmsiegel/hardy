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
  --prefix DIR      where Hardy keeps its venv and Lean project (default $HARDY_HOME)
  --bin-dir DIR     where the \`hardy\` command is linked (default $HARDY_BIN_DIR)
  -h, --help        show this message

Environment: HARDY_MODEL, OPENAI_API_KEY, and ANTHROPIC_API_KEY, when set, are
written to the config file without prompting.
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

create_environment() {
	step "Installing Hardy into $VENV"
	mkdir -p "$HARDY_HOME"
	if [ "$USE_UV" = 1 ]; then
		uv venv --python 3.12 "$VENV"
		uv pip install --python "$VENV/bin/python" -e "$REPO_ROOT"
	else
		"$PYTHON" -m venv --upgrade-deps "$VENV" 2>/dev/null || "$PYTHON" -m venv "$VENV" ||
			fail "could not create a virtual environment (on Debian/Ubuntu install python3-venv)"
		"$VENV/bin/python" -m pip install --upgrade pip
		"$VENV/bin/python" -m pip install -e "$REPO_ROOT"
	fi
	[ -x "$VENV/bin/hardy" ] || fail "the hardy command was not installed into $VENV"
	say "installed hardy (editable, from $REPO_ROOT)"
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

# --- configuration ----------------------------------------------------------

toml_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

write_config() {
	if [ "$WRITE_CONFIG" = 0 ]; then
		step "Skipping the config file (--no-config)"
		return
	fi
	step "Writing $HARDY_CONFIG"
	local model="${HARDY_MODEL:-}" key="${OPENAI_API_KEY:-}" base_url="${HARDY_BASE_URL:-}"
	local anthropic_key="${ANTHROPIC_API_KEY:-}"
	if [ -e "$HARDY_CONFIG" ]; then
		say "config already exists; leaving your model and key untouched"
		if [ "$SKIP_MATHLIB" = 0 ] && ! grep -q '^[[:space:]]*lean_project' "$HARDY_CONFIG"; then
			printf 'lean_project = "%s"\n' "$(toml_escape "$LEAN_PROJECT")" >>"$HARDY_CONFIG"
			say "recorded lean_project = $LEAN_PROJECT"
		fi
		return
	fi
	if [ -z "$model" ] && [ "$ASSUME_YES" = 0 ] && [ -t 0 ]; then
		printf '\nHardy talks to Claude through the Anthropic Messages API, and to any\n'
		printf 'OpenAI-compatible endpoint with native tool calling. The backend follows\n'
		printf 'the model identity, and /model switches between them later.\n'
		read -r -p "Model identity (e.g. claude-opus-5 or gpt-5.1; blank to skip): " model
		case "$model" in
		claude-*)
			read -r -s -p "Anthropic API key (blank to read \$ANTHROPIC_API_KEY at run time): " anthropic_key
			printf '\n'
			;;
		?*)
			read -r -p "API base URL [https://api.openai.com/v1]: " base_url
			read -r -s -p "API key (blank to read \$OPENAI_API_KEY at run time): " key
			printf '\n'
			;;
		esac
	fi
	mkdir -p "$(dirname "$HARDY_CONFIG")"
	# Create the file empty and lock it down before the key is written to it.
	: >"$HARDY_CONFIG"
	chmod 600 "$HARDY_CONFIG"
	{
		printf '# Written by the Hardy installer. Every value can be overridden by a\n'
		printf '# HARDY_* environment variable or a command-line flag.\n'
		[ -n "$model" ] && printf 'model = "%s"\n' "$(toml_escape "$model")"
		[ -n "$base_url" ] && printf 'base_url = "%s"\n' "$(toml_escape "$base_url")"
		[ -n "$key" ] && printf 'api_key = "%s"\n' "$(toml_escape "$key")"
		[ -n "$anthropic_key" ] && printf 'anthropic_api_key = "%s"\n' "$(toml_escape "$anthropic_key")"
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
	cat <<EOF

$(printf '\033[1mHardy is installed.\033[0m')

  command      $HARDY_BIN_DIR/hardy
  environment  $VENV
  lean project ${LEAN_PROJECT}$([ "$SKIP_MATHLIB" = 1 ] && printf ' (skipped)' || true)
  config       $HARDY_CONFIG

Start doing mathematics with an agent:

  hardy

Other useful commands:

  hardy doctor --deep     check Lean, Mathlib, LaTeX, and the model end to end
  hardy chat --workspace ./my-project
EOF
	[ "$(command -v hardy 2>/dev/null)" = "$HARDY_BIN_DIR/hardy" ] ||
		printf '\nOpen a new shell first, so that %s is on your PATH.\n' "$HARDY_BIN_DIR"
}

hardy_install_main() {
	parse_arguments "$@"
	step "Installing Hardy on $(os_label)"
	say "repository: $REPO_ROOT"
	check_disk_space
	ensure_python
	create_environment
	link_command
	ensure_elan
	ensure_lean_project
	ensure_latex
	write_config
	verify
	summary
}
