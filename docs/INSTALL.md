# Installing Hardy

One command takes a clean machine to a working `hardy`. The installers are
written so that a machine with none of the prerequisites — no Python, no Lean,
no LaTeX — reaches an interactive session in a single run.

| Platform | Command |
| --- | --- |
| Linux | `scripts/install-linux.sh` |
| macOS | `scripts/install-macos.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1` |
| Any POSIX shell | `scripts/install.sh` — detects the OS and runs the right one |

```sh
git clone https://github.com/charlesmsiegel/hardy
cd hardy
scripts/install.sh          # add --yes for an unattended run
hardy
```

Windows needs no WSL: the PowerShell installer uses winget and elan's official
Windows release directly.

## What the installers do

Each step is skipped when the machine already satisfies it, so re-running the
installer is cheap and safe.

1. **Python 3.11+** — installed with the system package manager (`apt-get`,
   `dnf`/`yum`, `pacman`, `zypper`, `apk`), Homebrew, or winget. When no system
   Python is new enough, the POSIX installers fall back to a private
   [uv](https://astral.sh/uv)-managed Python 3.12.
2. **Hardy itself** — an editable install into a dedicated virtual environment,
   with the `hardy` command linked into your `PATH`. The install points at your
   clone, so keep the clone where it is (`git pull` then updates `hardy` too);
   re-run the installer after moving it.
3. **`lake`** — installed through [elan](https://github.com/leanprover/elan),
   the Lean toolchain manager, which supplies `lake`, `lean`, and `elan`.
4. **A shared Mathlib project** — a Lake project pinned to Mathlib's own
   toolchain, with Mathlib's prebuilt cache fetched (`lake exe cache get`).
   This is the long step: several gigabytes and typically 10–30 minutes.
5. **`pdflatex`** — a LaTeX subset large enough for Hardy's writeups
   (`amsmath`, `amsthm`, `amssymb`, `geometry`, `hyperref`). Use `--full-latex`
   for the distribution's complete TeX instead.
6. **Configuration** — the installer asks for a model identity and API key and
   writes them to the config file below (mode 600). An existing config file is
   never overwritten.
7. **Verification** — `hardy doctor` runs last and reports anything still
   missing.

### Where things go

| | Linux / macOS | Windows |
| --- | --- | --- |
| Virtual environment | `~/.local/share/hardy/venv` | `%LOCALAPPDATA%\hardy\venv` |
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
| `--prefix DIR` | `-Prefix DIR` | Where the virtual environment and Lean project live |
| `--bin-dir DIR` | `-BinDir DIR` | Where the `hardy` command is placed |

`HARDY_MODEL`, `HARDY_BASE_URL`, and `OPENAI_API_KEY` are used without prompting
when they are already set, which is how to configure an unattended install:

```sh
HARDY_MODEL=provider/model OPENAI_API_KEY=sk-... scripts/install.sh --yes
```

## Configuration

The config file is TOML and every key is optional:

```toml
model = "provider/model-version"
base_url = "https://api.openai.com/v1"
api_key = "sk-..."              # or leave unset and export OPENAI_API_KEY
api_key_env = "OPENAI_API_KEY"  # read the key from a different variable
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
hardy doctor          # Python, lake, the Lean project, pdflatex, model, API key
hardy doctor --deep   # also compiles `import Mathlib` + `norm_num`, which is slow
```

`doctor` never prints your API key — only where it was found.

## Troubleshooting

**`hardy: command not found`** — the command directory was added to your shell
profile, but the current shell predates it. Open a new terminal, or
`export PATH="$HOME/.local/bin:$PATH"`.

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
