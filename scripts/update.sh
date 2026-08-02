#!/usr/bin/env bash
#
# Updates an existing Hardy installation on Linux or macOS.
#
#   scripts/update.sh                    # code and dependencies
#   scripts/update.sh --mathlib          # and refresh Mathlib
#   scripts/update.sh --toolchain        # and update elan's Lean toolchains
#
# There are two kinds of installation and this updates either. An install made
# from the published release has no source tree: it fetches the newest released
# wheel and puts it in the virtual environment. An install made from a clone is
# editable, so new *code* is live the moment the tree moves; new *dependencies*
# are not, which is the whole reason that path exists — a release that adds one
# is otherwise a working checkout and a broken command.
#
# Mathlib is left alone unless asked for. It is a multi-gigabyte rebuild and
# almost never what someone updating Hardy itself is after.
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Reached from Git Bash, this would look for a POSIX install that is not there.
# HARDY_SKIP_PLATFORM_CHECK=1 is for the test suite, and for anyone who
# genuinely means to drive this script from such a shell.
case "${HARDY_SKIP_PLATFORM_CHECK:-0}:$(uname -s)" in
0:MINGW* | 0:MSYS* | 0:CYGWIN* | 0:Windows_NT)
	cat >&2 <<EOF
error: this looks like Windows.

Hardy's Windows updater is PowerShell, not shell. Run it from PowerShell:

  powershell -ExecutionPolicy Bypass -File $script_directory\\update-windows.ps1

If you are deliberately updating an install inside a WSL distribution, run this
script from within that distribution instead.
EOF
	exit 2
	;;
esac

# shellcheck source=lib/common.sh
. "$script_directory/lib/common.sh"

UPDATE_MATHLIB=0
UPDATE_TOOLCHAIN=0
SOURCE_TREE=""
UPDATE_FROM=""

