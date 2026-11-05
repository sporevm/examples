# Rails/Postgres RSpec Fan-Out

This example boots a Rails app once and forks the running process:

1. `spore build` builds the image from [`Dockerfile`](Dockerfile): Ruby,
   Rails, PostgreSQL, the app, and its specs.
2. `spore run` boots it. The guest starts PostgreSQL in RAM, loads the schema,
   then one Ruby process loads Rails and every spec file and serves a warm
   `GET /health`.
3. The host saves the running VM and forks N children from the save.
4. `spore fanout` resumes every child inside that same Ruby process. Each child
   reads its fork identity, serves `GET /health` against its own live
   PostgreSQL, and runs its share of the RSpec examples. Nothing is loaded
   again.

CI runs this on Linux ARM64 KVM for every build and fails if any child fails.

## Run It

Run from the root of this repository with `spore` on `PATH` (for example
`mise use -g github:sporevm/sporevm@latest`):

```bash
bash rails/bin/fanout-rspec --count 4
```

The script performs these steps:

```bash
spore build -t local/sporevm-rails-rspec:dev rails
spore run --image local/sporevm-rails-rspec:dev --save rails.spore --save-on USR1 -- \
  /bin/bash /usr/local/bin/sporevm-rails-coordinator
# after the guest prints SPOREVM_RAILS_READY: kill -USR1 <spore run pid>
spore checkpoint fork rails.spore --count 4 --out children
spore fanout children
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

It ends with per-child `first_response_ms`, a `timings:` line for the build,
boot, save, fork, and fan-out, and the workdir that keeps the save and logs.
Pass `--no-image-build` to reuse an image that is already built, or `--image`
to run a registry image built from the same Dockerfile.

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
