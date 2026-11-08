# SporeVM Examples

Examples for running real application workloads with
[SporeVM](https://github.com/sporevm/sporevm).

## Examples

- [Rails/Postgres RSpec fan-out](rails/README.md): build a Rails/Postgres image
  with `spore build`, boot it once with Rails loaded, save the running VM, and
  fork children that each run an RSpec shard in the warm Ruby process.

## Quick Start

These scripts use the unreleased 0.18.0 CLI, not the published v0.17.1 CLI.
Build the exact candidate used by CI (requires Git, mise and Linux ARM64 KVM):

```bash
git clone https://github.com/sporevm/sporevm.git /tmp/sporevm-candidate
git -C /tmp/sporevm-candidate checkout a9cfff5600772fdb1ff476804b8e05cd476dbf45
mise trust /tmp/sporevm-candidate/mise.toml
mise -C /tmp/sporevm-candidate exec zig@0.16.0 -- zig build --release=safe
export PATH="/tmp/sporevm-candidate/zig-out/bin:$PATH"
```

Then run the examples from this repository root:

```bash
bash rails/bin/fanout-rspec --count 4
```

CI pins that source commit in `.buildkite/rails-fanout.sh`; it does not download
an unpublished 0.18.0 release or silently test against 0.17.1. Switch to a
compatible release archive and verified checksum only after publication.
