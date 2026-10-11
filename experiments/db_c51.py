#!/usr/bin/env python3
"""Paired absolute-latency validation on preselected nontrivial query costs.

Inherits the corrected annual physical-history setup, without rewriting SSB
queries. This suite tests absolute millisecond penalties alongside statistics
loading, not a changed or post-hoc upgrade acceptance policy.
"""
import csv,hashlib,json,random,statistics,pathlib
from collections import defaultdict
import db_c50 as C

B=C.B
R=pathlib.Path("s51")
R.mkdir(parents=True,exist_ok=True)
B.R=R; B.P.R=R; B.S.ROOT=R; B.S.base.ROOT=R
B.S.base.VERSIONS=[B.S.OLD,B.S.OLD]
B.S.CT="c51_ch"; B.S.VOL="c51_vol"
B.RUNS=12
QUERIES=(4,7,11,12,13)
PRIMARY_ARMS=("cache_on","cache_off","stats_off")
PRIMARY_PAIRS=(("hist_vs_inherited","ref","hist"),
               ("fresh_vs_inherited","ref","fresh"))
MATERIAL_ABS_MS=5.0
MATERIAL_REL=1.10

def median(seq):
    return statistics.median(seq)

def quantile(seq,p):
    xs=sorted(seq)
    k=(len(xs)-1)*p
    lo=int(k)
    return xs[lo]+(xs[min(lo+1,len(xs)-1)]-xs[lo])*(k-lo)

def bootstrap_ci(deltas,seed):
    r=random.Random(seed)
    n=len(deltas)
    med=[]
    for _ in range(2000):
        med.append(median([deltas[r.randrange(n)] for j in range(n)]))
    return [round(quantile(med,.025),4),round(quantile(med,.975),4)]

def evaluate(phase,rows,tables):
    by=defaultdict(list)
    for x in rows:
        by[(x['table'],x['arm'],x['sql'])].append(x)
    output=[]
    for label,a,b in PRIMARY_PAIRS:
        if a not in tables or b not in tables:continue
        for arm in PRIMARY_ARMS:
            for qi in QUERIES:
                left=by[(a,arm,qi)];right=by[(b,arm,qi)]
                xa={x['round']:x for x in left};xb={x['round']:x for x in right}
                if set(xa)!=set(xb):raise RuntimeError("Unpaired samples")
                rs=sorted(xa)
                a_ms=median([float(xa[k]['ms']) for k in rs])
                b_ms=median([float(xb[k]['ms']) for k in rs])
                pair_d=[float(xb[k]['ms'])-float(xa[k]['ms']) for k in rs]
                load_d=[(float(xb[k]['stat_us'])-float(xa[k]['stat_us']))/1000 for k in rs]
                optimize_d=[(float(xb[k]['opt_us'])-float(xa[k]['opt_us']))/1000 for k in rs]
                difference=b_ms-a_ms;ratio=b_ms/max(0.001,a_ms)
                output.append({
                 'phase':phase,'comparison':label,'arm':arm,'sql':qi,
                 'repetitions':len(rs),
                 'baseline_median_ms':a_ms,'target_median_ms':b_ms,
                 'delta_median_ms':difference,'ratio':ratio,
                 'paired_delta_median_ms':median(pair_d),
                 'paired_delta_ci95_ms':bootstrap_ci(pair_d,5100+qi+len(arm)+len(label)),
                 'positive_fraction':sum(d>0 for d in pair_d)/len(pair_d),
                 'stats_load_delta_median_ms':median(load_d),
                 'optimize_delta_median_ms':median(optimize_d),
                 'material_absolute_and_relative':difference>=MATERIAL_ABS_MS and ratio>=MATERIAL_REL,
                 'measurement_note':'query_duration_ms integer; bootstrap resamples paired sequential runs'
                })
    B.dump(phase+'/target_metrics.json',output)
    for x in output:
        if x['arm']=='cache_on':
            B.log("TARGET",phase,x['comparison'],x['sql'],round(x['delta_median_ms'],3),
                  round(x['ratio'],3),x['paired_delta_ci95_ms'],x['material_absolute_and_relative'])
    return output

