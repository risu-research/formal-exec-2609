#!/usr/bin/env python3
"""Actual >=300ms analytic SQL: fixed families, blind baseline selection.

No repeated-SQL inflation, no sleeps, no deliberate scan amplification beyond
the queries' ordinary exact distinct/count and percentile work.
The same original SF1 records and physical-history mechanism are retained.
"""
import csv,hashlib,importlib,json,os,pathlib,random,statistics
from collections import defaultdict

LAYOUT=os.environ.get("C52_LAYOUT","annual")
if LAYOUT not in ("annual","monthly"):raise ValueError("layout must be annual/monthly")
if LAYOUT=="annual":
    import db_c50 as C
    B=C.B
else:
    import db_c50 as C
    B=importlib.reload(C.B)

R=pathlib.Path("s52_"+LAYOUT)
R.mkdir(exist_ok=True)
B.R=R;B.P.R=R;B.S.ROOT=R;B.S.base.ROOT=R
B.S.base.VERSIONS=[B.S.OLD,B.S.OLD]
B.S.CT="s52_"+LAYOUT[:3]+"_ch";B.S.VOL="s52_"+LAYOUT[:3]+"_vol"
B.RUNS=8

# Entire fixed candidate population, decided before any performance outcomes.
# Dates, categories and groups are meaningful SSB analytical predicates.
# Return cardinality is capped by LIMIT after full GROUP BY aggregation.
SQL_CANDIDATES={
 "D1_exact_line_counts":
 """SELECT toYear(LO_ORDERDATE) AS yr, C_NATION, S_NATION,
    uniqExact(tuple(LO_ORDERKEY, LO_LINENUMBER)) AS line_count,
    quantilesExact(0.25,0.50,0.95)(LO_REVENUE) AS revenue_quantiles,
    sum(LO_REVENUE) AS revenue
    FROM lineorder_flat
    WHERE LO_ORDERDATE >= toDate('1993-01-01')
      AND LO_ORDERDATE < toDate('1998-01-01')
    GROUP BY yr,C_NATION,S_NATION
    ORDER BY yr,C_NATION,S_NATION LIMIT 300""",
 "D2_customer_supplier_percentiles":
 """SELECT C_REGION,S_REGION,P_CATEGORY,LO_DISCOUNT,
    uniqExact(LO_ORDERKEY) AS unique_orders,
    quantilesExact(0.1,0.50,0.9)(LO_EXTENDEDPRICE) AS price_quantiles,
    sum(LO_REVENUE) AS revenue
    FROM lineorder_flat
    WHERE LO_ORDERDATE >= toDate('1992-01-01')
      AND LO_ORDERDATE < toDate('1998-01-01') AND LO_QUANTITY BETWEEN 1 AND 45
    GROUP BY C_REGION,S_REGION,P_CATEGORY,LO_DISCOUNT
    ORDER BY C_REGION,S_REGION,P_CATEGORY,LO_DISCOUNT LIMIT 300""",
 "D3_daily_distribution":
 """SELECT LO_ORDERDATE, C_REGION, S_REGION,
    uniqExact(LO_ORDERKEY) AS order_count,
    quantilesExact(0.1,0.5,0.95)(LO_REVENUE) AS revenue_percentiles,
    sum(LO_REVENUE) AS total_revenue
    FROM lineorder_flat
    WHERE LO_ORDERDATE >= toDate('1994-01-01')
      AND LO_ORDERDATE < toDate('1998-01-01')
    GROUP BY LO_ORDERDATE,C_REGION,S_REGION
    ORDER BY LO_ORDERDATE,C_REGION,S_REGION LIMIT 300""",
 "D4_keys_and_line_prices":
 """SELECT toYear(LO_ORDERDATE) AS yr, C_REGION, S_REGION, P_CATEGORY,
    uniqExact(tuple(LO_ORDERKEY,LO_PARTKEY)) AS distinct_order_parts,
    quantilesExact(0.25,0.5,0.75,0.95)(LO_EXTENDEDPRICE) AS price_quantiles,
    sum(LO_REVENUE) AS revenue
    FROM lineorder_flat
    WHERE LO_ORDERDATE >= toDate('1992-01-01')
      AND LO_ORDERDATE < toDate('1999-01-01') AND LO_DISCOUNT BETWEEN 1 AND 9
    GROUP BY yr,C_REGION,S_REGION,P_CATEGORY
    ORDER BY yr,C_REGION,S_REGION,P_CATEGORY LIMIT 300""",
 "D5_city_revenue_spectrum":
 """SELECT C_CITY,S_CITY,P_BRAND1,
    uniqExact(LO_ORDERKEY) AS order_count,
    quantileExact(0.9)(LO_REVENUE) AS high_revenue,
    sum(LO_REVENUE) AS revenue
    FROM lineorder_flat
    WHERE LO_ORDERDATE >= toDate('1993-01-01')
      AND LO_ORDERDATE < toDate('1998-01-01')
    GROUP BY C_CITY,S_CITY
    ORDER BY C_CITY,S_CITY LIMIT 300"""
}
ARM_SETTINGS=(("cache_on",1,1),("cache_off",1,0),("stats_off",0,1))
COMPARE=(("hist_vs_inherited","ref","hist"),
         ("fresh_vs_inherited","ref","fresh"))
