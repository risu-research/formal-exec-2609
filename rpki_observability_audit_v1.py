import csv, json, math, os, statistics
from collections import Counter, defaultdict
from datetime import date

SEL='results/rpki_outcomeblind_exact_v1_20261002/selection.csv'
EXACT='results/rpki_outcomeblind_exact_v1_20261002/exact_reconstruction.csv'
CENSUS='results/rpki_full_census_fast_closeout_20261002/census_weekly_rov.csv'
OUT='results/rpki_observability_audit_v1_20261002'
os.makedirs(OUT, exist_ok=True)

def read_csv(path):
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))

def key(r):
    return (r['event_date'], r['representative_prefix'], str(r['A']), str(r['B']))

def fnum(x):
    try: return float(x)
    except: return float('nan')

def q(vals, p):
    vals=sorted(v for v in vals if math.isfinite(v))
    if not vals: return None
    if len(vals)==1: return vals[0]
    pos=(len(vals)-1)*p
    lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    if lo==hi: return vals[lo]
    return vals[lo]*(hi-pos)+vals[hi]*(pos-lo)

def describe(vals):
    vals=[v for v in vals if math.isfinite(v)]
    return {'n':len(vals),'mean':sum(vals)/len(vals) if vals else None,
            'median':q(vals,.5),'p25':q(vals,.25),'p75':q(vals,.75),
            'min':min(vals) if vals else None,'max':max(vals) if vals else None}

def sd(vals):
    vals=[v for v in vals if math.isfinite(v)]
    return statistics.stdev(vals) if len(vals)>1 else 0.0

def smd(a,b):
    a=[v for v in a if math.isfinite(v)]; b=[v for v in b if math.isfinite(v)]
    if not a or not b: return None
    va=sd(a)**2; vb=sd(b)**2
    sp=math.sqrt((va+vb)/2)
    return 0.0 if sp==0 else ((sum(a)/len(a))-(sum(b)/len(b)))/sp

def cliffs_delta(a,b):
    a=[v for v in a if math.isfinite(v)]; b=[v for v in b if math.isfinite(v)]
    if not a or not b: return None
    gt=lt=0
    for x in a:
        for y in b:
            if x>y: gt+=1
            elif x<y: lt+=1
    return (gt-lt)/(len(a)*len(b))

def cat_stats(res, unres):
    cats=sorted(set(res)|set(unres))
    nr=sum(res.values()); nu=sum(unres.values()); n=nr+nu
    tv=0.5*sum(abs(res.get(c,0)/nr-unres.get(c,0)/nu) for c in cats)
    col={c:res.get(c,0)+unres.get(c,0) for c in cats}
    chi2=0.0
    for rowtot,row in [(nr,res),(nu,unres)]:
        for c in cats:
            exp=rowtot*col[c]/n
            if exp>0: chi2+=(row.get(c,0)-exp)**2/exp
    k=min(2-1, len(cats)-1)
    v=math.sqrt(chi2/(n*k)) if n and k>0 else 0.0
    return {'categories':cats,'resolved_counts':dict(res),'unresolved_counts':dict(unres),
            'resolved_n':nr,'unresolved_n':nu,'total_variation':tv,'cramers_v':v}

def prefixlen(p):
    return int(p.split('/')[-1])

def month(s): return s[:7]
def days_from(s, origin):
    y,m,d=map(int,s.split('-')); return (date(y,m,d)-origin).days

sel=read_csv(SEL); exact=read_csv(EXACT); census=read_csv(CENSUS)
assert len(sel)==419, len(sel)
ex={key(r):r for r in exact}; ce={key(r):r for r in census}
assert len(ex)==419, len(ex)
missing_ex=[key(r) for r in sel if key(r) not in ex]
missing_ce=[key(r) for r in sel if key(r) not in ce]
assert not missing_ex, missing_ex[:3]
assert not missing_ce, missing_ce[:3]

# frame frequencies capture whether an endpoint ASN/pair recurs elsewhere in the 7046-frame.
old_freq=Counter(str(r['A']) for r in census)
new_freq=Counter(str(r['B']) for r in census)
pair_freq=Counter((str(r['A']),str(r['B'])) for r in census)

rows=[]
origin=date.fromisoformat(min(r['event_date'] for r in sel))
for s in sel:
    k=key(s); e=ex[k]; c=ce[k]
    rrc=int(float(e['exact_rrc_n'] or 0))
    rec={
      'event_date':s['event_date'],'month':month(s['event_date']),
      'representative_prefix':s['representative_prefix'],'A':str(s['A']),'B':str(s['B']),
      'resolved_ge3':rrc>=3,'exact_rrc_n':rrc,
      'cluster_prefix_n':int(float(s['cluster_prefix_n'])),'prefix_len':prefixlen(s['representative_prefix']),
      'event_day_index':days_from(s['event_date'],origin),
      'pre_stable_obs_min':int(float(c['pre_stable_obs_min'])),
      'B_confirm_obs_min':int(float(c['B_confirm_obs_min'])),
      'oldA_frame_freq':old_freq[str(s['A'])], 'newB_frame_freq':new_freq[str(s['B'])],
      'pair_frame_freq':pair_freq[(str(s['A']),str(s['B']))],
      'A_rov_weekly':c['A_rov_weekly'],'B_rov_weekly':c['B_rov_weekly'],
      'weekly_state':c['A_rov_weekly']+'/'+c['B_rov_weekly'],
      'weekly_inversion':str(c['weekly_inversion']).lower()=='true'
    }
    rows.append(rec)