usage() {
	cat <<EOF
Usage: $0 [options]

Updates Hardy in place and runs \`hardy doctor\`. An install made from the
published release moves to the newest release; one made from a clone has its
tree pulled and reinstalled, so that a newly declared dependency is picked up.

Options:
  --yes, -y        non-interactive; accept every step
  --mathlib        also run lake update, cache get, and build in $LEAN_PROJECT
  --toolchain      also run elan self update and elan update
  --source DIR     the Hardy source tree to pull (default: wherever the
                   installed environment says its code lives; an install from
                   a release has none, and is updated from the release)
  --prefix DIR     where Hardy is installed (default $HARDY_HOME)
  -h, --help       show this message

Environment: HARDY_VERSION selects a release tag to move to instead of the
current one.
EOF
}

parse_update_arguments() {
	while [ $# -gt 0 ]; do
		case "$1" in
		--yes | -y) ASSUME_YES=1 ;;
		--mathlib) UPDATE_MATHLIB=1 ;;
		--toolchain) UPDATE_TOOLCHAIN=1 ;;
		--source)
			[ $# -ge 2 ] || fail "--source needs a directory"
			SOURCE_TREE="$2"
			shift
			;;
		--prefix)
			[ $# -ge 2 ] || fail "--prefix needs a directory"
			HARDY_HOME="$2"
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

# Ask the installed environment what kind of installation it is, rather than
# keeping a record that could go stale. Read from the distribution's own
# metadata and not by importing Hardy: a checkout that cannot be imported right
# now — a syntax error mid-edit, a dependency not yet installed — is still an
# editable install, and answering "release" for it would replace the developer's
# checkout with a published wheel.
#
# Prints the tree for an editable install; exits 3 for a wheel install, and 1
# when there is no Hardy in the environment to ask.
discover_source_tree() {
	"$VENV/bin/python" - <<'PY'
import json, pathlib
from importlib import metadata
from urllib.parse import urlparse
from urllib.request import url2pathname

try:
    distribution = metadata.distribution("hardy-prover")
except metadata.PackageNotFoundError:
    raise SystemExit(1)

# pip records this for anything installed from a local path or a URL; a wheel
# install from a file has one too, so `editable` is what distinguishes them.
recorded = distribution.read_text("direct_url.json")
if recorded:
    direct = json.loads(recorded)
    parsed = urlparse(direct.get("url", ""))
    if direct.get("dir_info", {}).get("editable") and parsed.scheme == "file":
        # url2pathname, not a slice: the path is percent-encoded, and on
        # Windows it carries a leading slash before the drive letter.
        tree = pathlib.Path(url2pathname(parsed.path))
        if (tree / "pyproject.toml").is_file():
            print(tree)
            raise SystemExit(0)
raise SystemExit(3)
PY
}

# Which of the two installations this is. An editable install resolves back to
# a source tree; a release install does not, and that absence is the answer
# rather than a fault to report.
resolve_update_source() {
	if [ -n "$SOURCE_TREE" ]; then
		[ -f "$SOURCE_TREE/pyproject.toml" ] || fail "$SOURCE_TREE does not look like the Hardy repository"
		UPDATE_FROM=source
		return 0
	fi
	[ -x "$VENV/bin/python" ] ||
		fail "no Hardy installation at $HARDY_HOME; run scripts/install.sh first, or pass --source"
	local status=0
	SOURCE_TREE="$(discover_source_tree)" || status=$?
	case "$status" in
	0) UPDATE_FROM=source ;;
	3) UPDATE_FROM=release ;;
	*) fail "the environment at $VENV has no Hardy in it to update; re-run scripts/install.sh, or pass --source DIR" ;;
	esac
}

update_source() {
	step "Updating the source tree at $SOURCE_TREE"
	if [ ! -d "$SOURCE_TREE/.git" ]; then
		# A curl install unpacks a tarball, which has no history to pull.
		warn "$SOURCE_TREE is not a git checkout; leaving the code as it is"
		say "re-run scripts/install.sh to fetch a newer copy"
		return 0
	fi
	have git || fail "git is required to update a checkout"
	local before after
	before="$(git -C "$SOURCE_TREE" rev-parse HEAD 2>/dev/null || printf 'unknown')"
	if ! git -C "$SOURCE_TREE" diff --quiet 2>/dev/null; then
		warn "$SOURCE_TREE has uncommitted changes"
		confirm "Pull anyway? Your changes are left in place and may conflict." ||
			fail "stopped: commit or stash your changes, then re-run"
	fi
	git -C "$SOURCE_TREE" pull --ff-only || fail "git pull failed in $SOURCE_TREE"
	after="$(git -C "$SOURCE_TREE" rev-parse HEAD 2>/dev/null || printf 'unknown')"
	if [ "$before" = "$after" ]; then
		say "already up to date ($before)"
	else
		say "$before -> $after"
	fi
}

# The environment may have been created by uv, which leaves no pip inside it,
# so prefer uv wherever it is on the machine — as the installer did.
install_into_environment() {
	if have uv; then
		uv pip install --python "$VENV/bin/python" "$@"
	else
		"$VENV/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
		"$VENV/bin/python" -m pip install "$@"
	fi
}

# The step that matters for an editable install. The code is already current;
# this is what turns a newly declared dependency into an installed one.
update_environment() {
	step "Reinstalling dependencies into $VENV"
	install_into_environment -e "$SOURCE_TREE" || fail "could not reinstall Hardy into $VENV"
	say "dependencies are current"
}

# There is no source tree to pull here: the wheel named by the release manifest
# is both the new code and the new dependency list.
update_from_release() {
	local wheel directory="$HARDY_HOME/download"
	step "Updating Hardy from $(release_base_url)"
	rm -rf "$directory"
	# Both artifacts in hand, verified, before either is put in place. A bundle
	# that fails to download after the wheel had already moved would leave
	# release N's updater and uninstaller minding release N+1.
	stage_installers
	wheel="$(download_release_asset .whl "$directory")"
	say "verified $(basename "$wheel") against the release manifest"
	# --upgrade, not --force-reinstall: a published release is never rewritten
	# (the release workflow refuses to replace the assets of one), so the
	# version in the wheel's name is the whole answer to whether there is
	# anything to do here.
	install_into_environment --upgrade "$wheel" ||
		fail "could not install $(basename "$wheel") into $VENV"
	say "installed $(basename "$wheel")"
	rm -rf "$directory"
	commit_installers
}

update_toolchain() {
	[ "$UPDATE_TOOLCHAIN" = 1 ] || return 0
	step "Updating the Lean toolchain"
	have elan || {
		warn "elan is not on PATH; skipping"
		return 0
	}
	elan self update || warn "elan self update failed; continuing"
	elan update || warn "elan update failed; continuing"
}

update_mathlib() {
	[ "$UPDATE_MATHLIB" = 1 ] || return 0
	step "Updating Mathlib in $LEAN_PROJECT"
	[ -d "$LEAN_PROJECT" ] || {
		warn "no Lean project at $LEAN_PROJECT; skipping"
		return 0
	}
	have lake || fail "lake is not on PATH; open a new shell, or re-run scripts/install.sh"
	say "this downloads several gigabytes and typically takes 10-30 minutes"
	(
		cd "$LEAN_PROJECT"
		lake update || fail "lake update failed"
		lake exe cache get || fail "lake exe cache get failed"
		lake build || fail "lake build failed"
	)
	say "Mathlib is current"
}

verify_update() {
	step "Verifying"
	if [ -x "$VENV/bin/hardy" ]; then
		"$VENV/bin/hardy" doctor || warn "hardy doctor reported problems; see above"
	else
		warn "the hardy command is missing from $VENV; re-run scripts/install.sh"
	fi
}

hardy_update_main() {
	parse_update_arguments "$@"
	resolve_update_source
	if [ "$UPDATE_FROM" = release ]; then
		update_from_release
	else
		update_source
		update_environment
	fi
	update_toolchain
	update_mathlib
	verify_update
	printf '\n'
	say "Hardy is up to date."
	[ "$UPDATE_MATHLIB" = 1 ] || say "Mathlib was not touched; --mathlib updates it."
}

hardy_update_main "$@"
