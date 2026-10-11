#!/usr/bin/env python3
"""Independent SQL-family check on the same public TPC-H subset.

This test holds the previously defined statistics-routing rule fixed.
It is an isolated JOIN-family evaluation, not a new upgrade approval test.
"""
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys

import db_tpch_join_boundary as T

R = Path("c48_result")
R.mkdir(exist_ok=True)
T.ROOT = R
T.KEY_CAP = 300000
VERSION = "26.8.1.2041"
CAP = 300000
REPS = 8
WARMUPS = 2
ARMS = (("prune", 0), ("optimized", 1))

def save(name, value):
    path = R / name
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

def normalize_answer(answer):
    rows = []
    for line in answer.strip().splitlines():
        cols = []
        for raw in line.split("\t"):
            try:
                x = float(raw)
                cols.append(("num", x))
            except ValueError:
                cols.append(("str", raw))
        rows.append(cols)
    return rows

def same_answer(a, b):
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        if len(ra) != len(rb):
            return False
        for (ta, va), (tb, vb) in zip(ra, rb):
            if ta != tb:
                return False
            if ta == "str" and va != vb:
                return False
            if ta == "num" and abs(va - vb) > max(1e-4, 1e-8 * max(abs(va), abs(vb))):
                return False
    return True

def rewrite(statement):
    import re
    tables = "|".join(T.TABLES)
    return re.sub(r"\b(" + tables + r")\b", lambda m: "tpch." + m.group(1) + "_f", statement)

def run_sql(ct, statement, query_id=None, timeout=300):
    return T.q(ct, statement, qid=query_id, timeout=timeout)

def settings(use_stats):
    return (" SETTINGS use_statistics=" + str(use_stats) +
            ",use_statistics_for_part_pruning=1,use_statistics_cache=0,"
            "use_query_condition_cache=0,max_threads=4,max_memory_usage=9000000000")

def inventory(ct):
    return run_sql(ct, "SELECT table,partition,name,rows,marks,bytes_on_disk "
             "FROM system.parts WHERE database='tpch' AND active "
             "ORDER BY table,partition,name FORMAT TSV")

