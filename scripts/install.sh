#!/usr/bin/env sh
#
# Detects the operating system and runs the matching Hardy installer. It holds
# no installation logic of its own: scripts/install-linux.sh,
# scripts/install-macos.sh, and scripts/install-windows.ps1 are the real ones,
# and each is safe to run directly.
#
#   scripts/install.sh --yes
set -eu

directory="$(cd "$(dirname "$0")" && pwd)"

case "$(uname -s)" in
Linux*) exec "$directory/install-linux.sh" "$@" ;;
Darwin*) exec "$directory/install-macos.sh" "$@" ;;
MINGW* | MSYS* | CYGWIN* | Windows_NT)
	cat >&2 <<'EOF'
error: this looks like Windows.

Hardy's Windows installer is PowerShell, not shell. Run it from PowerShell:

  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1

WSL is not required. If you are deliberately installing inside a WSL
distribution, run scripts/install-linux.sh instead.
EOF
	exit 2
	;;
*)
	printf 'error: unsupported operating system: %s\n' "$(uname -s)" >&2
	printf 'Supported: Linux, macOS, and Windows (scripts/install-windows.ps1).\n' >&2
	exit 2
	;;
esac
