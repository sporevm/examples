# Rails/Postgres RSpec Fan-Out

This example is the warm CI shape SporeVM is aiming at:

1. Build a Docker image that contains Ruby, Rails, PostgreSQL, the app, and the
   test suite.
2. Import the buildx OCI layout into SporeVM's local rootfs cache.
3. Run that image as a SporeVM immutable rootfs.
4. Start PostgreSQL inside the VM, load the schema, eager-load Rails and RSpec,
   cleanly stop PostgreSQL, then capture the warm filesystem seed.
5. Fork the captured spore into child spores.
6. Resume the children in parallel, start PostgreSQL from each child's private
   seed, and run one RSpec shard per child.

The rootfs is read-only on resume, so the guest coordinator copies the Rails app
to `/tmp`, initializes PostgreSQL under `/tmp/sporevm-postgres`, and keeps all
runtime state in guest RAM. PostgreSQL initialization and schema load happen
once before capture, and the parent validates that Rails and RSpec eager-load.
Each child starts its own postmaster from the cleanly stopped, schema-loaded seed
and executes a fresh Ruby/Rails shard process because child commands do not
inherit the coordinator's processes.

## Prerequisites

Run commands from the root of this repository. The scripts need:

- Docker with buildx.
- The `spore` binary installed on `PATH`. If needed:
  `mise use -g github:sporevm/sporevm@latest`.

## Kernel Requirements

The full Postgres fan-out path works with SporeVM's managed run kernel in
`v0.5.0` and later. That kernel includes the normal Linux facilities
Rails/PostgreSQL needs:

```text
CONFIG_SYSVIPC=y
CONFIG_POSIX_MQUEUE=y
CONFIG_CGROUPS=y
CONFIG_NAMESPACES=y
CONFIG_SECCOMP=y
CONFIG_MULTIUSER=y
CONFIG_POSIX_TIMERS=y
CONFIG_BINFMT_SCRIPT=y
CONFIG_BLK_DEV_INITRD=y
CONFIG_VIRTIO_BLK=y
CONFIG_NETFILTER=y
CONFIG_BRIDGE=y
CONFIG_VETH=y
CONFIG_TUN=y
# CONFIG_DEVMEM is not set
CONFIG_EXT4_FS=y
CONFIG_OVERLAY_FS=y
CONFIG_KEYS=y
```

Use `--kernel` or `SPOREVM_KERNEL_IMAGE` only when testing an unreleased kernel.

## Build the OCI Image

The default path does not push to a registry and does not require SporeVM to
talk to the Docker daemon. Docker buildx writes an OCI layout tar, then
`spore rootfs import-oci` imports that layout into the local rootfs cache:

```bash
bash rails/bin/build-image
```

The script runs the equivalent of:

```bash
docker buildx build \
  --platform linux/arm64 \
  --output type=oci,dest=/tmp/sporevm-rails-rspec.oci \
  rails

spore rootfs import-oci /tmp/sporevm-rails-rspec.oci \
  --ref local/sporevm-rails-rspec:dev \
  --platform linux/arm64
```

`local/sporevm-rails-rspec:dev` is a host-local convenience ref. Import resolves
it to a digest-pinned `local/sporevm-rails-rspec@sha256:<manifest>` identity in
the rootfs cache. Captured spores still restore by the ext4 BLAKE3 artifact
digest recorded in the spore manifest; the local tag is not portable identity.

To push the image to a registry instead:

```bash
bash rails/bin/build-image \
  --push \
  --image ghcr.io/YOUR_ORG/sporevm-rails-rspec:dev
```

## Run the Fan-Out Demo

```bash
bash rails/bin/fanout-rspec --count 4
```

By default `fanout-rspec` builds and imports the local OCI layout first. If the
image has already been imported, skip that step:

```bash
bash rails/bin/fanout-rspec \
  --image local/sporevm-rails-rspec:dev \
  --no-image-build \
  --count 4
```

The script performs the product lifecycle explicitly:

```bash
spore run --image local/sporevm-rails-rspec:dev --save rails-rspec.spore --save-on USR1 -- \
  /bin/bash /usr/local/bin/sporevm-rails-coordinator

spore fork rails-rspec.spore --count 4 --out rails-rspec.children
spore fanout rails-rspec.children
```

Expected output includes the warm marker from the parent:

```text
SPOREVM_POSTGRES_SEED state=stopped duration_ms=...
SPOREVM_RAILS_READY pid=... capture_delay=2
```

and one successful RSpec completion marker per child:

