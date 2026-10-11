#!/usr/bin/env python3
"""Annual-partition natural-maintenance control with quarterly batch arrivals.
Keeps the established four-history, three-policy, paired-query experiment fixed.
One database, one runner; no claim about independent production populations.
"""
import json,pathlib,time
import db_c42 as B

R=pathlib.Path('s50')
R.mkdir(parents=True,exist_ok=True)
B.R=R;B.P.R=R;B.S.ROOT=R;B.S.base.ROOT=R
B.S.base.VERSIONS=[B.S.OLD,B.S.OLD]
B.S.CT='c50_ch';B.S.VOL='c50_vol'
B.RUNS=6
original_sql=B.sql
original_partmap=B.partmap

# Preserve the original SQL interface. Alter table DDL and insertion predicates only.
# A previous annual-layout control altered Q2 and Q10 predicates and is invalid.
assert B.sql is original_sql

def create_year(name,cols):
    B.sql('CREATE TABLE ssb.'+name+' ('+cols+') ENGINE=MergeTree '
          'PARTITION BY toYear(LO_ORDERDATE) ORDER BY (LO_ORDERDATE,LO_ORDERKEY)')
B.create=create_year

def cutoff_year():
    raw=B.sql('SELECT toYear(LO_ORDERDATE),count() FROM ssb.raw GROUP BY 1 ORDER BY 1 FORMAT TSV')
    years=[(int(a),int(b)) for a,b in (x.split('\t') for x in raw.splitlines())]
    total=sum(n for _,n in years)
    if total!=6001215 or len(years)<5:raise RuntimeError('Unexpected source date population')
    candidates=[];old=0
    for i,(year,count) in enumerate(years):
        if i>=2 and len(years)-i>=2:
            candidates.append((abs(old-total/2),year,old,total-old))
        old+=count
    _,cut,old,recent=min(candidates)
    if min(old,recent)<.25*total:raise RuntimeError('Unbalanced source history')
    B.dump('calendar.json',{'years':years,'cutoff_year':cut,'old_rows':old,'new_rows':recent,
              'full_rows':total,'selection':'nearest balanced year cutoff fixed before measurements',
              'arrival_schedule':'four quarterly batches per logical loading operation'})
    B.log('YEAR_CUTOFF',cut,old,recent)
    return cut,old,recent
B.pick_cutoff=cutoff_year

def insert_quarterly(table,predicate,mode=None):
    # The caller passes a YYYYMM comparison against an annual cutoff.
    # Convert this *insertion predicate only*, never the evaluated SSB SQL.
    predicate=predicate.replace('toYYYYMM(LO_ORDERDATE)','toYear(LO_ORDERDATE)')
    t=time.monotonic()
    materialize='' if mode is None else f'materialize_statistics_on_insert={mode},'
    for quarter in (1,2,3,4):
        stmt=(f'INSERT INTO ssb.{table} SELECT * FROM ssb.raw WHERE ({predicate}) '
              f'AND toQuarter(LO_ORDERDATE)={quarter} SETTINGS {materialize}'
              'max_threads=2,max_insert_threads=1,'
              'min_insert_block_size_rows=100000,min_insert_block_size_bytes=1')
        B.sql(stmt)
    B.log('QUARTERLY_LOAD',table,'batches',4,'stat_mode',mode,'elapsed_s',round(time.monotonic()-t,2))
B.insert=insert_quarterly

def map_annual(label,tables,cutoff):
    result=original_partmap(label,tables,cutoff)
    for v in result.values():
        v['per_year']=[{'year':x['month'],'rows':x['rows'],'stats':x['stats']} for x in v.pop('per_month')]
    B.dump(label+'/state.json',result)
    return result
B.partmap=map_annual


