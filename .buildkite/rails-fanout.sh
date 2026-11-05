#!/usr/bin/env bash
# Run the Rails fan-out demo end to end with a pinned SporeVM release.
set -euo pipefail

spore_version="v0.17.1"
spore_sha256="07452068394f2cbac911cffda1dd5d97692ed5b4c5d85b30d03401654ab89327"

# Keep spore's caches, runtime state, saves, and logs in one job directory:
# image-backed saves must share a filesystem with the rootfs cache, and other
# spore versions on this agent must not see this job's cache.
work="$(mktemp -d "${TMPDIR:-/tmp}/sporevm-examples-rails.XXXXXX")"
trap 'rm -rf "${work}"' EXIT
export XDG_CACHE_HOME="${work}/cache"
export SPOREVM_RUNTIME_DIR="${work}/runtime"
export TMPDIR="${work}/tmp"
mkdir -p "${XDG_CACHE_HOME}" "${TMPDIR}"
mkdir -m 700 "${SPOREVM_RUNTIME_DIR}"

curl -fsSL --retry 3 -o "${work}/spore.tar.gz" \
  "https://github.com/sporevm/sporevm/releases/download/${spore_version}/spore_Linux_arm64.tar.gz"
echo "${spore_sha256}  ${work}/spore.tar.gz" | sha256sum -c -
tar -xzf "${work}/spore.tar.gz" -C "${work}"
export PATH="${work}/spore_Linux_arm64/bin:${PATH}"

bash rails/bin/fanout-rspec --count 4 --workdir "${work}/run"