BUDGET_LO_MS=300
BUDGET_HI_MS=2500  # include 1s+ queries when measured
MAX_CHOSEN=3
REPS=8
SEED=520052

def median(vals):
    return statistics.median(vals)

def statement(name,table,arm):
    if name not in SQL_CANDIDATES:raise RuntimeError('unknown fixed candidate')
    au=next((x for x in ARM_SETTINGS if x[0]==arm),None)
    if au is None:raise RuntimeError('unknown arm')
    _,use,cache=au
    body=SQL_CANDIDATES[name].replace('FROM lineorder_flat','FROM ssb.'+table)
    if 'FROM ssb.'+table not in body:raise RuntimeError('table rewrite absent')
    return (body+f" SETTINGS use_statistics={use},use_statistics_cache={cache},"
            "max_threads=2,use_query_condition_cache=0")

def sq(name,table,arm,qid=None,fmt='Null'):
    return B.sql(statement(name,table,arm),qid=qid,fmt=fmt)

def check_originals(phase,tables,qs):
    checked=0
    for qi in range(1,14):
        for t in tables:
            for arm,u,c in B.ARMS:
                v=B.sql(B.P.qstr(qs[qi-1],t,u,c)+' FORMAT TSV')
                h=hashlib.sha256(v.encode()).hexdigest()
                if h!=C.GOLDEN_ANSWERS[str(qi)]:
                    raise RuntimeError('ORIGINAL_SQL_ANSWER_MISMATCH '+str(qi)+' '+t+' '+arm)
                checked+=1
    B.dump(phase+'/original_answers_verified.json',
           {'reference':'earlier monthly layout official SQL outputs',
            'queries':13,'answer_hashes_checked':checked,'valid':True})
    B.log('ORIGINAL_GOLDEN_OK',phase,checked)

def read_logs(prefix):
    B.sql('SYSTEM FLUSH LOGS')
    raw=B.sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
        "FROM system.query_log WHERE type='QueryFinish' "
        "AND startsWith(query_id,'"+prefix+"') FORMAT JSONEachRow")
    out={}
    for ln in raw.splitlines():
        if not ln.strip():continue
        r=json.loads(ln)
        if r['query_id'] in out:raise RuntimeError('duplicate receipt')
        out[r['query_id']]=r
    return out

def pilot():
    # Controls candidate selection using only statistics-disabled inherited
    # baseline duration: treatment outcomes are not inspected until fixed.
    hits=[]
    for n in SQL_CANDIDATES:
        candidate={'name':n,'times':[]}
        try:
            for i in range(3):
                sq(n,'ref','stats_off',qid=f'C52_PILOT_{LAYOUT}_{n}_{i}')
            hit=read_logs(f'C52_PILOT_{LAYOUT}_{n}_')
            for i in (1,2):
                key=f'C52_PILOT_{LAYOUT}_{n}_{i}'
                if key not in hit:raise RuntimeError('missing pilot receipt')
                candidate['times'].append(hit[key]['query_duration_ms'])
            candidate['baseline_median_ms']=median(candidate['times'])
            candidate['eligible']=BUDGET_LO_MS<=candidate['baseline_median_ms']<=BUDGET_HI_MS
        except Exception as e:
            candidate['eligible']=False
            candidate['error']=str(e)[-700:]
        hits.append(candidate)
        B.log('PILOT',n,candidate.get('baseline_median_ms'),candidate['eligible'])
    selected=[x['name'] for x in hits if x['eligible']][:MAX_CHOSEN]
    # Never pretend an unqualified query is within the target interval.
    if not selected:
        selected=[max((x for x in hits if 'baseline_median_ms' in x),
                      key=lambda x:x['baseline_median_ms'])['name']]
    B.dump('SELECTED.json',{'pilot':hits,'selected':selected,
        'target_range_ms':[BUDGET_LO_MS,BUDGET_HI_MS],
        'qualified_count':sum(x['eligible'] for x in hits),
        'selection':'first three candidates eligible by baseline-only duration,'
         ' else longest working candidate, explicitly out of range',
        'selection_before_treatment':True})
    return selected

