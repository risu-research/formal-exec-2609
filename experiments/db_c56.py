#!/usr/bin/env python3
"""Balanced startup first-query and statistics-cache priming crossover.

Pre-registered 3 prime policies x 6 table orders; frozen active part manifests;
D4 unchanged; cold process restart is not called stats-only cache clearing.
"""
import csv,itertools,json,os,pathlib,random,statistics
from collections import defaultdict
os.environ["C52_LAYOUT"]="monthly"
os.environ["C53_BATCHES"]="1"
REPLICATE=int(os.environ.get("C56_REPLICATE","0"))
if REPLICATE not in (0,1):raise ValueError("replicate must be 0 or 1")
import db_c54 as F

B=F.B
T=F.T
N=F.N
assert N==1
R=pathlib.Path("s56_rep"+str(REPLICATE))
R.mkdir(exist_ok=True)
F.R=R;F.G.R=R;T.R=R;B.R=R;B.P.R=R;B.S.ROOT=R;B.S.base.ROOT=R
B.S.base.VERSIONS=[B.S.OLD,B.S.OLD]
B.S.CT="c56_"+str(REPLICATE)+"_ch";B.S.VOL="c56_"+str(REPLICATE)+"_vol"
B.RUNS=9
TABLES=("ref","hist","fresh")
PRIMES=("cache_on","stats_off","cache_off")
STEPS=("prime","probe","repeat","bypass","off")
Q=F.Q
SEED=550061
assert Q=="D4_keys_and_line_prices"

def schedule(phase):
    # Short, internally position-balanced startup block. Complementary orders
    # in the second independent runner complete all six permutations per arm.
    cyclic=(TABLES,TABLES[1:]+TABLES[:1],TABLES[2:]+TABLES[:2])
    orders=cyclic if REPLICATE==0 else tuple(tuple(reversed(o)) for o in cyclic)
    plan=[(arm,order) for arm in PRIMES for order in orders]
    random.Random(SEED+REPLICATE).shuffle(plan)
    for arm in PRIMES:
        for table in TABLES:
            positions=[order.index(table) for p,order in plan if p==arm]
            assert sorted(positions)==[0,1,2]
    return plan

def events(phase,cycle):
    # Persist each cycle before the next restart; ClickHouse query_log entries
    # may be lost if the server is killed before a later final flush.
    B.sql("SYSTEM FLUSH LOGS")
    raw=B.sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
        "FROM system.query_log WHERE type='QueryFinish' "
        "AND startsWith(query_id,'c56_"+phase+"_"+str(cycle)+"_') FORMAT JSONEachRow")
    entries={}
    for line in raw.splitlines():
        if line:
            r=json.loads(line)
            if r["query_id"] in entries:raise RuntimeError("DUPLICATE_RECEIPT")
            entries[r["query_id"]]=r
    return entries

def median(vals):
    return statistics.median(vals)

def boot(xs,seed):
    n=len(xs)
    rng=random.Random(seed)
    samples=sorted(median([xs[rng.randrange(n)] for _ in range(n)])
                   for i in range(1600))
    return [samples[39],samples[1559]]

def calculate(phase,rows,plan):
    group=defaultdict(list)
    for r in rows:group[(r["prime_arm"],r["table"],r["step"])].append(r)
    summary=[]
    for arm in PRIMES:
        for t in TABLES:
            for step in STEPS:
                a=group[(arm,t,step)]
                assert len(a)==3
                summary.append({"prime_arm":arm,"table":t,"step":step,
                   "n":len(a),"ms":median([x["ms"] for x in a]),
                   "stats_ms":median([x["stat_us"]/1000 for x in a]),
                   "read_rows":median([x["read_rows"] for x in a]),
                   "file_open":median([x["file_open"] for x in a]),
                   "os_read_bytes":median([x["os_read_bytes"] for x in a])})
    decisions=[]
    for arm in PRIMES:
        for step in STEPS:
            baseline=next(x for x in summary if x["prime_arm"]==arm and x["step"]==step and x["table"]=="ref")
            for target in ("hist","fresh"):
                effect=next(x for x in summary if x["prime_arm"]==arm and x["step"]==step and x["table"]==target)
                d=effect["ms"]-baseline["ms"]
                ratio=effect["ms"]/max(1,baseline["ms"])
                decisions.append({"prime_arm":arm,"step":step,
                  "comparison":target+"_vs_ref",
                  "baseline_ms":baseline["ms"],"target_ms":effect["ms"],
                  "difference_ms":d,"ratio":ratio,
                  "material_30ms_and_10pct":d>=30 and ratio>=1.1,
                  "loaded_stats_difference_ms":effect["stats_ms"]-baseline["stats_ms"],
                  "read_rows_difference":effect["read_rows"]-baseline["read_rows"]})
    # Cross-over priming comparison uses fully balanced table positions.
    primes=[]
    for t in TABLES:
        for step in ("prime","probe"):
            for arm in PRIMES:
                x=next(z for z in summary if z["table"]==t and z["step"]==step and z["prime_arm"]==arm)
                primes.append(x)
    B.dump(phase+"/profile_summary.json",summary)
    B.dump(phase+"/decisions.json",decisions)
    B.dump(phase+"/startup_policy_contrasts.json",primes)
    B.log("C56_DECISIONS",phase,[(d["prime_arm"],d["step"],d["comparison"],
          round(d["difference_ms"],2),round(d["ratio"],3),
          d["material_30ms_and_10pct"]) for d in decisions
          if d["comparison"]=="fresh_vs_ref" and d["step"] in ("prime","probe","repeat")])
    return {"records":len(rows),"n_resets":len(plan),
      "balanced_startup_order":True,"original_and_D4_outputs_equal":True,
      "part_manifests_unchanged":True}

