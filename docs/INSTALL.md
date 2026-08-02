# Installing Hardy

One command takes a clean machine to a working `hardy`. The installers are
written so that a machine with none of the prerequisites — no Python, no Lean,
no LaTeX, and no clone — reaches an interactive session in a single run.

```sh
curl -fsSL https://raw.githubusercontent.com/charlesmsiegel/hardy/main/scripts/install.sh | sh
hardy
```

| Platform | Command |
| --- | --- |
| Linux | `scripts/install-linux.sh` |
| macOS | `scripts/install-macos.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1` |
| Any POSIX shell | `scripts/install.sh` — detects the OS and runs the right one |

Windows needs no WSL: the PowerShell installer uses winget and elan's official
Windows release directly.

Every one of these is exercised on a real runner of its own operating system on
every change to Hardy, from a single downloaded script, so a broken installer is
the project's problem before it is yours.

## Without cloning first

Installing Hardy means putting a released wheel into a virtual environment, so
nothing here needs the repository. An installer run on its own fetches the
installer bundle from Hardy's latest release into
`~/.local/share/hardy/installers`, downloads the released wheel, checks its
SHA-256 against the release's `SHA256SUMS`, and installs that. A wheel whose
digest does not match is refused rather than installed. All of these work:

```sh
curl -fsSL https://raw.githubusercontent.com/charlesmsiegel/hardy/main/scripts/install.sh | sh
bash ~/Downloads/install-macos.sh          # a copy saved from the browser
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```

Run a downloaded POSIX script with `bash` or `sh` as shown. A browser strips the
executable bit, so double-clicking it in Finder or a file manager opens it in a
text editor instead of running it — one common way for an install to appear to
do nothing at all.

`HARDY_VERSION=v0.1.0` installs a particular release instead of the current one.
The installer bundle is re-fetched on every run, so the scripts and the wheel
always come from the same release.

## From a clone, a fork, or a branch

Run from a checkout, the installers install *that tree*, editable — which is
what working on Hardy wants, since `git pull` then updates `hardy` too:

```sh
git clone https://github.com/charlesmsiegel/hardy
cd hardy
scripts/install.sh          # add --yes for an unattended run
```

`--from-release` (`-FromRelease`) installs the published wheel even from a
checkout, and `--from-source` (`-FromSource`) is the other way round.

`HARDY_REPO_REF` names a branch or tag to install from the repository rather
than from a release, and `HARDY_REPO_URL` points at a fork. Either one takes the
repository path: the tree is fetched to `~/.local/share/hardy/src` and installed
editable, exactly as a clone is.

## What the installers do

Each step is skipped when the machine already satisfies it, so re-running the
installer is cheap and safe.

