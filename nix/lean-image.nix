# hardy-lean:dev — the REPL worker sandbox image, built from the Nix store plus
# the artifacts setup_lean.sh produces (no Docker Hub base, no elan, no network
# at build or run time).
#
# Prerequisite: run scripts/setup_lean.sh first so lean_project/.lake (Mathlib
# oleans) and vendor/repl/.lake/build/bin/repl exist. Those get baked into the
# image at /home/hardy, matching hardy.lean.launch.sandboxed_worker_spec (which
# runs `. /home/hardy/repl-env.sh && exec /home/hardy/repl/.lake/build/bin/repl`
# and wipes /scratch between checks). LEAN_PATH is captured over the built
# package olean dirs at image-build time, so the read-only container never runs
# lake. Because full Mathlib's oleans are multi-GB, this image is large (~10 GB)
# and needs a host with matching disk; on a constrained host, mount the trusted,
# already-built lean_project read-only instead of baking it.
#
# Build:  nix-build nix/lean-image.nix && docker load < result  (-> hardy-lean:dev)
let
  pkgs = import ./nixpkgs.nix;
  leanProject = ../lean_project; # after setup_lean.sh: .lake holds the oleans
  repl = ../vendor/repl; # after setup_lean.sh: .lake/build/bin/repl exists
in
pkgs.dockerTools.buildLayeredImage {
  name = "hardy-lean";
  tag = "dev";
  contents = [
    (pkgs.buildEnv {
      name = "hardy-lean-root";
      paths = [ pkgs.lean4 pkgs.busybox pkgs.coreutils ];
      pathsToLink = [ "/bin" ];
    })
    pkgs.dockerTools.fakeNss
  ];
  extraCommands = ''
    mkdir -p home/hardy
    cp -r --no-preserve=mode,ownership ${leanProject} home/hardy/lean_project
    cp -r --no-preserve=mode,ownership ${repl} home/hardy/repl
    # repl-env.sh exports the toolchain env a directly-launched repl needs
    # (see hardy.lean.launch.repl_env): LEAN_SYSROOT is essential — without it
    # continuation checks lose core (numeric literals / OfNat) — plus LEAN_PATH
    # over every built package olean dir and LD_LIBRARY_PATH for the shared
    # runtime. No lake at runtime on the read-only rootfs.
    lp=""
    for d in home/hardy/lean_project/.lake/build/lib/lean \
             home/hardy/lean_project/.lake/packages/*/.lake/build/lib/lean; do
      [ -d "$d" ] && lp="/$d:$lp"
    done
    {
      echo "export LEAN_SYSROOT=${pkgs.lean4}"
      echo "export LD_LIBRARY_PATH=${pkgs.lean4}/lib/lean:${pkgs.lean4}/lib"
      echo "export LEAN_PATH=$lp"
    } > home/hardy/repl-env.sh
  '';
  config = {
    Env = [ "PATH=/bin" ];
    # Unprivileged: model-generated Lean runs arbitrary IO during elaboration.
    User = "65534:65534";
    Cmd = [ "/bin/sh" ];
  };
}
