# Pinned nixpkgs for the sandbox images — the same snapshot the toolchain is
# built from (lean4 4.30.0), on releases.nixos.org (part of the *.nixos.org
# allowlist the whole setup already requires). The sha256 makes the fetch a
# fixed-output derivation: Nix reuses the cached copy when the hash matches and
# verifies integrity, so no second host or re-download is needed once cached.
import (fetchTarball {
  url = "https://releases.nixos.org/nixpkgs/nixpkgs-26.11pre1038038.421eebfd0ec7/nixexprs.tar.xz";
  sha256 = "02jflbmayfbm8pm0d883djkzn0kyla65m2sr7nn5q3ihbmrsxi67";
}) { }
