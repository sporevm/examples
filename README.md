# SporeVM Examples

Examples for running real application workloads with
[SporeVM](https://github.com/sporevm/sporevm).

## Examples

- [Rails/Postgres RSpec fan-out](rails/README.md): build a Rails/Postgres image
  with `spore build`, boot it once with Rails loaded, save the running VM, and
  fork children that each run an RSpec shard in the warm Ruby process.

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
