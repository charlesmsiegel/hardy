# Installing Hardy

This page gets a working `hardy` command onto your machine, explains what
each installer does, and says where things go; it is for anyone installing
or troubleshooting Hardy for the first time.

## From a clone, a fork, or a branch

This is the path that works today, before Hardy has a tagged release. Run
from a checkout, the installers install *that tree*, editable, which is what
working on Hardy wants, since `git pull` then updates `hardy` too:

```sh
git clone https://github.com/charlesmsiegel/hardy
cd hardy
scripts/install.sh
```

Add `--yes` for an unattended run.

| Platform | Command |
| --- | --- |
| Linux | `scripts/install-linux.sh` |
| macOS | `scripts/install-macos.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1` |
| Any POSIX shell | `scripts/install.sh`, which detects the OS and runs the right one |

Windows needs no WSL: the PowerShell installer uses winget and elan's official
Windows release directly.

Every one of these is exercised on a real runner of its own operating system
on every change to Hardy, from a single downloaded script, so a broken
installer is the project's problem before it is yours.

`--from-release` (`-FromRelease`) installs the published wheel even from a
checkout, and `--from-source` (`-FromSource`) is the other way round; once
Hardy has a release, this is how a checkout tests against it.

`HARDY_REPO_REF` names a branch or a tag, which only the repository can
serve, so setting it takes the repository path: that tree is fetched to
`~/.local/share/hardy/src` and installed editable, exactly as a clone is. It
outranks the release selectors, and is always re-fetched, so changing the ref
cannot silently reinstall the previous one.

It does not override a checkout. Running an installer from a source tree
installs *that tree*; the ref chooses what to fetch when there is nothing
here to install, and quietly replacing a developer's working copy with some
other revision would be a worse surprise than ignoring the variable. To
install a ref while standing in a checkout, run the installer from somewhere
else.

`HARDY_REPO_URL` points at a different repository, a fork, and moves both
its releases and its archives. On its own it installs that fork's latest
release; if the fork publishes none, the repository is the fallback, exactly
as it is before Hardy's own first release. The repository an installation
came from is recorded in `~/.local/share/hardy/release-origin`
(`%LOCALAPPDATA%\hardy\release-origin`) and reused by the updater, so a
fork's installation keeps following that fork without having to be told
again.

## Without cloning first

Once Hardy has a tagged release, this will be the path that needs no
repository at all: installing Hardy will mean putting a released wheel into
a virtual environment. An installer run on its own will fetch the installer
bundle from Hardy's latest release into `~/.local/share/hardy/installers`
(`%LOCALAPPDATA%\hardy\installers`), hand over to those scripts, and they
will download the released wheel and install it. Both downloads will be
checked against the release's own `SHA256SUMS`, with anything whose digest
does not match refused rather than used. Once a release exists, all of these
will work:

```sh
curl -fsSL https://raw.githubusercontent.com/charlesmsiegel/hardy/main/scripts/install.sh | sh
bash ~/Downloads/install-macos.sh          # a copy saved from the browser
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```

Today, before Hardy's first release, none of these scripts have a release to
fetch. Use **From a clone, a fork, or a branch** above instead; with neither
`HARDY_VERSION` nor `HARDY_RELEASE_BASE_URL` set, the installers fall back to
the repository and say so.

Run a downloaded POSIX script with `bash` or `sh` as shown. A browser strips
the executable bit, so double-clicking it in Finder or a file manager opens
it in a text editor instead of running it, one common way for an install to
appear to do nothing at all.

`HARDY_VERSION=v0.1.0` will install a particular release instead of the
current one. The installer bundle is re-fetched on every run, so the scripts
and the wheel always come from the same release.

Naming a release means that release: when `HARDY_VERSION` (or
`HARDY_RELEASE_BASE_URL`) is set and that release cannot be installed, the
installer stops rather than quietly putting a branch on the machine under a
version number that says otherwise.

## What the installers do

Each step is skipped when the machine already satisfies it, so re-running the
installer is cheap and safe.

