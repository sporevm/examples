require "json"
require "fileutils"
require "minitest/autorun"
require "open3"
require "tmpdir"

SHARD_PATH = ENV.fetch(
  "SPOREVM_RSPEC_SHARD_PATH",
  File.expand_path("../rails/image/bin/sporevm-rspec-shard", __dir__)
)
load SHARD_PATH

class SporevmRspecShardTest < Minitest::Test
  REVISION = "f" * 40

  def setup
    @original_cwd = Dir.pwd
    @original_env = ENV.to_h
  end

  def teardown
    ENV.replace(@original_env)
    Dir.chdir(@original_cwd)
  end

  def populate_context_env
    CHILD_CONTEXT_FIXED_ENV.each { |key, value| ENV[key] = value }
    ENV["SPOREVM_SOURCE_REVISION"] = REVISION
    ENV["SPOREVM_POSTGRES_BIN"] = "/usr/bin"
    %w[
      SPOREVM_RAILS_INITDB_MS
      SPOREVM_RAILS_POSTGRES_MS
      SPOREVM_RAILS_SCHEMA_MS
      SPOREVM_RAILS_BEFORE_PRELOAD_MS
    ].each { |key| ENV[key] = "1" }
  end

  def assert_rejected(message = /invalid Rails child context/)
    _, stderr = capture_io do
      error = assert_raises(SystemExit) { yield }
      assert_equal 1, error.status
    end
    assert_match message, stderr
  end

  def test_context_round_trip_is_strict_and_mode_0600
    Dir.mktmpdir do |directory|
      populate_context_env
      assert_equal File.join("/usr/local/bundle/ruby", RbConfig::CONFIG.fetch("ruby_version")), ENV.fetch("GEM_HOME")
      Dir.chdir(directory)
      path = File.join(directory, "child-context.json")
      expected = CHILD_CONTEXT_ENV.to_h { |key| [key, ENV.fetch(key)] }

      persist_child_context(path, expected_cwd: directory)

      assert_equal 0o600, File.stat(path).mode & 0o777
      assert_empty Dir.glob("#{path}.tmp-*")
      CHILD_CONTEXT_ENV.each { |key| ENV.delete(key) }
      Dir.chdir("/")

      load_child_context(path, expected_cwd: directory)

      assert_equal directory, Dir.pwd
      assert_equal expected, CHILD_CONTEXT_ENV.to_h { |key| [key, ENV.fetch(key)] }
    end
  end

  def test_context_rejects_schema_shape_environment_and_cwd_changes
    Dir.mktmpdir do |directory|
      populate_context_env
      context = child_context_payload(cwd: directory)
      mutations = [
        ->(value) { value["schema"] = "wrong" },
        ->(value) { value["extra"] = true },
        ->(value) { value["environment"]["EXTRA"] = "value" },
        ->(value) { value["environment"].delete("SPOREVM_SOURCE_REVISION") },
        ->(value) { value["environment"]["SPOREVM_SOURCE_REVISION"] = "not-a-revision" },
        ->(value) { value["environment"]["SPOREVM_POSTGRES_BIN"] = "../bin" },
        ->(value) { value["environment"]["SPOREVM_RAILS_INITDB_MS"] = "-1" },
        ->(value) { value["environment"]["PATH"] = "/unexpected" },
        ->(value) { value["cwd"] = "/unexpected" }
      ]

      mutations.each do |mutate|
        changed = Marshal.load(Marshal.dump(context))
        mutate.call(changed)
        assert_rejected { validate_child_context(changed, expected_cwd: directory) }
      end
    end
  end

  def test_context_load_rejects_malformed_json_and_unavailable_cwd
    Dir.mktmpdir do |directory|
      path = File.join(directory, "child-context.json")
      File.write(path, "{")
      assert_rejected(/could not load/) { load_child_context(path, expected_cwd: directory) }

      populate_context_env
      missing = File.join(directory, "missing")
      File.write(path, JSON.generate(child_context_payload(cwd: missing)))
      assert_rejected(/could not load/) { load_child_context(path, expected_cwd: missing) }
    end
  end

  def test_argument_modes_are_unambiguous
    assert_equal [2, false], parse_args(["--capture-delay", "2"])
    assert_equal [nil, false], parse_args([])
    assert_equal [nil, true], parse_args([CHILD_CONTEXT_REEXEC_ARG])

    [
      ["--capture-delay", "2", CHILD_CONTEXT_REEXEC_ARG],
      [CHILD_CONTEXT_REEXEC_ARG, CHILD_CONTEXT_REEXEC_ARG],
      ["--unknown"]
    ].each do |arguments|
      _, stderr = capture_io do
        error = assert_raises(SystemExit) { parse_args(arguments) }
        assert_equal 1, error.status
      end
      refute_empty stderr
    end
  end

  def test_command_detail_is_bounded_and_single_line
    assert_equal "one two", command_detail("one\ntwo")
    assert_equal 512, command_detail("x" * 600).bytesize
    assert_equal "unavailable", command_detail(" \n ")
  end

  def test_postgres_ready_requires_exact_database_identity
    Dir.mktmpdir do |directory|
      File.write(File.join(directory, "pg_isready"), "#!/bin/sh\nexit 0\n")
      psql = File.join(directory, "psql")
      File.write(psql, "#!/bin/sh\nprintf '%s\\n' '#{POSTGRES_DATABASE}|#{POSTGRES_DATA}'\n")
      FileUtils.chmod(0o755, [File.join(directory, "pg_isready"), psql])
      ENV["SPOREVM_POSTGRES_BIN"] = directory
      assert postgres_ready?

      File.write(psql, "#!/bin/sh\nprintf 'wrong|database\\n'\n")
      FileUtils.chmod(0o755, psql)
      refute postgres_ready?
    end
  end

  def test_postgres_probe_timeout_terminates_its_process_group
    started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
    _, stderr, status = bounded_command_output(
      ["/bin/sh", "-c", "sleep 10"],
      timeout_seconds: 0.1
    )
    elapsed = Process.clock_gettime(Process::CLOCK_MONOTONIC) - started

    refute status&.success?
    assert_includes stderr, "command timed out"
    assert_operator elapsed, :<, 2
  end

  def test_postgres_process_scan_detects_the_expected_executable
    Dir.mktmpdir do |directory|
      postgres = File.join(directory, "postgres")
      other = File.join(directory, "other")
      FileUtils.touch(postgres)
      FileUtils.touch(other)
      FileUtils.chmod(0o755, postgres)
      ENV["SPOREVM_POSTGRES_BIN"] = directory
      proc_root = File.join(directory, "proc")
      FileUtils.mkdir_p(File.join(proc_root, "123"))
      File.write(File.join(proc_root, "123", "comm"), "postgres\n")
      File.symlink(postgres, File.join(proc_root, "123", "exe"))
      FileUtils.mkdir_p(File.join(proc_root, "124"))
      File.write(File.join(proc_root, "124", "comm"), "unrelated\n")
      File.symlink(postgres, File.join(proc_root, "124", "exe"))
      FileUtils.mkdir_p(File.join(proc_root, "125"))
      File.write(File.join(proc_root, "125", "comm"), "postgres\n")
      File.symlink(other, File.join(proc_root, "125", "exe"))
      FileUtils.mkdir_p(File.join(proc_root, "126"))
      File.write(File.join(proc_root, "126", "comm"), "postgres\n")
      assert_equal [123], postgres_process_pids(proc_root: proc_root)
    end
  end

  def test_postgres_process_scan_fails_closed_on_inaccessible_candidate
    Dir.mktmpdir do |directory|
      postgres = File.join(directory, "postgres")
      FileUtils.touch(postgres)
      FileUtils.chmod(0o755, postgres)
      ENV["SPOREVM_POSTGRES_BIN"] = directory
      proc_root = File.join(directory, "proc")
      process_executable = File.join(proc_root, "123", "exe")
      FileUtils.mkdir_p(File.dirname(process_executable))
      File.write(File.join(proc_root, "123", "comm"), "postgres\n")
      realpath = File.method(:realpath)

      _, stderr = capture_io do
        error = assert_raises(SystemExit) do
          File.stub(:realpath, ->(path) { raise Errno::EACCES, path if path == process_executable; realpath.call(path) }) do
            postgres_process_pids(proc_root: proc_root)
          end
        end
        assert_equal 1, error.status
      end
      assert_includes stderr, "cannot inspect PostgreSQL processes"
    end
  end

  def test_postgres_resume_refuses_a_live_process_without_touching_locks
    Dir.mktmpdir do |directory|
      marker = File.join(directory, "pg-ctl-ran")
      postgres = File.join(directory, "postgres")
      FileUtils.touch(postgres)
      FileUtils.chmod(0o755, postgres)
      File.write(File.join(directory, "pg_ctl"), "#!/bin/sh\ntouch '#{marker}'\n")
      FileUtils.chmod(0o755, File.join(directory, "pg_ctl"))
      ENV["SPOREVM_POSTGRES_BIN"] = directory
      proc_root = File.join(directory, "proc")
      FileUtils.mkdir_p(File.join(proc_root, "123"))
      File.write(File.join(proc_root, "123", "comm"), "postgres\n")
      File.symlink(postgres, File.join(proc_root, "123", "exe"))
      FileUtils.mkdir_p(POSTGRES_SOCKET_DIR)
      lock = File.join(POSTGRES_SOCKET_DIR, ".s.PGSQL.5432.lock")
      File.write(lock, "preserve")

      _, stderr = capture_io do
        error = assert_raises(SystemExit) { resume_postgres(proc_root: proc_root) }
        assert_equal 1, error.status
      end
      assert_includes stderr, "live but unavailable"
      refute File.exist?(marker)
      assert_equal "preserve", File.read(lock)
    ensure
      FileUtils.rm_rf(File.dirname(POSTGRES_DATA))
    end
  end

  def test_image_child_reexec_starts_ruby_with_restored_context
    skip "requires the built workload image" unless SHARD_PATH == "/usr/local/bin/sporevm-rspec-shard"

    FileUtils.rm_rf(CHILD_CONTEXT_FIXED_ENV.fetch("SPOREVM_RAILS_RUNTIME_ROOT"))
    FileUtils.rm_rf(File.dirname(POSTGRES_DATA))
    FileUtils.mkdir_p([CHILD_CONTEXT_CWD, CHILD_CONTEXT_FIXED_ENV.fetch("HOME")])
    fake_postgres_bin = File.join(File.dirname(POSTGRES_DATA), "fake-bin")
    FileUtils.mkdir_p(fake_postgres_bin)
    File.write(File.join(fake_postgres_bin, "postgres"), "#!/bin/sh\nexit 0\n")
    File.write(File.join(fake_postgres_bin, "pg_ctl"), "#!/bin/sh\nexit 0\n")
    File.write(File.join(fake_postgres_bin, "pg_isready"), "#!/bin/sh\nexit 0\n")
    File.write(File.join(fake_postgres_bin, "psql"), "#!/bin/sh\nprintf '%s\\n' '#{POSTGRES_DATABASE}|#{POSTGRES_DATA}'\n")
    FileUtils.chmod(0o755, %w[postgres pg_ctl pg_isready psql].map { |name| File.join(fake_postgres_bin, name) })
    File.write(File.join(CHILD_CONTEXT_CWD, "Gemfile"), "source \"https://rubygems.org\"\n")
    populate_context_env
    ENV["SPOREVM_POSTGRES_BIN"] = fake_postgres_bin
    Dir.chdir(CHILD_CONTEXT_CWD)
    persist_child_context(CHILD_CONTEXT)

    environment = {
      "SPORE_PARALLEL_JOB" => "3",
      "SPORE_PARALLEL_JOB_COUNT" => "4"
    }
    stdout, stderr, status = Open3.capture3(
      environment,
      CHILD_RUBY,
      SHARD_PATH,
      unsetenv_others: true
    )

    assert status.success?, stderr
    assert_includes stdout, "SPOREVM_POSTGRES_RESUME action=started status=ready"
    assert_includes stdout, "SPOREVM_RSPEC_SHARD parallel_index=3 parallel_count=4 selected=0"
    assert_includes stdout, "source_revision=#{REVISION}"
    assert_includes stdout, "SPOREVM_RSPEC_DONE parallel_index=3 parallel_count=4 exit=0"
  ensure
    if SHARD_PATH == "/usr/local/bin/sporevm-rspec-shard"
      FileUtils.rm_rf(CHILD_CONTEXT_FIXED_ENV.fetch("SPOREVM_RAILS_RUNTIME_ROOT"))
      FileUtils.rm_rf(CHILD_CONTEXT_FIXED_ENV.fetch("HOME"))
      FileUtils.rm_rf(File.dirname(POSTGRES_DATA))
    end
  end
end
