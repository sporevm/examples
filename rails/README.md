# Rails/Postgres RSpec Fan-Out

This example boots a Rails app once and forks the running process:

1. `spore build` builds the image from [`Dockerfile`](Dockerfile): Ruby,
   Rails, PostgreSQL, the app, and its specs.
2. `spore run` boots it. The guest starts PostgreSQL in RAM, loads the schema,
   then one Ruby process loads Rails and every spec file and serves a warm
   `GET /health`.
3. The host saves the running VM and forks N children from the save.
4. `spore checkpoint fanout` resumes every child inside that same Ruby process. Each child
   reads its fork identity, serves `GET /health` against its own live
   PostgreSQL, and runs its share of the RSpec examples. Nothing is loaded
   again.

The `.buildkite` pipeline runs this on Linux ARM64 KVM and fails if any child
fails.

## Run It

Use the source-built candidate from the [repository quick start](../README.md#quick-start),
then run from the root of this repository. The published v0.17.1 uses different
commands and cannot run this wrapper:

```bash
bash rails/bin/fanout-rspec --count 4
```

The script performs these steps:

```bash
spore build --tag local/sporevm-rails-rspec:dev rails
spore run --image local/sporevm-rails-rspec:dev --save-checkpoint rails.spore --save-on USR1 -- \
  /bin/bash /usr/local/bin/sporevm-rails-coordinator
# after the guest prints SPOREVM_RAILS_READY: kill -USR1 <spore run pid>
spore checkpoint fork rails.spore --count 4 --output children
spore checkpoint fanout children
```

The parent prints one ready line before the save:

```text
SPOREVM_RAILS_READY examples=3 boot_ms=... warm_request_ms=...
```

Each child prints its first response and its shard result, and the script
prefixes every fan-out line with milliseconds since the fan-out started:

```text
   412 [000000] SPOREVM_RAILS_RESPONSE parallel_index=0 parallel_count=4 status=200 widget_count=0 duration_ms=...
   498 [000000] SPOREVM_RSPEC_DONE parallel_index=0 parallel_count=4 examples=1 exit=0
```

It ends with per-child `first_response_ms` and a `timings:` line for the build,
boot, save, fork, and fan-out. `examples=` is the number of examples RSpec ran
in that child; the script fails unless the children's counts add up to the
number of examples the parent loaded. The save and logs stay in the workdir
printed at the start.

Pass `--no-image-build` to reuse a local image that is already built. With
`--image` set to a registry ref built from the same Dockerfile, the script skips
the build and `spore run` pulls the image.

## Sharding

Each child waits until SporeVM publishes its identity in `/run/sporevm/env`
(`SPORE_FORK_BATCH_ID` is set and `SPORE_GENERATION` is greater than
`SPORE_PARENT_GENERATION`), then runs every example whose position in the
sorted example IDs satisfies:

```ruby
index % SPORE_PARALLEL_JOB_COUNT == SPORE_PARALLEL_JOB
```

Children share the parent's Ruby random state; this suite does not depend on
it, but workloads that generate secrets must reseed after the fork.