```text
[000000] SPOREVM_POSTGRES_RESUME action=started status=ready duration_ms=...
[000000] SPOREVM_RSPEC_DONE parallel_index=0 parallel_count=4 exit=0
[000001] SPOREVM_RSPEC_DONE parallel_index=1 parallel_count=4 exit=0
```

The host script leaves logs and spores in its workdir so the manifest and child
output can be inspected after the run.

## Sharding

Forked child manifests contain stable generation params including
`parallel_index`, `parallel_count`, `fork_index`, `fork_count`,
`fork_batch_id`, and `vm_id`.

Inside rootfs guests, SporeVM publishes the fork generation payload to:

```text
/run/sporevm/generation.json
/run/sporevm/env
```

`/usr/local/bin/sporevm-rspec-shard` reads those helper files and splits spec
files by:

```ruby
spec_index % parallel_count == parallel_index
```

Missing identity is treated as a demo failure. Set
`SPOREVM_RSPEC_ALLOW_UNSHARDED=1` only for ad hoc image debugging outside
SporeVM, where running the full suite once is more useful than proving fan-out
identity.

## Useful Knobs

```bash
bash rails/bin/fanout-rspec --count 8 --memory 4gb
bash rails/bin/fanout-rspec --backend kvm
bash rails/bin/fanout-rspec --workdir /tmp/sporevm-rails-demo
bash rails/bin/fanout-rspec --image ghcr.io/YOUR_ORG/sporevm-rails-rspec:dev --no-image-build
bash rails/bin/fanout-rspec --kernel /path/to/sporevm-run-arm64-linux-6.1.155-Image
bash rails/bin/fanout-rspec --capture-delay 5
```

Environment equivalents:

```bash
SPORE_RAILS_IMAGE=local/sporevm-rails-rspec:dev
SPORE_RAILS_OCI=/tmp/sporevm-rails-rspec.oci
SPORE_RAILS_FANOUT_COUNT=4
SPORE_RAILS_MEMORY=2gb
SPORE_RAILS_CAPTURE_DELAY=2
```

## Docker versus Kubernetes benchmark

The public ARM64 workload image can be published as
`ghcr.io/sporevm/examples-rails-rspec`. From a clean checkout, publish the exact
source revision under its immutable tag, then resolve that tag to a manifest
digest:

```bash
revision="$(git rev-parse HEAD)"
rails/bin/build-image --push \
  --image "ghcr.io/sporevm/examples-rails-rspec:sha-${revision}"
docker buildx imagetools inspect \
  "ghcr.io/sporevm/examples-rails-rspec:sha-${revision}"
```

The publisher refuses dirty checkouts, non-ARM64 platforms, and any tag other
than `sha-<full commit>`. It builds from a `git archive` of that exact revision,
so ignored or untracked working-tree files cannot enter the image, and bakes the
revision into the OCI label and workload markers. Use the resulting
`ghcr.io/sporevm/examples-rails-rspec@sha256:...` in all commands; the benchmark
deliberately refuses tags.

The two benchmark environments may use different registry transports for the
same manifest. For example, the Buildkite worker may use a worker-local
registry while Kubernetes uses the private cluster-local registry:

```text
localhost:5000/sporevm/examples-rails-rspec@sha256:<digest>
spore-registry.sporevm-system.svc.cluster.local:5000/sporevm/examples-rails-rspec@sha256:<digest>
```

Copy the exact OCI manifest and blobs between those registries without
rebuilding or converting them, and verify the destination digest after the
copy. Schema-v3 reports retain each actual reference as `workload.imageRef`,
derive `workload.imageDigest` from it, and fail if those fields disagree. The
standalone comparator permits different transport references but requires the
same digest, source revision, platform, inventory, shard shape, sample shape,
and cache posture. Its output records both references and the shared digest.

The checked-in Buildkite pipeline can create that worker-local image, OCI
archive, provenance record, and Docker benchmark report in one ARM64 job.
Trigger the `sporevm-examples` pipeline with
`SPOREVM_RAILS_BUILD_LOCAL=1`. The job builds from the exact clean Git archive,
pushes the native ARM64 image to an ephemeral worker-local registry, exports it
with Skopeo's digest-preserving mode, verifies that the registry and OCI archive
expose the same Linux ARM64 manifest digest, and uploads:

```text
rails-rspec-linux-arm64.oci
rails-rspec-image.json
rails-rspec-buildkite.json
```

Copy `rails-rspec-linux-arm64.oci` into the cluster-local registry with a tool
that fails if the manifest digest cannot be preserved, then independently
resolve the destination before running the Kubernetes side. The temporary
worker registry, image, and exact-source build context are removed by the job
cleanup trap.

