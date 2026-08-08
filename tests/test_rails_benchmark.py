import argparse, base64, copy, hashlib, importlib.machinery, importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest import mock

PATH=Path(__file__).parents[1]/"rails/bin/benchmark"
loader=importlib.machinery.SourceFileLoader("rails_benchmark",str(PATH)); spec=importlib.util.spec_from_loader(loader.name,loader)
bench=importlib.util.module_from_spec(spec); loader.exec_module(bench)
REV="f"*40

def marker(index=0,count=1,paths=("spec/a_spec.rb",),revision=REV):
    digest=bench.path_digest(paths)
    return ("SPOREVM_RAILS_WARM_TIMING initdb_ms=1 postgres_ms=2 schema_ms=3 before_preload_ms=9\n"
            f"SPOREVM_RSPEC_SHARD parallel_index={index} parallel_count={count} selected={len(paths)} selected_sha256={digest} source_revision={revision} identity_source=x\n"
            f"SPOREVM_RSPEC_DONE parallel_index={index} parallel_count={count} exit=0 duration_ms=12 source_revision={revision}\n")
def terminal(child=0,run="run",text=None,**output):
    raw=(text or marker()).encode(); out={"stdoutBytes":len(raw),"stdoutPreviewBase64":base64.b64encode(raw).decode(),"stderrBytes":0,"stderrPreviewBase64":"",**output}
    obj={"runID":run,"childID":child,"attemptID":f"{run}-child-{child}-attempt-01","status":"succeeded","terminal":True,"output":out}
    return "SPOREVM_TERMINAL_JSON_BASE64 "+base64.b64encode(json.dumps(obj).encode()).decode()
def runtime(run="run",n=2):
    s={"runID":run,"state":"succeeded","childCount":n,"shardCount":1,"assignedChildren":n,"assignedShards":1,"attemptCount":n,"completedChildren":n,"succeededChildren":n,"failedChildren":0,"skippedTerminalChildren":0,"platformMismatches":0,"nonTerminalFailures":0,"leaseErrors":0,"startedAt":"2026-01-01T00:00:00Z","finishedAt":"2026-01-01T00:00:01Z"}
    return {"plan":{"summary":{"state":"admitted"}},"summary":s,"prepare":{"childCount":n,"timingsMs":{"runSave":500,"fork":100}}}