1. **Python 3.11+**, installed with the system package manager (`apt-get`,
   `dnf`/`yum`, `pacman`, `zypper`, `apk`), Homebrew, or winget. When no system
   Python is new enough, the POSIX installers fall back to a private
   [uv](https://astral.sh/uv)-managed Python 3.12.
2. **Hardy itself**, the released wheel, verified against the release
   manifest, installed into a dedicated virtual environment with the `hardy`
   command linked into your `PATH`. Run from a checkout it is an editable
   install of that tree instead, so keep the clone where it is (`git pull`
   then updates `hardy` too) and re-run the installer after moving it.
3. **`lake`**, installed through [elan](https://github.com/leanprover/elan),
   the Lean toolchain manager, which supplies `lake`, `lean`, and `elan`.
4. **A shared Mathlib project**, a Lake project pinned to one Lean release
   and one Mathlib tag (the values in `scripts/lib/common.sh`, which
   `hardy.app.installers` and the Windows installer repeat), with Mathlib's
   prebuilt cache fetched (`lake exe cache get`). This is the long step:
   several gigabytes and typically 10 to 30 minutes. Every recorded run names
   the Lean version and commit, the Mathlib revision, and the manifest digest
   it actually ran against, and `hardy doctor` says when a project has been
   repinned away from these.
5. **`pdflatex`**, a LaTeX subset large enough for Hardy's writeups
   (`amsmath`, `amsthm`, `amssymb`, `geometry`, `hyperref`). Use
   `--full-latex` for the distribution's complete TeX instead.
6. **The Claude Code CLI**, installed with npm when npm is available, since
   Hardy authenticates through it. Node itself is not installed for you; if
   npm is missing the installer says so rather than guessing a package
   manager.
7. **Configuration**: the installer asks for a model identity and writes it
   to the config file below (mode 600). On the default backend there is no
   API key: sign in once with `claude login`. The opt-in `api` backend is
   the one exception and is not installed by default; see **The API
   backend** below. An existing config file is never overwritten.
8. **Verification**: `hardy doctor` runs last and reports anything still
   missing.

### Where things go

| | Linux / macOS | Windows |
| --- | --- | --- |
| Virtual environment | `~/.local/share/hardy/venv` | `%LOCALAPPDATA%\hardy\venv` |
| Fetched installers | `~/.local/share/hardy/installers` | `%LOCALAPPDATA%\hardy\installers` |
| Recorded release origin | `~/.local/share/hardy/release-origin` | `%LOCALAPPDATA%\hardy\release-origin` |
| Lean project | `~/.local/share/hardy/lean` | `%LOCALAPPDATA%\hardy\lean` |
| `hardy` command | `~/.local/bin/hardy` | `%LOCALAPPDATA%\hardy\bin\hardy.cmd` |
| Config file | `~/.hardy/config.toml` | `%USERPROFILE%\.hardy\config.toml` |
| Lean toolchain | `~/.elan` | `%USERPROFILE%\.elan` |

Nothing is installed system-wide except distribution packages (Python, git,
curl, TeX), which are the only steps that use `sudo`.

## Options

| POSIX | Windows | Effect |
| --- | --- | --- |
| `--yes` | `-Yes` | Non-interactive; accept every install, skip prompts |
| `--skip-mathlib` | `-SkipMathlib` | Install `lake` but do not build the shared Mathlib project |
| `--skip-latex` | `-SkipLatex` | Do not install TeX |
| `--full-latex` | `-FullLatex` | Full TeX Live / MacTeX / TeX Live instead of the subset |
| `--no-config` | `-NoConfig` | Do not write a config file |
| `--from-release` | `-FromRelease` | Install the published wheel even from a checkout |
| `--from-source` | `-FromSource` | Install this source tree, editable |
| `--prefix DIR` | `-Prefix DIR` | Where the virtual environment and Lean project live |
| `--bin-dir DIR` | `-BinDir DIR` | Where the `hardy` command is placed |

`HARDY_MODEL` is used without prompting when it is already set, which is how
to configure an unattended install:

```sh
HARDY_MODEL=claude-opus-5 scripts/install.sh --yes
```

Authentication is separate from installation: run `claude login` once, and
every Hardy session on that machine uses your subscription.

## Configuration

The config file is TOML and lives at `~/.hardy/config.toml`
(`%USERPROFILE%\.hardy\config.toml` on Windows); every key is optional, and
the installer writes only the ones it asked you about or discovered on your
machine: `model`, the identity you gave the installer, and, once `hardy
setup` has found your toolchain, the three paths it records: `elan`, `lake`,
`tectonic`. For every other setting, its default, its environment variable,
and what it means, see [the full settings table](reference/configuration.md#settings).

### The API backend

`backend = "api"` (or `HARDY_BACKEND=api`) sends to the Anthropic Messages
API directly instead of through the Claude Code CLI, and Hardy runs the turn
loop itself. It is opt-in because it is billed differently: it needs an API
key rather than a subscription, and which transport carried a run is
recorded as part of that run's identity.

```sh
pip install 'hardy-prover[api]'   # or: uv pip install 'hardy-prover[api]'
export ANTHROPIC_API_KEY=...
```

See [Configuration](reference/configuration.md#settings) for `context_window`
and the other settings that only matter on this backend.

## Checking an installation

The internal packages ship together in `hardy-prover`; there are no separate
capability installations. The console script uses `hardy.app.cli:main`.
`python -m hardy` and the legacy `python -m hardy.cli` launch the same
commands; `python -m hardy.mcp_server` remains available to Codex clients
through the `hardy.app.mcp` adapter. `python -m hardy.cas_driver` launches
the helper in `hardy.algebra.driver`. These are entry-point shims;
configuration, installation and doctor implementations live in `hardy.app`,
and capability implementations are imported from their domain packages.

The wheel includes `hardy/algebra/driver.py`, templates under
`hardy/prompts/` and `hardy/documents/templates/`,
`hardy/documents/export.css`, the viewer pages under `hardy/app/`, and
`hardy/workflows/acceptance_problems.json`.

For a packaging smoke check from a directory outside a checkout, run `hardy
--help` and `python -m hardy prove --help`. These need no model or Lean
installation. Development verification uses `uv run --extra test pytest
--cov`; the unchanged coverage floor includes all relocated modules. Real
toolchain and live model tests remain separate, opt-in checks. Module
boundaries add no execution isolation.

CI also builds a wheel and runs `scripts/smoke_wheel.py` with a fresh
environment from outside the checkout. It checks packaged resources, command
help, deterministic workflow outcomes, the SymPy helper and real MCP stdio
against a fake Lean service. MCP stays on the supported v1 API (`>=1.28,<2`);
v2 removed the `FastMCP` import this transport uses.

```sh
hardy doctor          # Python, lake, the Lean project, pdflatex, model, SDK, CLI, login
hardy doctor --deep   # also compiles `import Mathlib` + `norm_num`, which is slow
```

`doctor` and `setup` both check whichever backend the config *selects*, not
whichever one Hardy shipped with. On the default backend that means the
Claude Code CLI is signed in, not merely installed: a logged-out machine
fails here rather than on your first question. With `backend = "api"` it
means the `anthropic` package is importable and `ANTHROPIC_API_KEY` is set,
whether one is set, never what it is, since a doctor report is something
people paste into issues.

## Updating

From a checkout, these are in it. An installation made **from a release**
has no checkout: the installers it was run from are kept beside it, and that
is where its updater lives:

```sh
~/.local/share/hardy/installers/scripts/update.sh          # Linux, macOS
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\hardy\installers\scripts\update-windows.ps1"

scripts/update.sh                 # from a checkout: Linux, macOS
powershell -ExecutionPolicy Bypass -File scripts\update-windows.ps1
```

There are two kinds of installation and this updates either, then runs
`doctor`.

An install made **from a release** has no source tree: the updater downloads
the newest released wheel, checks it against the release manifest, and
installs it. `HARDY_VERSION` moves to a particular release instead. The
installer scripts kept under `~/.local/share/hardy/installers` are replaced
at the same time, so the updater and uninstaller on disk always match the
release that is installed.

An install made **from a checkout** is editable, so new *code* is already
live once the tree moves; the reinstall is what picks up a newly declared
*dependency*, which is otherwise a current checkout and a broken `hardy`
command. Which one you have is found by asking the installed environment
where its own code lives, rather than by a record that could go stale.

Mathlib and the Lean toolchain are left alone by default; refreshing Mathlib
is a multi-gigabyte rebuild, and rarely what updating Hardy is about.

| Flag | PowerShell | Effect |
| --- | --- | --- |
| `--mathlib` | `-Mathlib` | also `lake update`, `cache get`, and `build` |
| `--toolchain` | `-Toolchain` | also `elan self update` and `elan update` |
| `--source DIR` | `-Source DIR` | update this tree instead of the installed one |

An install made from a downloaded repository archive is the one case with
neither: it is editable, but has no history to pull. Re-run the installer to
get a newer copy.

## Uninstalling

The same two places as the updater: beside the installation for a release
install, in the checkout for a source one:

```sh
~/.local/share/hardy/installers/scripts/uninstall.sh       # Linux, macOS
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\hardy\installers\scripts\uninstall-windows.ps1"

scripts/uninstall.sh              # from a checkout: Linux, macOS
powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1
```

Removes the virtual environment, whatever the installer fetched (the
installer bundle of a release install, or a source tree), the `hardy`
command, and the PATH lines the installer added. Before touching anything
expensive to rebuild or personal, it asks:

| Asked about | Kept unless you say otherwise |
| --- | --- |
| The Lean project | a multi-gigabyte download to rebuild |
| The config file | holds your model choice |
| elan and the Lean toolchain | other Lean projects on the machine use it |

`--yes` answers **no** to all three, so an unattended uninstall never
silently takes them; `--all` answers yes. Individually:
`--remove-lean-project`, `--remove-config`, `--remove-toolchain`
(`-RemoveLeanProject`, `-RemoveConfig`, `-RemoveToolchain` in PowerShell).

TeX, Node, and the Claude Code CLI are never removed. Hardy may have
installed them, but they are ordinary shared tools that something else
likely wants.

Only the PATH lines carrying the installer's own marker comment are
stripped; a line you wrote yourself for the same directory is left alone.

## Troubleshooting

**The installer printed nothing and exited**, you likely double-clicked it
instead of running it from a terminal (see [Without cloning
first](#without-cloning-first)). Run `bash path/to/install-macos.sh` and read
the output; every failure path prints a reason before exiting.

**`could not fetch .../SHA256SUMS`**, the installer found no release to
install from, which is expected before Hardy's first release. Install from a
checkout instead (`git clone …; scripts/install.sh`).

**`checksum mismatch`**, what was downloaded is not what the release vouches
for, and the installer stopped rather than install it. Re-run it; if it
happens again, the download is being interfered with somewhere between you
and GitHub.

**`hardy: command not found`**, the command directory was added to your
shell profile, but the current shell predates it. Open a new terminal, or
`export PATH="$HOME/.local/bin:$PATH"`. The installer writes the PATH line to
`~/.profile`, and to `~/.zshrc` when zsh is your login shell (the macOS
default, where `~/.profile` is never read). If you use a shell that reads
neither, add the line to its startup file yourself; the installer prints
exactly which files it touched.

**`lake: command not found`** after installing elan, same cause; elan adds
`~/.elan/bin` to your profile. Open a new terminal.

**Lean errors mentioning `import Mathlib`**, either the shared project was
skipped (`--skip-mathlib`) or its cache is incomplete. Rebuild it:

```sh
cd ~/.local/share/hardy/lean && lake exe cache get && lake build
```

**`pdflatex` fails on a missing `.sty`**, the LaTeX subset lacks a package
Hardy's writeup used. Install it with your TeX manager (`tlmgr install
NAME`, MiKTeX installs on demand), or re-run the installer with
`--full-latex`.

**Not enough disk space**, Mathlib's cache and the Lean toolchain need
roughly 10 GB. `--skip-mathlib` installs everything else and leaves the
Lean project to you.

**A distribution with no supported package manager**, install Python 3.11+,
git, curl, and a TeX distribution yourself, then re-run the installer: it
skips what is already present.

## Safety

Hardy executes model-generated Lean and LaTeX directly, without isolation.
Install and run it on a machine you are willing to treat as disposable, and
only with model output you are willing to trust.
[Running Hardy safely](guides/running-safely.md) says how to make that
disposability real, install into a container or VM that holds only the
work, and what the trust boundary is and is not;
[Trust boundary](design/trust-boundary.md) carries the design argument.
[isolation.md](isolation.md)
tracks the confinement work that will narrow this boundary.