R=[r for r in rows if r['resolved_ge3']]; U=[r for r in rows if not r['resolved_ge3']]
assert len(R)==309 and len(U)==110, (len(R),len(U))

cont_specs=[
 ('event_day_index',False),('cluster_prefix_n',True),('prefix_len',False),
 ('pre_stable_obs_min',False),('B_confirm_obs_min',False),
 ('oldA_frame_freq',True),('newB_frame_freq',True),('pair_frame_freq',True)]
continuous={}
for name,log in cont_specs:
    ar=[float(r[name]) for r in R]; au=[float(r[name]) for r in U]
    tr=(lambda x: math.log1p(x)) if log else (lambda x:x)
    continuous[name]={
      'resolved':describe(ar),'unresolved':describe(au),
      'smd_on_'+('log1p' if log else 'raw'):smd([tr(x) for x in ar],[tr(x) for x in au]),
      'cliffs_delta':cliffs_delta(ar,au)
    }

month_stats=cat_stats(Counter(r['month'] for r in R), Counter(r['month'] for r in U))
cluster_cat=lambda x: '1' if x==1 else ('2-4' if x<=4 else ('5-9' if x<=9 else '10+'))
cluster_stats=cat_stats(Counter(cluster_cat(r['cluster_prefix_n']) for r in R), Counter(cluster_cat(r['cluster_prefix_n']) for r in U))
prefix_cat=lambda x: '<=23' if x<=23 else ('24' if x==24 else '>=25')
prefix_stats=cat_stats(Counter(prefix_cat(r['prefix_len']) for r in R), Counter(prefix_cat(r['prefix_len']) for r in U))
weekly_state_stats=cat_stats(Counter(r['weekly_state'] for r in R), Counter(r['weekly_state'] for r in U))

ri=sum(r['weekly_inversion'] for r in R); ui=sum(r['weekly_inversion'] for r in U)
pr=ri/len(R); pu=ui/len(U)
weekly_inv={'resolved':{'n':len(R),'inversions':ri,'rate':pr},
            'unresolved':{'n':len(U),'inversions':ui,'rate':pu},
            'risk_difference_resolved_minus_unresolved':pr-pu,
            'risk_ratio':(pr/pu if pu>0 else None)}

# resolution-level breakdown for transparency.
rrc_hist=Counter(r['exact_rrc_n'] for r in rows)

# Rank baseline effects; weekly ROV is reported separately because it is an outcome diagnostic.
effects=[]
for name,d in continuous.items():
    sv=[v for k,v in d.items() if k.startswith('smd_')][0]
    effects.append({'variable':name,'metric':'abs_smd','value':abs(sv) if sv is not None else None,'signed':sv})
for name,d in [('month',month_stats),('cluster_size_category',cluster_stats),('prefix_length_category',prefix_stats)]:
    effects.append({'variable':name,'metric':'cramers_v','value':d['cramers_v']})
effects=[x for x in effects if x['value'] is not None]
effects.sort(key=lambda x:x['value'], reverse=True)

summary={
 'design':{
   'panel_n':419,'resolved_ge3_n':309,'unresolved_lt3_n':110,
   'resolved_definition':'exact_rrc_n >= 3 from frozen RIPEstat BGPlay reconstruction',
   'baseline_covariates':'month, event timing, cluster size, prefix length, pre-stability, B-confirmation, full-frame old/new ASN and pair frequencies',
   'post_selection_diagnostic':'coarse weekly ROV state/inversion from frozen 7046 census',
   'inference':'descriptive effect sizes; no p-values because deterministic panel is not a probability sample'
 },
 'rrc_histogram':{str(k):v for k,v in sorted(rrc_hist.items())},
 'continuous':continuous,
 'month':month_stats,'cluster_size_category':cluster_stats,'prefix_length_category':prefix_stats,
 'weekly_state_postselection':weekly_state_stats,'weekly_inversion_postselection':weekly_inv,
 'ranked_baseline_effects':effects
}
with open(os.path.join(OUT,'summary.json'),'w',encoding='utf-8') as f: json.dump(summary,f,indent=2,sort_keys=True)

with open(os.path.join(OUT,'joined_419.csv'),'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
with open(os.path.join(OUT,'covariate_effects.csv'),'w',newline='',encoding='utf-8') as f:
    fields=['variable','metric','value','signed']; w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
    for x in effects: w.writerow({k:x.get(k,'') for k in fields})
with open(os.path.join(OUT,'month_counts.csv'),'w',newline='',encoding='utf-8') as f:
    cats=month_stats['categories']; w=csv.writer(f); w.writerow(['month','resolved','unresolved','resolved_share','unresolved_share'])
    for c in cats:
        a=month_stats['resolved_counts'].get(c,0); b=month_stats['unresolved_counts'].get(c,0)
        w.writerow([c,a,b,a/len(R),b/len(U)])

print(json.dumps({
 'resolved':len(R),'unresolved':len(U),
 'largest_baseline_effects':effects[:5],
 'month_tv':month_stats['total_variation'],'month_v':month_stats['cramers_v'],
 'weekly_state_tv':weekly_state_stats['total_variation'],'weekly_state_v':weekly_state_stats['cramers_v'],
 'weekly_inversion':weekly_inv
},indent=2))