def verify_new_answers(phase,tables,selected):
    # No assumed equivalence: compare actual query outputs for each deployed
    # table and every arm with canonical JSON rows (order specified by SQL).
    for n in selected:
        seen=None
        for t in tables:
            for arm,_,_ in ARM_SETTINGS:
                v=sq(n,t,arm,fmt='JSONEachRow')
                obj=[json.loads(x) for x in v.splitlines() if x.strip()]
                canonical=json.dumps(obj,sort_keys=True,separators=(',',':'))
                digest=hashlib.sha256(canonical.encode()).hexdigest()
                if seen is None:seen=digest
                if digest!=seen:raise RuntimeError('NEW_SQL_OUTPUT_MISMATCH '+n+' '+t+' '+arm)
        B.log('NEW_SQL_EQUAL',phase,n)
    B.dump(phase+'/new_sql_answers.json',{'valid':True,'candidates':selected,
      'comparisons_per_sql':len(tables)*len(ARM_SETTINGS)})

def ci(x,seed):
    rng=random.Random(seed)
    n=len(x)
    draws=[median([x[rng.randrange(n)] for _ in range(n)]) for _ in range(1000)]
    draws.sort()
    return [draws[24],draws[974]]

def summarize(phase,receipts,selected):
    indexed=defaultdict(dict)
    for r in receipts:
        indexed[(r['name'],r['table'],r['arm'])][r['round']]=r
    ret=[]
    for n in selected:
        for label,a,b in COMPARE:
            for arm,_,_ in ARM_SETTINGS:
                aa=indexed[(n,a,arm)];bb=indexed[(n,b,arm)]
                if set(aa)!=set(bb):raise RuntimeError('unpaired')
                ks=sorted(aa)
                before=median([aa[k]['ms'] for k in ks])
                after=median([bb[k]['ms'] for k in ks])
                deltas=[bb[k]['ms']-aa[k]['ms'] for k in ks]
                loaded=[(bb[k]['stat_us']-aa[k]['stat_us'])/1000 for k in ks]
                opts=[(bb[k]['opt_us']-aa[k]['opt_us'])/1000 for k in ks]
                reading=[bb[k]['read_rows']-aa[k]['read_rows'] for k in ks]
                x={'name':n,'phase':phase,'history':label,'arm':arm,
                   'baseline_median_ms':before,'target_median_ms':after,
                   'delta_median_ms':after-before,
                   'ratio':after/max(.001,before),
                   'paired_delta_median_ms':median(deltas),
                   'paired_delta_ci95_ms':ci(deltas,SEED+len(n)+len(label)+len(arm)),
                   'positive_fraction':sum(z>0 for z in deltas)/len(deltas),
                   'loaded_statistics_delta_median_ms':median(loaded),
                   'optimize_delta_median_ms':median(opts),
                   'read_rows_delta_median':median(reading),
                   'at_least_30ms':(after-before)>=30,
                   'at_least_10pct':(after/max(.001,before))>=1.10,
                   'strong_regr':(after-before)>=30 and after/max(.001,before)>=1.10,
                   'qualified_baseline':before>=300,
                   'n':len(ks)}
                ret.append(x)
                if arm=='cache_on':
                    B.log('RESULT',phase,n,label,'ms',before,after,'d',x['delta_median_ms'],
                          'r',round(x['ratio'],3),'stats',round(x['loaded_statistics_delta_median_ms'],2),
                          'ci',x['paired_delta_ci95_ms'],'strong',x['strong_regr'])
    B.dump(phase+'/results.json',ret)
    return ret

