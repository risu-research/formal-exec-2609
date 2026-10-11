#!/usr/bin/env python3
"""Stage 47 prospective test: frozen query-kind policy and held-out sequential streams.

Registered before performance collection. All arms use the same fresh, 26.8-built
physical tables and the exact same SQL request trace and order in each scenario.
Rules: prune=(use_statistics 0), both=(1), selective=join 1 / ssb 0.
Part pruning remains ON and stats cache OFF in all arms. No concurrency/QPS claims.
"""
import csv, hashlib, json, os, pathlib, random, statistics, subprocess, sys, time
from collections import defaultdict

CAP = int(os.environ.get('C47_CAP','300000'))
assert CAP in (300000, 1500000)
N = 200 if CAP == 300000 else 400
REPS = 2
ARMS = ('prune','both','selective')
JOIN_Q = (3,5,10,12)
FAMILIES = ((1,2,3),(4,5,6),(7,8,9,10),(11,12,13))
# Predeclared before observing any Stage 47 measurement.
SCENARIOS = (
  [('mo_low','mo',.25,False),('mo_high','mo',.40,False),
   ('mo_high_noq5','mo',.40,True),('yr_high','yr',.10,False)]
  if CAP==300000 else
  [('mo_low','mo',.01,False),('mo_high','mo',.025,False),
   ('mo_high_noq5','mo',.025,True),('yr_high','yr',.01,False)]
)
R=pathlib.Path('c47_'+str(CAP)).resolve();R.mkdir(parents=True,exist_ok=True)

def save(rel,data):
 p=R/rel;p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(data,sort_keys=True,indent=2)+'\n')
def print_status(*args):print('C47',*args,flush=True)
def pctl(v,fraction):
 s=sorted(v);k=(len(s)-1)*fraction;a=int(k)
 return s[a]+(s[min(a+1,len(s)-1)]-s[a])*(k-a)