# Fixed cross-run golden answers from the earlier monthly-layout run.
# An answer mismatch invalidates the annual-layout comparison before acceptance claims.
GOLDEN_ANSWERS = {
  "1": "053433cc055d868f14188229dd9e571169167eabfa4adc8e5f01867f0c2f91da",
  "2": "66e3927b8eb61a212aefa1b0e1432800dc864c2d53ef3b9f4b1b10d6be5ec4e3",
  "3": "6bdb98a584e5061b655a5601c68a46deae8f096d79d8fad4ccaa1d4765034a62",
  "4": "e3818b80f4530cfa439a78e0dd830a671d843c4460e39e46bac2cbe5f7947c72",
  "5": "f8e644c2d457ef1afcbb7ea74d1b83c0928594f1990c5a3ab848df6b9a8437ab",
  "6": "e830c886923f5444759713327f8eed88a45622f1737db20e80a6645f9bec42d9",
  "7": "da2e39bca57fd010047445277fa7f42a6bddb6435c49f328430e291084935a83",
  "8": "b480d3db660d64045a8b35adf974c36429b90929a83e191c0db31deb6b4cafb7",
  "9": "b0242ca170818735162c0ea18fa26d7a7bf44c3cddd009117ee44537f5d21998",
  "10": "72dcf540d01db608f34ca87fab3485a18dd64aa18abccaf09506dfb8afa14871",
  "11": "c668e39656898b6c232b71c48a94f70d4e7b5605f8592c7b81b8e80f5241c5",
  "12": "9c4ff6580251ffdda1c810bdfc0998072257881388dfbdfc1f557305cd6ff2c4",
  "13": "5de16226f8f1d5de7872cbe86b3339c2c3ffc9bab9fa4802f9ff062795cc3259"
}
original_measure=B.measure
def measure_verified(phase,tables,queries,nreps):
    for k in (2,10):
        if 'toYYYYMM(LO_ORDERDATE)' not in queries[k-1]:
            raise RuntimeError('PINNED_SQL_PREDICATE_CHANGED q'+str(k))
    result=original_measure(phase,tables,queries,nreps)
    if phase in ('post_ingest','later'):
        hashes=json.loads((R/phase/'answers.json').read_text())
        for q in range(1,14):
            for table in tables:
                for arm in ('cache_on','cache_off','stats_off'):
                    key=f'{q}/{table}/{arm}'
                    if hashes.get(key)!=GOLDEN_ANSWERS[str(q)]:
                        raise RuntimeError('CROSS_RUN_ANSWER_MISMATCH '+key)
        B.dump(phase+'/cross_run_answer_check.json',{'equal_to_monthly_reference':True,'13_queries':True,'cells':len(hashes)})
        B.log('CROSS_RUN_ANSWER_EQUALITY',phase,len(hashes))
    return result
B.measure=measure_verified

if __name__=='__main__':
    B.dump('PREREGISTERED.json',{
      'layout':'annual partitioning','batches':'four quarterly inserts per operation',
      'merge_policy':'normal merging during load and idle; symmetrically frozen only for paired measurements',
      'control':'established dated-history four-table design and fixed policy comparison',
      'sqls':'13 unchanged pinned SSB queries; all results must match monthly reference','primary':'cache-off inherited vs rebuilt comparison',
      'rounds_initial':6,'rounds_followup':4,'no_forced_optimize':True,
      'scope':'single public runner, one database system, source SF1'})
    import sys
    if '--preflight' in sys.argv:
        assert any(isinstance(x,str) and 'toYear' in x for x in create_year.__code__.co_consts)
        assert B.RUNS==6
        assert B.sql is original_sql
        assert len(GOLDEN_ANSWERS)==13
        print('C50_PREFLIGHT_OK',flush=True)
    else:
        B.main()
        path=R/'FINAL_STATUS.json'
        result=json.loads(path.read_text())
        result['cutoff_year']=result.pop('cutoff_month')
        result['layout']='annual date partitions'
        result['arrival_schedule']='quarterly incremental batches'
        result['limitations']=[x for x in result['limitations'] if not x.startswith('Monthly partitioning')]
        result['limitations'].append('Annual partitions and quarterly batches are an analytical control, not a sampled production deployment')
        path.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
        print('C50_FINAL_SUCCESS',result['cutoff_year'],flush=True)