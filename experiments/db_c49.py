#!/usr/bin/env python3
"""Annual-partition natural-maintenance control with quarterly batch arrivals.
Keeps the established four-history, three-policy, paired-query experiment fixed.
One database, one runner; no claim about independent production populations.
"""
import json,pathlib,time
import db_c42 as B

R=pathlib.Path('s49')
R.mkdir(parents=True,exist_ok=True)
B.R=R;B.P.R=R;B.S.ROOT=R;B.S.base.ROOT=R
B.S.base.VERSIONS=[B.S.OLD,B.S.OLD]
B.S.CT='c49_ch';B.S.VOL='c49_vol'
B.RUNS=6
original_sql=B.sql
original_partmap=B.partmap

def sql_year(query,qid=None,fmt=None):
    return original_sql(query.replace('toYYYYMM(LO_ORDERDATE)','toYear(LO_ORDERDATE)'),qid=qid,fmt=fmt)
B.sql=sql_year

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

if __name__=='__main__':
    B.dump('PREREGISTERED.json',{
      'layout':'annual partitioning','batches':'four quarterly inserts per operation',
      'merge_policy':'normal merging during load and idle; symmetrically frozen only for paired measurements',
      'control':'established dated-history four-table design and fixed policy comparison',
      'sqls':'13 pinned SSB queries','primary':'cache-off inherited vs rebuilt comparison',
      'rounds_initial':6,'rounds_followup':4,'no_forced_optimize':True,
      'scope':'single public runner, one database system, source SF1'})
    import sys
    if '--preflight' in sys.argv:
        assert any(isinstance(x,str) and 'toYear' in x for x in create_year.__code__.co_consts)
        assert B.RUNS==6
        print('C49_PREFLIGHT_OK',flush=True)
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
        print('C49_FINAL_SUCCESS',result['cutoff_year'],flush=True)