class BenchmarkTest(unittest.TestCase):
    def test_marker_strictness_and_assignment(self):
        got=bench.parse_markers(marker(),0,1,REV); bench.validate_assignment(got,["spec/a_spec.rb"],0,1,REV)
        for bad in [marker()+marker(),marker().replace("duration_ms=12","duration_ms=-1"),marker().replace("parallel_index=0","parallel_index=1",1),marker().replace("source_revision="+REV,"source_revision=x"),marker().replace("selected=1","selected")]:
            with self.subTest(bad=bad[-80:]),self.assertRaises(ValueError): bench.parse_markers(bad,0,1,REV)
    def test_empty_inventory_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,"empty"): bench.inventory(Path(d))
    def test_inventory_and_empty_shard(self):
        paths=["spec/a_spec.rb","spec/b_spec.rb"]
        self.assertEqual(bench.shard_paths(paths,2,3),[])
        self.assertEqual(bench.path_digest(paths),hashlib.sha256(b"spec/a_spec.rb\nspec/b_spec.rb\n").hexdigest())
    def test_nearest_rank(self):
        self.assertEqual(bench.percentiles(list(range(1,101))),{"p50":50,"p95":95,"p99":99})
    def test_optional_external_buildkite_timings(self):
        with mock.patch.dict(bench.os.environ,{"SPOREVM_BUILDKITE_QUEUE_WAIT_MS":"12.5"},clear=True):
            self.assertEqual(bench.optional_ms_env("SPOREVM_BUILDKITE_QUEUE_WAIT_MS"),12.5)
            self.assertIsNone(bench.optional_ms_env("SPOREVM_BUILDKITE_CHECKOUT_MS"))
        for value in ("bad","-1","nan","inf"):
            with self.subTest(value=value),mock.patch.dict(bench.os.environ,{"X":value},clear=True),self.assertRaises(ValueError): bench.optional_ms_env("X")
    def test_runtime_validation(self):
        self.assertEqual(bench.validate_runtime_report(runtime(),"run",2),{"executionMs":1000,"prepareMs":600,"workloadWallMs":1600})
        for key,value in [("runID","other"),("failedChildren",1),("attemptCount",1),("finishedAt","2025-01-01T00:00:00Z")]:
            bad=runtime(); bad["summary"][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): bench.validate_runtime_report(bad,"run",2)
    def test_runtime_extraction_mixed(self):
        raw=json.dumps(runtime("r",1)); self.assertEqual(bench.extract_runtime_report("noise\n"+raw+"\nnoise")["summary"]["runID"],"r")
        with self.assertRaises(ValueError): bench.extract_runtime_report(raw+raw)
    def test_terminals_mixed_duplicate_missing_truncated(self):
        good="noise\n"+terminal()+"\nother"
        self.assertEqual(len(bench.parse_terminal_results(good,"run",1,["spec/a_spec.rb"],REV)),1)
        with self.assertRaises(ValueError): bench.parse_terminal_results(good+"\n"+terminal(),"run",1,["spec/a_spec.rb"],REV)
        with self.assertRaises(ValueError): bench.parse_terminal_results("noise","run",1,["spec/a_spec.rb"],REV)
        with self.assertRaises(ValueError): bench.parse_terminal_results(terminal(stdoutTruncated=True),"run",1,["spec/a_spec.rb"],REV)
        with self.assertRaises(ValueError): bench.parse_terminal_results(terminal(stdoutBytes=999),"run",1,["spec/a_spec.rb"],REV)
    def test_kubernetes_names(self):
        raw="Run_ID"; self.assertEqual(bench.kubernetes_name("run",raw),"run-run-id-"+hashlib.sha256(raw.encode()).hexdigest()[:8])
        self.assertLessEqual(len(bench.kubernetes_name("spore-coordinator","x"*100)),63)
    def test_result_reader_and_cleanup_pods_are_post_run_operations(self):
        args=argparse.Namespace(namespace="sporevm-system",verifier_image=None,runtime_image="example.com/runtime@sha256:"+"a"*64,result_root="/var/lib/sporevm/results",shards=4)
        reader=bench.result_pod_manifest("reader","node",args,"bucket","prefix/run",True)
        script=reader["spec"]["containers"][0]["command"][2]
        self.assertNotIn("while",script)
        self.assertIn("attempts=",script)
        self.assertLess(script.index("SPOREVM_TERMINAL_JSON_BASE64"),script.index("rm -rf"))
        cleanup=bench.result_pod_manifest("cleanup","node",args,"bucket","prefix/run",False)
        cleanup_script=cleanup["spec"]["containers"][0]["command"][2]
        self.assertIn('rm -rf -- "$target"',cleanup_script)
        self.assertNotIn("SPOREVM_TERMINAL_JSON_BASE64",cleanup_script)
    def test_k8s_deletes_coordinator_before_reading_and_removing_results(self):
        paths=["spec/a_spec.rb"]
        workload={"revision":REV,"sourceDirty":False,"image":"example.com/workload@sha256:"+"b"*64,"inventory":{"count":1,"sha256":bench.path_digest(paths),"paths":paths},"shardCount":1,"iterations":1,"warmups":1,"platform":"linux/arm64","cachePosture":bench.CACHE_POSTURE}
        base={"schema":bench.SCHEMA,"schemaVersion":2,"createdAt":bench.now(),"success":False,"workload":workload}
        provenance={"identity":{"podUID":"uid","nodeName":"node"},"report":{"configuredRuntimeImage":"runtime","runtimeImageID":"runtime@sha256:"+"c"*64,"architecture":"arm64","executionSlots":1,"healthy":True,"sporeVMVersion":None,"versionVerification":"not exposed by public agent status"}}
        events=[]; current={"run":None}
        def fake_run(command,**_kwargs):
            if command[0]=="sporectl":
                run_id=json.loads(Path(command[-1]).read_text())["runID"]
                current["run"]=run_id; events.append((run_id,"submit"))
                return bench.subprocess.CompletedProcess(command,0,stdout=json.dumps(runtime(run_id,1)),stderr="")
            if command[1:3]==["delete","job"]: events.append((current["run"],"delete-job"))
            if command[1:3]==["apply","-f"]:
                manifest=json.loads(Path(command[3]).read_text()); name=manifest["metadata"]["name"]
                events.append((current["run"],"apply-reader" if "verifier" in name else "apply-cleanup"))
            return bench.subprocess.CompletedProcess(command,0)
        def fake_check_output(command,**_kwargs):
            if command[1]=="logs": return terminal(run=current["run"])+"\n"
            raise AssertionError(command)
        with tempfile.TemporaryDirectory() as d:
            args=argparse.Namespace(image=workload["image"],shards=1,iterations=1,warmups=1,report=str(Path(d)/"report.json"),allow_dirty=False,runtime_image="example.com/runtime@sha256:"+"d"*64,verifier_image=None,sporectl="sporectl",kubectl="kubectl",namespace="sporevm-system",result_root="/var/lib/sporevm/results",timeout=30)
            with mock.patch.object(bench,"common",return_value=copy.deepcopy(base)),mock.patch.object(bench,"agent_provenance",return_value=provenance),mock.patch.object(bench.subprocess,"run",side_effect=fake_run),mock.patch.object(bench.subprocess,"check_output",side_effect=fake_check_output):
                bench.k8s(args)
            self.assertTrue(Path(args.report).is_file())
        run_ids=[run_id for run_id,event in events if event=="submit"]
        self.assertEqual(len(run_ids),2)
        for run_id in run_ids:
            ordered=[event for candidate,event in events if candidate==run_id]
            self.assertLess(ordered.index("submit"),ordered.index("delete-job"))
            self.assertLess(ordered.index("delete-job"),ordered.index("apply-reader"))
            self.assertNotIn("apply-cleanup",ordered)
    def test_k8s_failure_stops_coordinator_before_result_cleanup(self):
        paths=["spec/a_spec.rb"]
        workload={"revision":REV,"sourceDirty":False,"image":"example.com/workload@sha256:"+"b"*64,"inventory":{"count":1,"sha256":bench.path_digest(paths),"paths":paths},"shardCount":1,"iterations":1,"warmups":1,"platform":"linux/arm64","cachePosture":bench.CACHE_POSTURE}
        base={"schema":bench.SCHEMA,"schemaVersion":2,"createdAt":bench.now(),"success":False,"workload":workload}
        provenance={"identity":{"podUID":"uid","nodeName":"node"},"report":{"architecture":"arm64"}}
        events=[]
        def fake_run(command,**_kwargs):
            if command[0]=="sporectl": events.append("submit"); return bench.subprocess.CompletedProcess(command,1,stdout="",stderr="failed")
            if command[1:3]==["delete","job"]: events.append("delete-job")
            if command[1:3]==["apply","-f"]: events.append("apply-cleanup")
            return bench.subprocess.CompletedProcess(command,0)
        with tempfile.TemporaryDirectory() as d:
            args=argparse.Namespace(image=workload["image"],shards=1,iterations=1,warmups=1,report=str(Path(d)/"report.json"),allow_dirty=False,runtime_image="example.com/runtime@sha256:"+"d"*64,verifier_image=None,sporectl="sporectl",kubectl="kubectl",namespace="sporevm-system",result_root="/var/lib/sporevm/results",timeout=30)
            with mock.patch.object(bench,"common",return_value=copy.deepcopy(base)),mock.patch.object(bench,"agent_provenance",return_value=provenance),mock.patch.object(bench.subprocess,"run",side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError,"sporectl submit"):
                    bench.k8s(args)
            self.assertFalse(Path(args.report).exists())
        self.assertLess(events.index("submit"),events.index("delete-job"))
        self.assertLess(events.index("delete-job"),events.index("apply-cleanup"))
    def reports(self):
        paths=["spec/a_spec.rb"]
        workload={"revision":"a"*40,"sourceDirty":False,"image":"example.com/workload@sha256:"+"b"*64,"inventory":{"count":1,"sha256":bench.path_digest(paths),"paths":paths},"shardCount":2,"iterations":3,"warmups":1,"platform":"linux/arm64","cachePosture":bench.CACHE_POSTURE}
        a_samples=[10,30,20]; b_samples=[5,15,10]
        def shards(run_id=None,docker=False):
            rows=[]
            for index in range(2):
                selected=bench.shard_paths(paths,index,2)
                row={"index":index,"selectedCount":len(selected),"selectedSha256":bench.path_digest(selected),"executionMs":1,"sourceRevision":workload["revision"]}
                if docker: row.update(processWallMs=2,exitCode=0)
                else: row.update(childID=index,attemptID=f"{run_id}-child-{index}-attempt-01")
                rows.append(row)
            return rows
        a_fanouts=[{"phase":"warmup" if index==0 else "measured","fanoutWallMs":wall,"shards":shards(docker=True)} for index,wall in enumerate([11,*a_samples])]
        a={"schema":bench.SCHEMA,"schemaVersion":2,"kind":"buildkite-docker","success":True,"workload":workload,"system":{"platform":"linux/arm64","architecture":"arm64"},"fanouts":a_fanouts,"timing":{"fanoutWallSamplesMs":a_samples,"fanoutWallPercentilesMs":bench.percentiles(a_samples),"excludedWarmupFanoutWallSamplesMs":[11]}}
        records=[]
        for index,wall in enumerate([6,*b_samples]):
            run_id=f"rails-bench-{index:032x}"
            records.append({"runID":run_id,"phase":"warmup" if index==0 else "measured","fanoutWallMs":wall,"prepareWallMs":2,"coordinatorRunWallMs":wall-2,"submissionWallMs":4+index,"prepare":{"childCount":2,"timingsMs":{"runSave":1,"fork":1}},"shards":shards(run_id=run_id)})
        runtime_digest="example.com/runtime@sha256:"+"c"*64
        b={"schema":bench.SCHEMA,"schemaVersion":2,"kind":"kubernetes-sporevm","success":True,"workload":copy.deepcopy(workload),"coordinatorRuntimeImage":runtime_digest,"agentProvenance":{"architecture":"arm64","runtimeImageID":runtime_digest,"executionSlots":2,"healthy":True},"cleanup":{"success":True},"iterations":records,"timing":{"fanoutWallSamplesMs":b_samples,"fanoutWallPercentilesMs":bench.percentiles(b_samples),"excludedWarmupFanoutWallSamplesMs":[6],"submissionWallSamplesMs":[5,6,7]}}
        return a,b
    def test_comparison_raw_and_median(self):
        got=bench.comparison(*self.reports()); self.assertEqual(got["kubernetesToBuildkiteRatio"],.5); self.assertEqual(got["buildkite"]["medianMs"],20)
    def test_every_compatibility_field(self):
        for key in ("revision","sourceDirty","image","inventory","shardCount","iterations","warmups","platform","cachePosture"):
            a,b=self.reports(); b["workload"][key]="different"
            with self.subTest(key=key),self.assertRaises(ValueError): bench.comparison(a,b)
    def test_comparison_rejects_equally_malformed_provenance(self):
        for mutate in (
            lambda a,b: (a["workload"].pop("revision"),b["workload"].pop("revision")),
            lambda a,b: (a["workload"].update(sourceDirty=True),b["workload"].update(sourceDirty=True)),
            lambda a,b: (a["workload"]["inventory"].update(sha256="0"*64),b["workload"]["inventory"].update(sha256="0"*64)),
            lambda a,b: (a["timing"].update(fanoutWallSamplesMs=[1]),b["timing"].update(fanoutWallSamplesMs=[1])),
        ):
            a,b=self.reports(); mutate(a,b)
            with self.assertRaises(ValueError): bench.comparison(a,b)
    def test_comparison_requires_complete_fanout_evidence(self):
        mutations=(
            lambda a,b: a["fanouts"][0]["shards"].pop(),
            lambda a,b: b["iterations"][1].update(fanoutWallMs=99),
            lambda a,b: b["iterations"][0]["shards"][0].update(attemptID="wrong"),
            lambda a,b: b.update(coordinatorRuntimeImage="example.com/runtime:latest"),
        )
        for mutate in mutations:
            a,b=self.reports(); mutate(a,b)
            with self.assertRaises(ValueError): bench.comparison(a,b)
    def test_atomic_write_creates_parent_and_no_temp(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"nested/report.json"; bench.atomic_write(p,{"ok":True})
            self.assertEqual(json.loads(p.read_text()),{"ok":True}); self.assertEqual(list(p.parent.glob(".report.json.*")),[])

if __name__=="__main__": unittest.main()