def persistently_freeze_background_merges(phase):
    # Existing SYSTEM STOP MERGES is a transient process setting. It may not
    # prevent a merge in the brief startup window after a process restart.
    # A persistent per-table max_parts_to_merge_at_once=0 setting explicitly
    # disables background MergeTree merge selection until this checkpoint ends.
    # This is applied only AFTER natural ingestion and idle maintenance.
    raw=B.sql("SELECT value FROM system.merge_tree_settings "
              "WHERE name='max_parts_to_merge_at_once' FORMAT TSV").strip()
    if not raw.isdigit():raise RuntimeError("MERGETREE_FREEZE_DEFAULT_UNKNOWN "+raw)
    previous=int(raw)
    if previous<=0:raise RuntimeError("MERGETREE_UNEXPECTED_DEFAULT "+raw)
    before=F.physical()
    for table in B.TABLES:
        B.sql("ALTER TABLE ssb."+table+
              " MODIFY SETTING max_parts_to_merge_at_once=0")
    # No manual OPTIMIZE and no changes to data content or part inventory.
    after=F.physical()
    if before!=after:raise RuntimeError("MERGETREE_SETTING_ALTERED_PHYSICAL_PARTS")
    count=int(B.sql("SELECT count() FROM system.merges "
               "WHERE database='ssb' AND table IN ('ref','hist','legacy','fresh') "
               "FORMAT TSV").strip())
    if count:raise RuntimeError("ACTIVE_MERGE_AT_CHECKPOINT "+str(count))
    metadata={'phase':phase,'merge_tree_setting':'max_parts_to_merge_at_once',
        'checkpoint_value':0,'default_prior_value':previous,
        'frozen_parts':after['parts'],'baseline_checksum':after['hash'],
        'note':'persistent setting applied only during evaluation checkpoint'}
    B.log("C56_MERGE_FREEZE",metadata)
    return metadata

def restore_background_merges(metadata):
    old=metadata['default_prior_value']
    for table in B.TABLES:
        B.sql("ALTER TABLE ssb."+table+
              " MODIFY SETTING max_parts_to_merge_at_once="+str(old))
    B.log("C56_MERGE_RESTORED",old)