1. **Python 3.11+** — installed with the system package manager (`apt-get`,
   `dnf`/`yum`, `pacman`, `zypper`, `apk`), Homebrew, or winget. When no system
   Python is new enough, the POSIX installers fall back to a private
   [uv](https://astral.sh/uv)-managed Python 3.12.
2. **Hardy itself** — the released wheel, verified against the release manifest,
   installed into a dedicated virtual environment with the `hardy` command
   linked into your `PATH`. Run from a checkout it is an editable install of
   that tree instead, so keep the clone where it is (`git pull` then updates
   `hardy` too) and re-run the installer after moving it.
3. **`lake`** — installed through [elan](https://github.com/leanprover/elan),
   the Lean toolchain manager, which supplies `lake`, `lean`, and `elan`.
4. **A shared Mathlib project** — a Lake project pinned to Mathlib's own
   toolchain, with Mathlib's prebuilt cache fetched (`lake exe cache get`).
   This is the long step: several gigabytes and typically 10–30 minutes.
5. **`pdflatex`** — a LaTeX subset large enough for Hardy's writeups
   (`amsmath`, `amsthm`, `amssymb`, `geometry`, `hyperref`). Use `--full-latex`
   for the distribution's complete TeX instead.
6. **The Claude Code CLI** — installed with npm when npm is available, since
   Hardy authenticates through it. Node itself is not installed for you; if npm
   is missing the installer says so rather than guessing a package manager.
7. **Configuration** — the installer asks for a model identity and writes it to
   the config file below (mode 600). There is no API key: sign in once with
   `claude login`. An existing config file is never overwritten.
8. **Verification** — `hardy doctor` runs last and reports anything still
   missing.

### Where things go

| | Linux / macOS | Windows |
| --- | --- | --- |
| Virtual environment | `~/.local/share/hardy/venv` | `%LOCALAPPDATA%\hardy\venv` |
| Fetched installers | `~/.local/share/hardy/installers` | the script you ran |
| Lean project | `~/.local/share/hardy/lean` | `%LOCALAPPDATA%\hardy\lean` |
| `hardy` command | `~/.local/bin/hardy` | `%LOCALAPPDATA%\hardy\bin\hardy.cmd` |
| Config file | `~/.config/hardy/config.toml` | `%APPDATA%\hardy\config.toml` |
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

`HARDY_MODEL` is used without prompting when it is already set, which is how to
configure an unattended install:

```sh
HARDY_MODEL=claude-opus-5 scripts/install.sh --yes
```

Authentication is separate from installation: run `claude login` once, and every
Hardy session on that machine uses your subscription.

## Configuration

The config file is TOML and every key is optional:

```toml
model = "claude-opus-5"         # any Claude model your subscription can reach
lean_project = "/home/you/.local/share/hardy/lean"
lean_command = "lake env lean"
lean_timeout = 180                # seconds per Lean invocation
latex_command = "pdflatex -interaction=nonstopmode -halt-on-error"
workspace = ".hardy"
```

Each setting can be overridden by the matching `HARDY_*` environment variable
(`HARDY_MODEL`, `HARDY_LEAN_PROJECT`, …) and then by a command-line flag, in
that order. `HARDY_CONFIG` selects a different config file, as does `--config`.

`lean_project` is what lets `hardy` run from any directory: Lean resolves
imports through that Lake project rather than the directory you happen to be
in. Point it at your own Lake project when you want your own Lean modules
importable, or launch Hardy with `--lean-project /path/to/project`.

## Checking an installation

```sh
hardy doctor          # Python, lake, the Lean project, pdflatex, model, SDK, CLI, login
hardy doctor --deep   # also compiles `import Mathlib` + `norm_num`, which is slow
```

`doctor` checks that the Claude Code CLI is signed in, not merely installed: a
logged-out machine fails here rather than on your first question.

## Updating

```sh
scripts/update.sh                 # Linux, macOS
powershell -ExecutionPolicy Bypass -File scripts\update-windows.ps1
```

There are two kinds of installation and this updates either, then runs `doctor`.

An install made **from a release** has no source tree: the updater downloads the
newest released wheel, checks it against the release manifest, and installs it.
`HARDY_VERSION` moves to a particular release instead.

An install made **from a checkout** is editable, so new *code* is already live
once the tree moves; the reinstall is what picks up a newly declared
*dependency*, which is otherwise a current checkout and a broken `hardy`
command. Which one you have is found by asking the installed environment where
its own code lives, rather than by a record that could go stale.

Mathlib and the Lean toolchain are left alone by default — refreshing Mathlib is
a multi-gigabyte rebuild, and rarely what updating Hardy is about.

| Flag | PowerShell | Effect |
| --- | --- | --- |
| `--mathlib` | `-Mathlib` | also `lake update`, `cache get`, and `build` |
| `--toolchain` | `-Toolchain` | also `elan self update` and `elan update` |
| `--source DIR` | `-Source DIR` | update this tree instead of the installed one |

An install made from a downloaded repository archive is the one case with
neither: it is editable, but has no history to pull. Re-run the installer to get
a newer copy.

## Uninstalling

```sh
scripts/uninstall.sh              # Linux, macOS
powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1
```

Removes the virtual environment, whatever the installer fetched (the installer
bundle of a release install, or a source tree), the `hardy` command, and the
PATH lines the installer added. Before touching anything expensive to rebuild or
personal, it asks:

| Asked about | Kept unless you say otherwise |
| --- | --- |
| The Lean project | a multi-gigabyte download to rebuild |
| The config file | holds your model choice |
| elan and the Lean toolchain | other Lean projects on the machine use it |

`--yes` answers **no** to all three, so an unattended uninstall never silently
takes them; `--all` answers yes. Individually: `--remove-lean-project`,
`--remove-config`, `--remove-toolchain` (`-RemoveLeanProject`, `-RemoveConfig`,
`-RemoveToolchain` in PowerShell).

TeX, Node, and the Claude Code CLI are never removed. Hardy may have installed
them, but they are ordinary shared tools that something else likely wants.

Only the PATH lines carrying the installer's own marker comment are stripped; a
line you wrote yourself for the same directory is left alone.

## Troubleshooting

**The installer printed nothing and exited** — you likely double-clicked it
instead of running it from a terminal (see [Without cloning
first](#without-cloning-first)). Run `bash path/to/install-macos.sh` and read
the output; every failure path prints a reason before exiting.

**`could not fetch .../SHA256SUMS`** — the installer found no release to install
from. Check your network, or install from a checkout instead
(`git clone …; scripts/install.sh`).

**`checksum mismatch`** — what was downloaded is not what the release vouches
for, and the installer stopped rather than install it. Re-run it; if it happens
again, the download is being interfered with somewhere between you and GitHub.

**`hardy: command not found`** — the command directory was added to your shell
profile, but the current shell predates it. Open a new terminal, or
`export PATH="$HOME/.local/bin:$PATH"`. The installer writes the PATH line to
`~/.profile`, and to `~/.zshrc` when zsh is your login shell — the macOS default,
where `~/.profile` is never read. If you use a shell that reads neither, add the
line to its startup file yourself; the installer prints exactly which files it
touched.

**`lake: command not found`** after installing elan — same cause; elan adds
`~/.elan/bin` to your profile. Open a new terminal.

**Lean errors mentioning `import Mathlib`** — either the shared project was
skipped (`--skip-mathlib`) or its cache is incomplete. Rebuild it:

```sh
cd ~/.local/share/hardy/lean && lake exe cache get && lake build
```

**`pdflatex` fails on a missing `.sty`** — the LaTeX subset lacks a package
Hardy's writeup used. Install it with your TeX manager (`tlmgr install NAME`,
MiKTeX installs on demand), or re-run the installer with `--full-latex`.

**Not enough disk space** — Mathlib's cache and the Lean toolchain need roughly
10 GB. `--skip-mathlib` installs everything else and leaves the Lean project to
you.

**A distribution with no supported package manager** — install Python 3.11+,
git, curl, and a TeX distribution yourself, then re-run the installer: it skips
what is already present.

## Safety

Hardy executes model-generated Lean and LaTeX directly, without isolation.
Install and run it on a machine you are willing to treat as disposable, and only
with model output you are willing to trust. See
[DESIGN.md](../DESIGN.md#trust-boundary-and-safety).
