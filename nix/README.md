# Sandbox images (Nix-built)

The sandbox images are built entirely from the Nix store — no Docker Hub base
image, no `apt`/`curl`, and no GitHub-release or network downloads at build or
run time. This replaces the old `docker/Dockerfile` (which pulled `ubuntu`,
`elan`, and `tectonic` from hosts that a no-network / egress-locked policy
blocks) with `dockerTools.buildLayeredImage` derivations that assemble the same
capabilities from `cache.nixos.org`.

Both images pin the same nixpkgs snapshot used to build the toolchain
(`lean_project/lean-toolchain` ↔ the nixpkgs `lean4`), so the whole stack is
reproducible from one revision.

**Who runs Nix:** maintainers and CI only, on Linux/macOS. Nix has no native
Windows port, and the project's platform policy (DESIGN.md Component 7) says
WSL must never be required — so end users get these images by `docker pull`
of the CI-published, digest-pinned builds, not by building them. Running the
containers needs any Linux-container runtime (on Windows, Docker Desktop's
Hyper-V backend suffices).

## `hardy-tex:dev` — untrusted-TeX compiler

Self-contained TeX Live (`pdflatex`) + busybox. Contains nothing Lean- or
repo-related, so untrusted TeX has nothing to `\input` beyond its own staging.

```sh
nix-build nix/tex-image.nix && docker load < result   # -> hardy-tex:dev
```

Validated: `scripts/sample_writeup.py --sandbox` and the `docker`-tier
`test_sandboxed_texlive_compiles_offline` compile a real PDF inside the
no-network, read-only container.

## `hardy-lean:dev` — REPL worker (in progress)

Packages the built `lean_project` (Mathlib oleans) + the
`leanprover-community/repl` binary + the Lean runtime. Because full Mathlib's
oleans are multi-GB, the image is large; on a disk-constrained host the oleans
can instead be provided as a read-only bind mount of the trusted, already-built
`lean_project` (the untrusted proof text still arrives only over the REPL's
stdin). See `nix/lean-image.nix`.
