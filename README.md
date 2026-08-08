# SporeVM Examples

Examples for running real application workloads with
[SporeVM](https://github.com/sporevm/sporevm).

## Examples

- [Rails/Postgres RSpec fan-out](rails/README.md): build a Rails/Postgres test
  image with Docker buildx, import it into SporeVM's local rootfs cache, warm the
  VM once, capture it, fork child spores, and run RSpec shards in parallel. It
  also includes a machine-readable, digest-pinned Docker-versus-Kubernetes
  benchmark and comparator.

## Quick Start

The scripts use `spore` from `PATH`. If needed, install the latest with
`mise`:

```bash
mise use -g github:sporevm/sporevm@latest
```

Then run the examples from this repository root:

```bash
bash rails/bin/fanout-rspec --count 4
```

See the [Rails benchmark documentation](rails/README.md#docker-versus-kubernetes-benchmark)
for the immutable image publication and exact report commands. Fair comparisons
require the same image digest, clean source revision, spec inventory, shard,
iteration and warmup counts, an explicitly warm/resident cache posture, and
separate queue/admission timing. Reports call each timed N-way run a fanout
sample; shards are never represented as statistical samples.