def main():
    q7 = T.fetch("queries/query_07.sql").strip().rstrip(";")
    q13 = T.fetch("queries/query_13.sql").strip().rstrip(";")
    original = "date '1995-01-01' AND date '1996-12-31'"
    replacement = "date '1995-07-01' AND date '1996-06-30'"
    if original not in q7:
        raise RuntimeError("Pinned Q7 date-range marker changed")
    q7_window = q7.replace(original, replacement)
    queries = {
        "q07_official": q7,
        "q07_narrow_window": q7_window,
        "q13_official": q13,
    }
    manifest = {
        "kind": "isolated JOIN SQL holdout",
        "engine": VERSION,
        "TPC_H_upstream_commit": T.PIN,
        "sqls": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in queries.items()},
        "definition": "Q07 and Q13 were absent from the policy-development SQL set; Q07 narrow is a declared selectivity variant",
        "prior_routing_rule": "JOIN -> use_statistics=1; SSB -> use_statistics=0",
        "cache": 0,
        "statistics_part_pruning": 1,
        "key_cap": CAP,
        "warmup_rounds": WARMUPS,
        "measured_rounds": REPS,
        "limitations": [
            "Single 26.8 server and one public key-restricted TPC-H data subset",
            "No unseen SSB SQL or independently measured real-world traffic",
            "Serial query executions, no concurrent throughput",
            "Q07 narrow-range variant is not a standard TPC-H benchmark query",
        ],
    }
    save("manifest.json", manifest)
    if "--preflight" in sys.argv:
        print("PREFLIGHT_OK", len(queries), len(ARMS), REPS, flush=True)
        return

    T.run(["docker", "pull", "clickhouse/clickhouse-server:" + VERSION], timeout=600)
    ct = T.boot(VERSION)
    try:
        sqls, ddl = T.setup_source()
        loaded = {}
        for tab in T.TABLES:
            columns, pk = ddl[tab]
            run_sql(ct, f"CREATE TABLE tpch.{tab}_f ({columns}) ENGINE=MergeTree ORDER BY ({pk})")
            src = "https://public-pme.s3.amazonaws.com/join_bench/tpc-h/sf10/" + T.SOURCE_PARQUET[tab]
            where = (f" WHERE o_orderkey <= {CAP}" if tab == "orders" else
                     f" WHERE l_orderkey <= {CAP}" if tab == "lineitem" else "")
            insert = (f"INSERT INTO tpch.{tab}_f SELECT {T.SELECTS[tab]} "
                      f"FROM s3('{src}',NOSIGN,'Parquet'){where}"
                      " SETTINGS materialize_statistics_on_insert=1,"
                      "max_threads=4,max_insert_threads=2,max_memory_usage=9000000000")
            run_sql(ct, insert, timeout=1100)
            loaded[tab] = int(run_sql(ct, f"SELECT count() FROM tpch.{tab}_f FORMAT TSV").strip())
            print("LOADED", tab, loaded[tab], flush=True)
        save("rows.json", loaded)
        for tab in T.TABLES:
            run_sql(ct, f"SYSTEM STOP MERGES tpch.{tab}_f")
        before = inventory(ct)
        save("physical_before.json", {"sha256": hashlib.sha256(before.encode()).hexdigest()})
        checks = {}
        for name, original_sql in queries.items():
            statement = rewrite(original_sql)
            for arm, u in ARMS:
                sql = statement + settings(u)
                answer = run_sql(ct, sql + " FORMAT TSV")
                checks[(name,arm)] = normalize_answer(answer)
                save("answers/" + name + "_" + arm + ".json", {
                    "n_rows": len(checks[(name,arm)]),
                    "sha256_original_output": hashlib.sha256(answer.encode()).hexdigest()
                })
                try:
                    plan = run_sql(ct, "EXPLAIN PLAN " + sql)
                    p = R / "plans" / (name + "_" + arm + ".txt")
                    p.parent.mkdir(exist_ok=True)
                    p.write_text(plan)
                except Exception as e:
                    print("PLAN_UNAVAILABLE", name, arm, str(e)[:200], flush=True)
            if not same_answer(checks[(name,"prune")], checks[(name,"optimized")]):
                raise RuntimeError("NONIDENTICAL_ANSWERS " + name)
        save("answer_check.json", {"all_equal": True, "query_count": len(queries)})

        # Predeclare the randomized, pair-balanced order of the two settings.
        schedule = []
        for rnd in range(-WARMUPS, REPS):
            names = list(queries)
            random.Random(481000 + rnd).shuffle(names)
            for name in names:
                order = list(ARMS)
                random.Random(482000 + rnd * 20 + sum(map(ord, name))).shuffle(order)
                for arm, u in order:
                    schedule.append((rnd, name, arm, u))
        save("schedule.json", schedule)
        for rnd, name, arm, u in schedule:
            qid = f"C48_{name}_{arm}_{rnd + WARMUPS}"
            run_sql(ct, rewrite(queries[name]) + settings(u), query_id=qid, timeout=500)
            if rnd >= 0:
                print("MEASURED", rnd, name, arm, flush=True)
        run_sql(ct, "SYSTEM FLUSH LOGS")
        raw = run_sql(ct, "SELECT query_id,query_duration_ms,read_rows,read_bytes,"
                  "memory_usage,ProfileEvents FROM system.query_log "
                  "WHERE type='QueryFinish' AND startsWith(query_id,'C48_') FORMAT JSONEachRow")
        logged = {json.loads(line)["query_id"]: json.loads(line)
                  for line in raw.splitlines() if line.strip()}
        records = []
        for rnd, name, arm, u in schedule:
            if rnd < 0:
                continue
            qid = f"C48_{name}_{arm}_{rnd + WARMUPS}"
            if qid not in logged:
                raise RuntimeError("MISSING_RECEIPT " + qid)
            data = logged[qid]
            events = data.get("ProfileEvents") or {}
            records.append({
                "round": rnd, "query": name, "arm": arm, "use_statistics": u,
                "duration_ms": data["query_duration_ms"],
                "read_rows": data["read_rows"],
                "read_bytes": data["read_bytes"],
                "peak_memory": data.get("memory_usage", 0),
                "load_statistics_us": events.get("LoadedStatisticsMicroseconds", 0),
                "optimize_us": events.get("QueryPlanOptimizeMicroseconds", 0),
                "selected_marks": events.get("SelectedMarks", 0)
            })
        with (R / "receipts.csv").open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        summary = []
        for name in queries:
            subsets = {arm: [v["duration_ms"] for v in records
                             if v["query"] == name and v["arm"] == arm]
                       for arm, _ in ARMS}
            if not all(len(v) == REPS for v in subsets.values()):
                raise RuntimeError("INCOMPLETE_REPETITIONS")
            baseline = statistics.median(subsets["prune"])
            enabled = statistics.median(subsets["optimized"])
            summary.append({"query": name, "baseline_median_ms": baseline,
                "optimized_median_ms": enabled,
                "optimized_to_baseline_ratio": enabled / max(.001, baseline),
                "repetitions_per_arm": REPS,
                "interpretation": "JOIN held-out diagnostic, not standalone upgrade approval"})
        after = inventory(ct)
        if after != before:
            raise RuntimeError("PHYSICAL_STATE_CHANGED")
        save("summary.json", summary)
        save("final.json", {"valid": True, "sql_outputs_equal": True,
            "measured_receipts": len(records), "physical_state_unchanged": True})
        print("C48_SUCCESS", json.dumps(summary), flush=True)
    finally:
        subprocess.run(["docker", "rm", "-f", ct], capture_output=True, timeout=120)

if __name__ == "__main__":
    main()