def measure(phase,tables,queries,nreps):
    # All 13 unmodified original SQLs must agree with the older monthly-run
    # answer corpus on every arm and every physical deployment history.
    answer_map={}
    for qi in range(1,14):
        for table in tables:
            for arm,u,c in B.ARMS:
                sql=B.P.qstr(queries[qi-1],table,u,c)+" FORMAT TSV"
                answer_hash=hashlib.sha256(B.sql(sql).encode()).hexdigest()
                answer_map[f"{qi}/{table}/{arm}"]=answer_hash
                if phase!='old_engine' and answer_hash!=C.GOLDEN_ANSWERS[str(qi)]:
                    raise RuntimeError("CROSS_RUN_ANSWER_MISMATCH q"+str(qi)+" "+table+" "+arm)
    B.dump(phase+'/answers.json',answer_map)
    B.dump(phase+'/answer_check.json',{'valid':True,'sql_count':13,
          'hashes_checked':len(answer_map),'monthly_reference_enforced':phase!='old_engine'})
    B.log("GOLDEN_ANSWERS_OK",phase,len(answer_map))
    # Randomize paired table/arm order within blocks to reduce sequential drift.
    tasks=[]
    for rnd in range(-1,nreps):
        combinations=[(q,t,arm,u,c) for q in QUERIES for t in tables for arm,u,c in B.ARMS]
        random.Random(510051+rnd).shuffle(combinations)
        for q,t,arm,u,c in combinations:
            qid=f"C51_{phase}_{rnd+1}_{q}_{t}_{arm}"
            statement=B.P.qstr(queries[q-1],t,u,c)
            B.sql(statement,qid=qid,fmt='Null')
            if rnd>=0:tasks.append({'phase':phase,'round':rnd,'sql':q,'table':t,'arm':arm,
                                    'qid':qid,'use_statistics':u,'cache':c})
        B.log("ROUND",phase,rnd,len(tasks))
    B.sql("SYSTEM FLUSH LOGS")
    raw=B.sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
          "FROM system.query_log WHERE type='QueryFinish' AND "
          "startsWith(query_id,'C51_"+phase+"_') FORMAT JSONEachRow")
    logs={}
    for l in raw.splitlines():
        if not l.strip():continue
        v=json.loads(l)
        if v['query_id'] in logs:raise RuntimeError('DUPLICATE_RECEIPT')
        logs[v['query_id']]=v
    for item in tasks:
        v=logs.get(item['qid'])
        if v is None:raise RuntimeError('MISSING_RECEIPT '+item['qid'])
        e=v.get('ProfileEvents') or {}
        item.update(ms=v['query_duration_ms'],read_rows=v['read_rows'],
              read_bytes=v['read_bytes'],stat_us=e.get('LoadedStatisticsMicroseconds',0),
              opt_us=e.get('QueryPlanOptimizeMicroseconds',0),
              selected_marks=e.get('SelectedMarks',0))
    with (R/phase/'receipts.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(tasks[0]))
        w.writeheader();w.writerows(tasks)
    results=evaluate(phase,tasks,tables)
    B.dump(phase+'/validation.json',{'valid':True,'measured_receipts':len(tasks),
      'original_sqls_verified':len(answer_map),
      'comparison_records':len(results),'preselected_sqls':list(QUERIES),
      'preregistered_abs_ms':MATERIAL_ABS_MS,'preregistered_ratio':MATERIAL_REL})
    return results

B.measure=measure
if __name__=="__main__":
    B.dump('PREREGISTERED.json',{
      'version':'51-1',
      'source':'same physical-history design and data as corrected annual layout control',
      'fixed_original_queries':list(QUERIES),
      'control':'all 13 original SSB SQL answer hashes checked against monthly reference',
      'arms':[list(x) for x in B.ARMS],
      'history_pairs':[list(x) for x in PRIMARY_PAIRS],
      'main_rounds':12,'followup_rounds':4,
      'primary_definition':'absolute latency difference >= 5ms and ratio >=1.10',
      'gates':'No new upgrade acceptance gate; diagnostic materiality only',
      'measurements':'paired query duration, loaded statistics microseconds, optimize microseconds, rows read',
      'interpretation':'within-run evidence, not independent machine replication',
      'selection_rationale':'Q4,Q7,Q11,Q12,Q13 baseline 20–100ms observed before test',
      'limitations':['single runner','query times rounded to whole milliseconds',
                     'more work is not artificially generated by repeated SQL within one request',
                     'part state may differ between independent full setups']
      })
    import sys
    if '--preflight' in sys.argv:
        assert tuple(QUERIES)==(4,7,11,12,13)
        assert len(C.GOLDEN_ANSWERS)==13
        assert B.sql is C.original_sql
        assert B.RUNS==12
        print('C51_PREFLIGHT_OK',flush=True)
    else:
        B.main()
        v=json.loads((R/'FINAL_STATUS.json').read_text())
        v['study']='51-paired-longer-query-replication'
        v['sql_selection']=list(QUERIES)
        v['main_rounds']=12
        v['followup_rounds']=4
        (R/'FINAL_STATUS.json').write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
        print('C51_FINAL_SUCCESS',flush=True)