def measure(phase,tables,qs,reps):
    if phase=="old_engine":
        B.dump(phase+"/skipped.json",{"purpose":"pre-upgrade table established"})
        return []
    if tuple(tables)!=B.TABLES:raise RuntimeError("unexpected physical tables")
    T.check_originals(phase,tables,qs)
    T.verify_new_answers(phase,tables,[Q])
    if phase=="later":
        B.dump(phase+"/validation.json",{"valid":True,"cross_over_performed":False,
            "purpose":"later logical and physical integrity checkpoint",
            "original_13_answers_verified":True,"D4_answers_equal":True,
            "replicate":REPLICATE})
        return []
    plan=schedule(phase)
    B.dump(phase+"/order_schedule.json",[
      {"cycle":i,"prime_arm":a,"table_order":list(p)} for i,(a,p) in enumerate(plan)])
    freeze_meta=persistently_freeze_background_merges(phase)
    baseline=F.physical()
    B.dump(phase+"/physical_baseline.json",baseline)
    rows=[]
    lg={}
    for cycle,(arm,order) in enumerate(plan):
        F.reset(baseline,phase,cycle)
        # Three tables each receive the same first policy, across a balanced
        # permutation of global first/second/third executed queries.
        for step in STEPS:
            setting=(arm if step=="prime" else
                  "cache_on" if step in ("probe","repeat") else
                  "cache_off" if step=="bypass" else "stats_off")
            for position,table in enumerate(order):
                query_id=f"c56_{phase}_{cycle}_{step}_{table}"
                T.sq(Q,table,setting,qid=query_id,fmt="Null")
                rows.append({"cycle":cycle,"prime_arm":arm,"step":step,
                    "table":table,"position":position,"query_id":query_id,
                    "setting":setting})
        if F.physical()!=baseline:raise RuntimeError("PHYSICAL_STATE_CHANGED_IN_CYCLE")
        cycle_logs=events(phase,cycle)
        expected={row["query_id"] for row in rows if row["cycle"]==cycle}
        missing=sorted(expected-set(cycle_logs))
        if missing:
            B.dump(phase+"/missing_cycle_"+str(cycle)+".json",
                {"cycle":cycle,"missing":missing,"retrieved":len(cycle_logs),
                 "completed_cycles_before_this":cycle})
            raise RuntimeError("MISSING_PER_CYCLE_RECEIPTS "+str(missing))
        for qid in expected:
            if qid in lg:raise RuntimeError("DUPLICATE_CYCLE_QUERY_ID "+qid)
            lg[qid]=cycle_logs[qid]
        B.dump(phase+"/recovered_receipt_journal.json",
          {"cycles_complete":cycle+1,"expected_queries":len(lg),
           "per_cycle_receipts":[{"qid":qid,
                "duration_ms":int(lg[qid]["query_duration_ms"])}
                for qid in sorted(lg)]})
        B.log("C56_CYCLE",phase,cycle+1,len(plan),arm,order,"receipts",len(expected))
    for x in rows:
        if x["query_id"] not in lg:raise RuntimeError("MISSING_QUERY_RECEIPT "+x["query_id"])
        r=lg[x["query_id"]]
        pe=r.get("ProfileEvents") or {}
        x.update(ms=r["query_duration_ms"],
          read_rows=r["read_rows"],read_bytes=r["read_bytes"],
          stat_us=pe.get("LoadedStatisticsMicroseconds",0),
          opt_us=pe.get("QueryPlanOptimizeMicroseconds",0),
          file_open=pe.get("FileOpen",0),
          os_read_bytes=pe.get("OSReadBytes",0))
    with (R/phase/"receipts.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]))
        w.writeheader();w.writerows(rows)
    evidence=calculate(phase,rows,plan)
    if F.physical()!=baseline:raise RuntimeError("PHYSICAL_CHANGED_AFTER_MEASUREMENT")
    evidence["persistent_merge_freeze"]=freeze_meta
    B.dump(phase+"/validation.json",evidence)
    B.log("C56_CHECKPOINT_PASSED",phase,json.dumps(evidence,sort_keys=True))
    restore_background_merges(freeze_meta)
    return []

B.measure=measure
if __name__=="__main__":
    B.dump("PREREGISTERED.json",{
      "design":"full-factorial balanced first query order after server restarts",
      "SQL":"fixed unchanged D4_keys_and_line_prices",
      "dataset":"SSB SF1 monthly one-batch history",
      "prime_settings":list(PRIMES),"steps":list(STEPS),
      "tables":list(TABLES),
      "cycles_initial":9,"cycles_followup":0,"replicate":REPLICATE,
      "design_change":"two independent 9-cycle balanced blocks, opposite cyclic permutations",
      "log_recovery":"per-cycle query_log flush and durable receipt journal before every restart",
      "balance":"each independent runner gets 3 primer policies x 3 cyclic Latin orders; 2nd runner uses complementary reversals",
      "selection":"fixed before observing this study",
      "integrity":"official 13 SQL+new D4 answer hashes and active part checksums",
      "frozen_checkpoint":"max_parts_to_merge_at_once=0 persisted as table setting during measurements, restored before next natural-aging stage",
      "intervention_check":"active part inventory and checksums unchanged when table setting toggles",
      "control":"cache_off bypass and stats_off primes for general first-query cost",
      "exploratory_materiality":"absolute 30ms plus relative 10% unchanged",
      "limits":["server restart resets multiple caches, not statistics alone",
        "first treatment is shared among three tables per restart",
        "3 samples per arm in each independent run, later checkpoint has no crossover",
        "load-statistics event can report zero despite cold latency"]})
    import sys
    if "--preflight" in sys.argv:
        assert len(schedule("post_ingest"))==9
        assert len(schedule("later"))==9
        assert B.RUNS==9 and N==1
        print("C56_PREFLIGHT_OK",REPLICATE,flush=True)
    else:
        B.main()
        p=R/"FINAL_STATUS.json"
        final=json.loads(p.read_text())
        final["study"]="56-split-counterbalanced-server-startup"
        final["replicate"]=REPLICATE
        final["cross_over_startup_balanced"]=True
        p.write_text(json.dumps(final,indent=2,sort_keys=True)+"\n")
        print("C56_FINAL_SUCCESS",REPLICATE,flush=True)
