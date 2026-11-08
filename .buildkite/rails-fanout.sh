#!/usr/bin/env bash
# Run the Rails fan-out demo against the exact unreleased CLI candidate.
set -euo pipefail

# v0.17.1 cannot run this command vocabulary. Switch to a release archive and
# its checksum only after a compatible release exists and passes this demo.
spore_commit="e4fffdbefe4a6ec29af2f080208fa3e9b008591f"

# Keep spore's caches, runtime state, saves, and logs in one job directory:
# image-backed saves must share a filesystem with the rootfs cache, and other
# spore versions on this agent must not see this job's cache.
work="$(mktemp -d "${TMPDIR:-/tmp}/sporevm-examples-rails.XXXXXX")"
cleanup() {
  local status=$?
  # The job directory is deleted below, so show the parent and fork logs on
  # failure. The fan-out output is already in the job log.
  if [[ "${status}" != "0" ]]; then
    for log in parent fork; do
      if [[ -f "${work}/run/${log}.log" ]]; then
        echo "+++ ${log}.log"
        cat "${work}/run/${log}.log"
      fi
    done
  fi
  rm -rf "${work}"
}
trap cleanup EXIT
unset SPOREVM_ROOTFS_CACHE_DIR SPOREVM_KERNEL_CACHE_DIR SPOREVM_BUNDLE_CACHE_DIR SPOREVM_KERNEL_IMAGE SPOREVM_RUN_INITRD
export XDG_CACHE_HOME="${work}/cache"
export SPOREVM_RUNTIME_DIR="${work}/runtime"
export TMPDIR="${work}/tmp"
mkdir -p "${XDG_CACHE_HOME}" "${TMPDIR}"
mkdir -m 700 "${SPOREVM_RUNTIME_DIR}"

git init -q "${work}/source"
git -C "${work}/source" remote add origin https://github.com/sporevm/sporevm.git
git -C "${work}/source" fetch --depth 1 origin "${spore_commit}"
git -C "${work}/source" checkout --detach FETCH_HEAD
[[ "$(git -C "${work}/source" rev-parse HEAD)" == "${spore_commit}" ]]
mise trust "${work}/source/mise.toml"
mise -C "${work}/source" exec zig@0.16.0 -- zig build --release=safe
export PATH="${work}/source/zig-out/bin:${PATH}"

bash rails/bin/fanout-rspec --count 4 --workdir "${work}/run"
