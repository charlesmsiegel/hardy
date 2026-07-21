# ---- Target "tex": untrusted-TeX compiler. Deliberately contains no Lean
# ---- project or repo state — nothing for \input to disclose.
FROM ubuntu:24.04 AS tex

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates fontconfig \
    && rm -rf /var/lib/apt/lists/*

# tectonic: pinned versioned release artifact, digest-verified — the
# convenience installer script is mutable remote content and would let the
# compiler drift under an unchanged commit. Before the FIRST image build,
# record the digest:
#   curl -fsSL <the URL below with the version filled in> | sha256sum
# and paste it into TECTONIC_SHA256; every later build fails on mismatch.
# (Asset is x86_64; on arm64 hosts use the aarch64-unknown-linux-musl asset
# and its own digest.)
ARG TECTONIC_VERSION=0.15.0
ARG TECTONIC_SHA256=RECORD-DIGEST-AT-FIRST-BUILD
RUN curl -fsSL -o /tmp/tectonic.tar.gz \
      "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    && echo "${TECTONIC_SHA256}  /tmp/tectonic.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin tectonic \
    && rm /tmp/tectonic.tar.gz

RUN useradd -m hardy
USER hardy
WORKDIR /home/hardy

# Warm tectonic's package cache so --only-cached compiles work with no network.
COPY --chown=hardy:hardy docker/warm.tex /tmp/warm/warm.tex
RUN cd /tmp/warm && tectonic --untrusted warm.tex && rm -rf /tmp/warm

# ---- Target "lean": REPL worker image.
FROM ubuntu:24.04 AS lean

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m hardy
USER hardy
WORKDIR /home/hardy

RUN curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
        | sh -s -- -y --default-toolchain none
ENV PATH="/home/hardy/.elan/bin:${PATH}"

# Build the pinned project with Mathlib's prebuilt oleans (network OK at build time).
COPY --chown=hardy:hardy lean_project lean_project
RUN cd lean_project && lake exe cache get && lake build

# Build the repl at the same toolchain (host must have run scripts/setup_lean.sh
# so vendor/repl exists and is checked out at the matching tag).
COPY --chown=hardy:hardy vendor/repl repl
RUN diff repl/lean-toolchain lean_project/lean-toolchain \
    && cd repl && lake build

# Capture LEAN_PATH at build time so runtime never invokes lake
# (the runtime rootfs is read-only; lake wants to write).
RUN cd lean_project \
    && echo "export LEAN_PATH=$(lake env printenv LEAN_PATH)" > /home/hardy/repl-env.sh
