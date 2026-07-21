# hardy-tex:dev — the untrusted-TeX sandbox image, built entirely from the Nix
# store (no Docker Hub base, no network at build or run time). Contains only a
# self-contained TeX Live + busybox (sh/cp/tar/timeout/find) — nothing Lean or
# repo-related, so untrusted TeX has nothing to \input beyond its own staging.
#
# Build:  nix-build nix/tex-image.nix && docker load < result
# Produces image  hardy-tex:dev
let
  pkgs = import (fetchTarball {
    url = "https://releases.nixos.org/nixpkgs/nixpkgs-26.11pre1038038.421eebfd0ec7/nixexprs.tar.xz";
  }) { };
in
pkgs.dockerTools.buildLayeredImage {
  name = "hardy-tex";
  tag = "dev";
  copyToRoot = pkgs.buildEnv {
    name = "hardy-tex-root";
    paths = [ pkgs.texliveMedium pkgs.busybox pkgs.coreutils ];
    pathsToLink = [ "/bin" "/share" ];
  };
  config = {
    Env = [ "PATH=/bin" ];
    Cmd = [ "/bin/sh" ];
  };
}
