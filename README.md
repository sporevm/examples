# SporeVM Examples

Examples for running real application workloads with
[SporeVM](https://github.com/sporevm/sporevm).

## Examples

- [Rails/Postgres RSpec fan-out](rails/README.md): build a Rails/Postgres test
  image with Docker buildx, import it into SporeVM's local rootfs cache, warm the
  VM once, capture it, fork child spores, and run RSpec shards in parallel.

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