def initial_tasks(layout,share,scenario):
 j=round(N*share);s=N-j
 counts=[s//4+int(i<s%4) for i in range(4)]
 ss=[]
 for group,count in zip(FAMILIES,counts):
  ss.extend(('ssb',group[i%len(group)]) for i in range(count))
 js=[('join',JOIN_Q[i%len(JOIN_Q)]) for i in range(j)]
 stream=ss+js
 random.Random(470000+CAP+sum(map(ord,scenario))).shuffle(stream)
 assert len(stream)==N and sum(t=='join' for t,q in stream)==j
 return stream

def make_trace(layout,share,scenario,without_q5):
 # Every Q5-free trace is identical to its Q5-containing parent except
 # at Q5 positions. It retains the same JOIN count and SSB positions.
 parent=scenario.replace('_noq5','')
 x=initial_tasks(layout,share,parent)
 if without_q5:
  alternatives=(3,10,12);n=0
  for i,(t,q) in enumerate(x):
   if (t,q)==('join',5):
    x[i]=('join',alternatives[n%len(alternatives)]);n+=1
 assert len(x)==N and (not without_q5 or not any(z==('join',5) for z in x))
 return x

def manifest():
 out={'version':'C47-prospective-1','cap':CAP,'n_requests_per_arm_round':N,
      'measured_rounds':REPS,'arms':list(ARMS),'join_sqls':list(JOIN_Q),
      'ssb_families':[list(f) for f in FAMILIES],
      'route':{'prune':'all queries use_statistics=0',
         'both':'all queries use_statistics=1',
         'selective':'SSB use_statistics=0; JOIN use_statistics=1'},
      'fixed_settings':{'use_statistics_for_part_pruning':1,'use_statistics_cache':0,
         'use_query_condition_cache':0},
      'gates':{'mean_regression_block_if_ratio_gt':1.10,
               'sql_regression_threshold_ge':1.5,
               'number_distinct_sqls_to_block':2},
      'scenario_list':[],
      'claim_scope':'unseen trace orders of previously explored SQL and input distribution, no held-out query families or concurrent throughput'}
 for name,layout,share,noq5 in SCENARIOS:
  xs=make_trace(layout,share,name,noq5)
  out['scenario_list'].append({'name':name,'layout':layout,'join_share':share,
         'no_q5':noq5,'realized_join_share':sum(x[0]=='join' for x in xs)/N,
         'q5_count':sum(x==('join',5) for x in xs),
         'stream_sha256':hashlib.sha256(json.dumps(xs).encode()).hexdigest(),
         'sql_counts':{kind+str(q):xs.count((kind,q)) for kind in ('ssb','join') for q in
                         (range(1,14) if kind=='ssb' else JOIN_Q) if (kind,q) in xs}})
 return out

def policy_for(kind,policy):
 if policy=='selective':return 'both' if kind=='join' else 'prune'
 return policy

def analysis(records,walls,save_output=True):
 cells=defaultdict(list)
 for x in records:cells[(x['scenario'],x['arm'])].append(x)
 report=[];summary=[]
 for name,layout,share,noq5 in SCENARIOS:
  d={arm:cells[(name,arm)] for arm in ARMS}
  for arm,vals in d.items():
   if len(vals)!=N*REPS:raise RuntimeError(f'incomplete {name} {arm} {len(vals)}')
   w=[x['wall_ms'] for x in walls if x['scenario']==name and x['arm']==arm]
   if len(w)!=REPS:raise RuntimeError('missing wall')
   summary.append({'scenario':name,'layout':layout,'join_share':share,'arm':arm,
      'count':len(vals),'mean_ms':statistics.mean(x['ms'] for x in vals),
      'p95_ms':pctl([x['ms'] for x in vals],.95),'wall_total_ms':sum(w),
      'q5_n':sum(x['workload']=='join' and x['q']==5 for x in vals),
      'stat_load_total_ms':sum(x['stat_us'] for x in vals)/1000})
  for arm in ('both','selective'):
   ratios={}
   for kind in ('ssb','join'):
    for q in (range(1,14) if kind=='ssb' else JOIN_Q):
     baseline=[x['ms'] for x in d['prune'] if (x['workload'],x['q'])==(kind,q)]
     compared=[x['ms'] for x in d[arm] if (x['workload'],x['q'])==(kind,q)]
     if baseline:
      if len(baseline)!=len(compared):raise RuntimeError('SQL receipts differ')
      ratios[kind+str(q)]={'n':len(baseline),'ratio':statistics.median(compared)/max(.001,statistics.median(baseline))}
   count=sum(v['ratio']>=1.5 for v in ratios.values() if v['n']>=2)
   old=statistics.mean(x['ms'] for x in d['prune'])
   new=statistics.mean(x['ms'] for x in d[arm])
   paired=[]
   for rnd in range(REPS):
    a=statistics.mean(x['ms'] for x in d['prune'] if x['round']==rnd)
    b=statistics.mean(x['ms'] for x in d[arm] if x['round']==rnd)
    paired.append(b/a)
   report.append({'scenario':name,'comparison':arm+'_vs_prune','layout':layout,
       'share':share,'no_q5':noq5,'n_calls':N*REPS,
       'mean_baseline_ms':old,'mean_policy_ms':new,'ratio':new/old,
       'regressed_distinct_sqls':count,'gate':'BLOCK' if new/old>1.10 or count>=2 else 'APPROVE',
       'per_round_ratio':paired,'per_sql':ratios})
 if save_output:
  save('metrics.json',summary);save('decision_matrix.json',report)
  for x in report:print_status('DECISION',x['scenario'],x['comparison'],round(x['ratio'],4),x['regressed_distinct_sqls'],x['gate'])
 return report

def preflight():
 m=manifest();save('PREREGISTERED.json',m)
 known={x['name']:x for x in m['scenario_list']}
 for name,layout,share,noq5 in SCENARIOS:
  xs=make_trace(layout,share,name,noq5)
  if noq5:
   p=make_trace(layout,share,name.replace('_noq5',''),False)
   assert all((a==b) or (a==('join',5) and b[0]=='join') for a,b in zip(p,xs)), 'Q5-ablation changed other positions'
  assert hash_json(xs)==known[name]['stream_sha256']
  save('traces/'+name+'.json',{'name':name,'tasks':xs,'sha256':hash_json(xs),
    'q5_count':known[name]['q5_count'],'exact_same_stream_all_policies':True})
 for kind in ('ssb','join'):
  assert policy_for(kind,'prune')=='prune'
  assert policy_for(kind,'both')=='both'
  assert policy_for(kind,'selective')==('both' if kind=='join' else 'prune')
 print_status('PREFLIGHT_OK',CAP,len(SCENARIOS),'traces',len(ARMS),'policies')
 return m

def hash_json(v):return hashlib.sha256(json.dumps(v).encode()).hexdigest()

def benchmark():
 # Dependencies are imported after pure preflight to keep the short smoke independent of Docker/network.
 import db_c46 as B
 assert B.CAP==CAP
 S=B.S
 B.R=R; B.P.R=R; B.T.ROOT=R; B.C.R=R
 S.ROOT=R; S.base.ROOT=R
 S.CT='c47_ch'; S.VOL='c47_volume'
 S.base.VERSIONS=[S.OLD,S.OLD]
 B.ARMS=(('prune',0,1,0),('both',1,1,0))
 print_status('SOURCE_PREPARE',CAP)
 for v in (S.OLD,S.NEW):S.run(['docker','pull','clickhouse/clickhouse-server:'+v],timeout=500)
 sq,jq,defs,native=B.get_data()
 try:
  fixed=B.setup(sq,jq,defs,native)
  B.verify(sq,jq)
  # Warm both physical layouts under both settings, separately from measured receipts.
  for layout in ('mo','yr'):
   for kind,queries in (('ssb',range(1,14)),('join',JOIN_Q)):
    for q in queries:
     for setting in B.ARMS:
      B.sql(B.statement(kind,q,layout,'f',setting,sq,jq),fmt='Null')
  print_status('DATA_VERIFIED_AND_WARMED',CAP)
  def qstatement(kind,q,layout,arm,tag=''):
   setting=B.ARMS[0] if policy_for(kind,arm)=='prune' else B.ARMS[1]
   return B.statement(kind,q,layout,'f',setting,sq,jq,tag)
  trace_map={name:make_trace(layout,share,name,without_q5) for name,layout,share,without_q5 in SCENARIOS}
  # Newly seeded schedule and order balancing; does not retune the policy.
  records=[];walls=[]
  for name,layout,share,without_q5 in SCENARIOS:
   stream=trace_map[name]
   for rnd in range(REPS):
    arms=list(ARMS)
    random.Random(475000+CAP+sum(map(ord,name))+rnd).shuffle(arms)
    for arm in arms:
     statements=[];start=len(records)
     for i,(kind,q) in enumerate(stream):
      tag=f'C47_{CAP}_{name}_{rnd}_{arm}_{i}'
      statements.append(qstatement(kind,q,layout,arm,tag)+';')
      records.append({'tag':tag,'scenario':name,'layout':layout,'join_share':share,
        'no_q5':int(without_q5),'round':rnd,'arm':arm,'index':i,'workload':kind,'q':q,
        'stats_setting':1 if policy_for(kind,arm)=='both' else 0})
     t0=time.monotonic()
     p=S.run(['docker','exec','-i',S.CT,'clickhouse-client','--database','ssb',
       '--multiquery','--format','Null'],inp=('\n'.join(statements)+'\n').encode(),timeout=1350,check=False)
     wall=(time.monotonic()-t0)*1000
     if p.returncode:raise RuntimeError('batch failed '+name+' '+arm+' '+p.stderr.decode(errors='replace')[-1200:])
     walls.append({'scenario':name,'round':rnd,'arm':arm,'n_requests':N,'wall_ms':wall})
     print_status('STREAM_DONE',CAP,name,'r'+str(rnd),arm,'requests',len(records)-start)
   # Save durable partial metadata if a later workload fails.
   save('stream_wall_partial.json',walls)
  B.sql('SYSTEM FLUSH LOGS')
  raw=B.sql("SELECT log_comment,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
       "FROM system.query_log WHERE type='QueryFinish' AND startsWith(log_comment,'C47_') FORMAT JSONEachRow")
  logger={}
  for line in raw.splitlines():
   if not line:continue
   z=json.loads(line);tag=z['log_comment']
   if tag in logger:raise RuntimeError('Duplicate query receipt: '+tag)
   logger[tag]=z
  if len(logger)!=len(records):raise RuntimeError('Missing query_log receipts '+str(len(logger))+'/'+str(len(records)))
  for row in records:
   z=logger[row['tag']];e=z.get('ProfileEvents') or {}
   row.update({'ms':z['query_duration_ms'],'read_rows':z['read_rows'],
      'read_bytes':z.get('read_bytes',0),'memory_usage':z.get('memory_usage',0),
      'stat_us':e.get('LoadedStatisticsMicroseconds',0),
      'plan_us':e.get('QueryPlanOptimizeMicroseconds',0),
      'selected_marks':e.get('SelectedMarks',0)})
  with (R/'receipts.csv').open('w',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
  save('stream_wall.json',walls)
  analysis(records,walls)
  final=B.dataset_snapshot('after_replay')
  if fixed!=final:raise RuntimeError('Physical part changes across measurement')
  # Check the precise pre-registered request counts and SQL identities.
  for name,layout,share,noq5 in SCENARIOS:
   template=trace_map[name]
   for arm in ARMS:
    for rnd in range(REPS):
     subset=[x for x in records if x['scenario']==name and x['arm']==arm and x['round']==rnd]
     if [(x['workload'],x['q']) for x in subset]!=template:raise RuntimeError('sequence mismatch')
  save('FINAL_STATUS.json',{'success':True,'cap':CAP,'query_receipts':len(records),
     'physical_unchanged':True,'correct_sql_results':True,'prospective_policy_frozen':True,
     'engine_version':S.NEW,'scope':'serial replay, unseen randomized requests, previously studied SQL'})
  print_status('SUCCESS',CAP,len(records))
 finally:
  S.run(['docker','rm','-f',S.CT],check=False,timeout=120)

if __name__=='__main__':
 preflight()
 if '--preflight' not in sys.argv:
  benchmark()