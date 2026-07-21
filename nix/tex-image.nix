# hardy-tex:dev — the untrusted-TeX sandbox image, built entirely from the Nix
# store (no Docker Hub base, no network at build or run time). Contains only a
# self-contained TeX Live + busybox (sh/cp/tar/timeout/find) — nothing Lean or
# repo-related, so untrusted TeX has nothing to \input beyond its own staging.
#
# Runs as an unprivileged user (65534): defense-in-depth on top of the
# sandbox's --cap-drop ALL / --read-only / --network none / no-new-privileges.
# The compile writes only to the quota'd /scratch tmpfs (HOME/TEXMFVAR there).
#
# Build:  nix-build nix/tex-image.nix && docker load < result   (-> hardy-tex:dev)
let
  pkgs = import ./nixpkgs.nix;
in
pkgs.dockerTools.buildLayeredImage {
  name = "hardy-tex";
  tag = "dev";
  contents = [
    (pkgs.buildEnv {
      name = "hardy-tex-root";
      paths = [ pkgs.texliveMedium pkgs.busybox pkgs.coreutils ];
      pathsToLink = [ "/bin" "/share" ];
    })
    pkgs.dockerTools.fakeNss # /etc/passwd etc. with a nobody(65534) entry
  ];
  config = {
    Env = [ "PATH=/bin" ];
    User = "65534:65534";
    Cmd = [ "/bin/sh" ];
  };
}
