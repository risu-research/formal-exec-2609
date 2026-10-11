#!/usr/bin/env python3
"""Fixed long analytic SQL under measured micro-batch history conditions.

Target: causal chain from ingestion batches -> naturally maintained physical
parts -> statistics loading overhead -> long-query latency. No clock sleeps,
no forced merges or altered original queries, no outcome-selected thresholds.
"""
import json,os,pathlib,statistics
os.environ["C52_LAYOUT"]="monthly"
import db_c52 as T
B=T.B
BATCHES=int(os.environ.get("C53_BATCHES","4"))
if BATCHES not in (1,4,8):raise ValueError("predeclared batch settings: 1,4,8")
R=pathlib.Path(f"s53_batch{BATCHES}")
R.mkdir(exist_ok=True)
T.R=R;B.R=R;B.P.R=R;B.S.ROOT=R;B.S.base.ROOT=R
B.S.base.VERSIONS=[B.S.OLD,B.S.OLD]
B.S.CT=f"s53_{BATCHES}_ch"
B.S.VOL=f"s53_{BATCHES}_vol"
B.RUNS=8
SQL="D4_keys_and_line_prices"
assert SQL in T.SQL_CANDIDATES
assert T.LAYOUT=="monthly"
assert B.create.__module__=="db_c42"
assert B.insert.__module__=="db_c42"

def micro_batches(t,predicate,mode=None):
    # Each pass ingests one non-overlapping contiguous day-of-month slice in
    # chronological order, representing weekly or 4-day arrivals per month.
    # Natural merges remain enabled. No OPTIMIZE, merge-stop or changed stats
    # settings before the original measurement checkpoint.
    clause="" if mode is None else f"materialize_statistics_on_insert={mode},"
    for k in range(BATCHES):
        # values k=0,...,B-1 partition days 1..31 exactly once
        date_slot=f"intDiv((toDayOfMonth(LO_ORDERDATE)-1)*{BATCHES},31)={k}"
        stmt=(f"INSERT INTO ssb.{t} SELECT * FROM ssb.raw WHERE ({predicate}) "
              f"AND {date_slot} SETTINGS {clause}"
              "max_threads=2,max_insert_threads=1,"
              "min_insert_block_size_rows=100000,min_insert_block_size_bytes=1")
        B.sql(stmt)
    B.log("MICROBATCH_COMPLETE",t,BATCHES,"stat_policy",mode)

B.insert=micro_batches

def fixed_candidate():
    # Fixed based on C52 before observing any batch-condition outcomes.
    # Baseline runtime verifies >=300ms after selecting, not used to select.
    result={'selected':[SQL],
      'selection':'prior completed stage D4 alone meets >=300ms on both layouts',
      'selection_before_treatment':True,
      'target_range_ms':[300,2500],
      'qualified_count':None,
      'original_pilot_not_used':True}
    B.dump("SELECTED.json",result)
    B.log("FIXED_SQL",SQL)
    return [SQL]
T.pilot=fixed_candidate

original_measure=T.measure
def measured(phase,tables,queries,nreps):
    result=original_measure(phase,tables,queries,nreps)
    if phase in ('post_ingest','later'):
        primary=[x for x in result if x['arm']=='cache_on']
        controls=[x for x in result if x['arm']=='stats_off']
        out={'version':'53-audit-1','condition_batches_per_month':BATCHES,
             'sql':SQL,'selection_fixed_before_measurement':True,
             'baseline_300ms_requirement':all(x['baseline_median_ms']>=300 for x in primary),
             'primary_30ms_and_10pct_count':sum(x['strong_regr'] for x in primary),
             'primary_10pct_count':sum(x['at_least_10pct'] for x in primary),
             'absolute_30ms_count':sum(x['at_least_30ms'] for x in primary),
             'stats_off_10pct_count':sum(x['at_least_10pct'] for x in controls),
             'expected_mechanism':'loaded-statistics difference should parallel change in elapsed time',
             'must_not_infer':'batch-count causal effect across different runners'}
        B.dump(phase+'/decision.json',out)
        B.log('DECISION',phase,json.dumps(out,sort_keys=True))
    return result
B.measure=measured

if __name__=="__main__":
    prereg={
      'design':'unmodified SQL D4 on SSB SF1 monthly partitions',
      'version':'53-1',
      'microbatch_count':BATCHES,
      'microbatch_partitioning':'contiguous day-of-month buckets per calendar month',
      'mechanism':'naturally merged parts and number of per-part statistics',
      'physical_histories':['inherited','mixed','legacy','fresh'],
      'sql':'D4_keys_and_line_prices',
      'sql_selected':'before all treatment outcomes using Stage52',
      'statistics_arms':list(T.ARM_SETTINGS),
      'comparisons':list(T.COMPARE),
      'primary':{'baseline_minimum_ms':300,'delta_ms':30,'relative_ratio':1.10,
          'requires_all':True,'stats_load_must_be_reported':True},
      'repetitions_initial':8,'repetitions_later':4,
      'old_engine':B.S.OLD,'new_engine':B.S.NEW,
      'evidence':['13 original SSB SQL results are identical to Stage42',
                  'D4 outputs identical between all arms and physical states',
                  'physical snapshots equal before vs after measurement',
                  'same-query paired randomized order',
                  'query log statistics-loading and read_rows metrics'],
      'limitations':['Micro-batch schedule models sequential day-of-month slices across all years',
                     'Fixed 6M SF1 rather than independent production workload',
                     'Independent runner per batch count, compare within-run contrasts',
                     'Longer execution may dilute nearly fixed statistics-loading overhead']}
    B.dump('PREREGISTERED.json',prereg)
    import sys
    if '--preflight' in sys.argv:
        assert BATCHES in (1,4,8)
        assert len(T.SQL_CANDIDATES)==5 and SQL in T.SQL_CANDIDATES
        assert len(T.C.GOLDEN_ANSWERS)==13
        assert B.RUNS==8
        assert T.LAYOUT=="monthly"
        for month_day in range(1,32):
            assert sum(((month_day-1)*BATCHES//31)==k for k in range(BATCHES))==1
        print('C53_PREFLIGHT_OK',BATCHES,flush=True)
    else:
        B.main()
        p=R/"FINAL_STATUS.json"
        data=json.loads(p.read_text())
        data['new_study']='53-fixed-long-query-batch-history'
        data['batch_count']=BATCHES
        data['preregistration']='PREREGISTERED.json'
        p.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
        print('C53_FINAL_SUCCESS',BATCHES,flush=True)