def measure(phase,tables,qs,nreps):
    if phase=='old_engine':
        B.dump(phase+'/status.json',{'skipped':True,
                  'reason':'old engine only establishes unchanged physical historical state'})
        return []
    check_originals(phase,tables,qs)
    if phase=='post_ingest':
        selected=pilot()
    else:
        selected=json.loads((R/'SELECTED.json').read_text())['selected']
    verify_new_answers(phase,tables,selected)
    receipts=[]
    for rnd in range(-1,nreps):
        tasks=[(n,arm,t) for n in selected for arm,_,_ in ARM_SETTINGS for t in tables]
        random.Random(SEED+rnd).shuffle(tasks)
        for n,arm,t in tasks:
            qid=f'C52_MEAS_{LAYOUT}_{phase}_{rnd+1}_{n}_{arm}_{t}'
            sq(n,t,arm,qid=qid)
            if rnd>=0:receipts.append({'phase':phase,'round':rnd,'name':n,'arm':arm,
                                       'table':t,'query_id':qid})
        B.log('MEASURE_ROUND',phase,rnd,len(receipts))
    raw=read_logs(f'C52_MEAS_{LAYOUT}_{phase}_')
    for row in receipts:
        v=raw.get(row['query_id'])
        if v is None:raise RuntimeError('query receipt missing '+row['query_id'])
        p=v.get('ProfileEvents') or {}
        row.update(ms=v['query_duration_ms'],read_rows=v['read_rows'],
                   read_bytes=v['read_bytes'],
                   stat_us=p.get('LoadedStatisticsMicroseconds',0),
                   opt_us=p.get('QueryPlanOptimizeMicroseconds',0),
                   selected_marks=p.get('SelectedMarks',0))
    with (R/phase/'receipts.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(receipts[0]))
        w.writeheader();w.writerows(receipts)
    result=summarize(phase,receipts,selected)
    B.dump(phase+'/validation.json',{'valid':True,'rows':len(receipts),
          'selected':selected,'comparison_results':len(result),
          'original_13_sql_answers_verified':True,'new_sql_outputs_equal':True})
    return result

B.measure=measure
if __name__=="__main__":
    B.dump('PREREGISTERED.json',{
       'layout':LAYOUT,'candidates':SQL_CANDIDATES,
       'baseline_pilot_only':True,'target_minimum_ms':BUDGET_LO_MS,
       'target_maximum_ms':BUDGET_HI_MS,
       'selection_before_treatment':True,
       'predeclared_selection':'first three qualified baseline candidates; otherwise record longest and mark unqualified',
       'no_artificial_delay':True,
       'main_rounds':REPS,'followup_rounds':4,
       'settings':ARM_SETTINGS,'comparisons':COMPARE,
       'absolute_materiality_ms':30,'relative_materiality_ratio':1.10,
       'strong_result_requires_both':True,
       'questions':['Real 300ms+ SQL cost','statistics loading','physical history'],
       'limitations':['single runner per layout','nonstandard diagnostic queries but realistic analytic aggregates',
                      'calendar partitions are an analytic controlled condition, not a production sample',
                      'run-to-run natural merge state can vary; snapshots are saved']})
    import sys
    if '--preflight' in sys.argv:
        assert len(SQL_CANDIDATES)==5 and B.RUNS==8
        assert len(C.GOLDEN_ANSWERS)==13 and all(len(x)==64 for x in C.GOLDEN_ANSWERS.values())
        assert all('FROM lineorder_flat' in x for x in SQL_CANDIDATES.values())
        print('C52_PREFLIGHT_OK',LAYOUT,flush=True)
    else:
        B.main()
        status=json.loads((R/'FINAL_STATUS.json').read_text())
        status['study']='52-actual-long-query-statistics-history'
        status['layout']=LAYOUT
        status['selection']=json.loads((R/'SELECTED.json').read_text())
        (R/'FINAL_STATUS.json').write_text(json.dumps(status,indent=2,sort_keys=True)+'\n')
        print('C52_FINAL_SUCCESS',LAYOUT,flush=True)