The Docker baseline is one Buildkite job on one `sporevm-linux-arm64` worker.
It prefetches the image, then starts all N independent containers concurrently
and measures that local wall itself. This is intentionally **not** Buildkite
parallelism: queue and checkout time are therefore excluded. The report records
whether the image was already present and prefetch duration separately.
Buildkite does not expose queue duration or checkout timing to job commands, so
those report fields are explicitly unavailable unless the trigger or an agent
hook supplies `SPOREVM_BUILDKITE_QUEUE_WAIT_MS` and
`SPOREVM_BUILDKITE_CHECKOUT_MS`. Record the Buildkite UI/API values alongside a
published result when those overrides are unavailable; they never enter the
headline runtime ratio.

```bash
python3 rails/bin/benchmark buildkite \
  --image localhost:5000/sporevm/examples-rails-rspec@sha256:<64-hex-digest> \
  --shards 4 --iterations 5 --warmups 1 --report /tmp/rails-buildkite.json

python3 rails/bin/benchmark k8s \
  --image spore-registry.sporevm-system.svc.cluster.local:5000/sporevm/examples-rails-rspec@sha256:<64-hex-digest> \
  --runtime-image ghcr.io/sporevm/k8s-runtime@sha256:<64-hex-digest> \
  --shards 4 --iterations 5 --warmups 1 --report /tmp/rails-k8s.json

python3 rails/bin/benchmark compare \
  --buildkite-report /tmp/rails-buildkite.json \
  --k8s-report /tmp/rails-k8s.json \
  --report /tmp/rails-comparison.json
```

Each measured iteration is one complete N-shard fanout; the identical excluded
warmup defaults to one fanout. Image prefetch is outside timing and caches stay
resident, so this is explicitly a warm-cache benchmark, not a cold-start claim.
Reports retain every measured fanout wall and nearest-rank p50/p95/p99. The
headline comparison is the median reported prepare + fork + child-execution
wall versus the median Docker fanout wall. Queue/checkout, image prefetch,
submission/admission, verifier log retrieval, and cleanup are never included in
that speed ratio.

The current fixture has two RSpec files. A run with more than two shards is a
setup-and-fanout benchmark: extra shards still start from the same prepared
Rails/PostgreSQL image but legitimately receive no spec files. Results from
this fixture therefore support a claim about repeated setup versus one-time
setup plus snapshot/fork fanout for this exact workload. They are not evidence
of general Rails suite throughput, cold-start performance, hardware-normalized
VM-versus-container performance, or broader Kubernetes/CI performance.

The Kubernetes agent must be configured with at least N execution slots and
enough memory for N concurrent 2 GiB children plus the agent. The public chart
defaults to one slot, so an N=4 benchmark deployment needs `agent.slots=4` and
appropriately sized resource requests/limits; the benchmark fails its preflight
rather than silently reducing concurrency.

`k8s` submits one source run per fanout that prepares Rails/Postgres once,
captures, forks N children, and executes concurrently. It uses the agents'
local result store with a synthetic `s3://sporevm-benchmark/...` identity. One
temporary verifier pod on the selected agent node reads terminal documents,
then removes only that run's result prefix; there is no exec or port-forward.
Every marker, child result, RuntimeReport count and source/modulo assignment is
validated. The verifier starts only after `sporectl submit` has completed and
the coordinator Job has been deleted, so agents cannot still be publishing
attempt records when the verifier removes the exact result prefix. Failure and
interruption paths delete and wait for the coordinator Job before using a
separate node-local cleanup pod. The verifier pod and exact hashed coordinator
Job/ConfigMap names are deleted before an atomic success report is written.
Defaults match the public chart; `--namespace`,
`--kubectl`, `--sporectl`, `--runtime-image`, `--result-root`, and
`--verifier-image` provide explicit deployment overrides.

Only compare successful reports with the same clean Git revision, exact image
digest, sorted nonempty inventory, shard/iteration/warmup counts, linux/arm64
platform, and cache posture; the comparator enforces all of these boundaries.
Registry hostnames may differ, but each report must use a digest-pinned
reference whose digest exactly matches its explicit `imageDigest`. The initial
topology is one ARM64 Buildkite worker versus one ARM64 Kubernetes agent/node.
Repeated measured distributions are evidence, not a universal performance
claim. The report intentionally makes no claim about cluster node CPU or memory.
Use `--require-k8s-faster` only when a gating assertion is explicitly desired.
