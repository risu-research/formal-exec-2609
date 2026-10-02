#!/usr/bin/env python3
import csv,json
from pathlib import Path
from datetime import datetime,timezone,date
from collections import Counter

BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_context_audit_20261002'/'audit.csv'
OUT=BASE/'results'/'rpki_lifecycle_asymmetry_20261002';OUT.mkdir(parents=True,exist_ok=True)
CUTOFF=date(2026,10,2)

def dt(s): return datetime.fromisoformat(s.replace('Z','+00:00'))
def d(s): return date.fromisoformat(s)

def ranges(js):
    try:return json.loads(js or '[]')
    except:return []

def containing_ranges(records,asn,event_date):
    out=[]
    for r in records:
        if int(r.get('asn') or 0)!=asn:continue
        for a,b in r.get('date_ranges') or []:
            try:
                aa,bb=d(a),d(b)
                if aa<=event_date<=bb:out.append((aa,bb,r.get('prefix'),r.get('max_len')))
            except:pass
    return out

def ranges_covering_cutoff(records,asn):
    out=[]
    for r in records:
        if int(r.get('asn') or 0)!=asn:continue
        for a,b in r.get('date_ranges') or []:
            try:
                aa,bb=d(a),d(b)
                if aa<=CUTOFF<=bb:out.append((aa,bb,r.get('prefix'),r.get('max_len')))
            except:pass
    return out

def main():
    rows=list(csv.DictReader(open(INP,encoding='utf8'))); out=[]
    for r in rows:
        clean=(str(r['same_org']).lower()!='true' and int(r['A_reappearing_peer_n'] or 0)==0 and int(r['transfer_near30_n'] or 0)==0 and int(r['rrc_n'] or 0)>=3 and bool(r['B_median']))
        if not clean:continue
        t=dt(r['B_median']);ed=t.date();A=int(r['A']);B=int(r['B'])
        ar=ranges(r['A_roa_history_json']);br=ranges(r['B_roa_history_json'])
        bcr=containing_ranges(br,B,ed); acr=containing_ranges(ar,A,ed); acut=ranges_covering_cutoff(ar,A)
        bstart=min((x[0] for x in bcr),default=None)
        # date-granular conservative lead: if auth range started at least previous calendar day, definitely preauthorized before event-day start.
        lead_floor_days=(ed-bstart).days if bstart else None
        a_cut=bool(acut)
        retirement_censor_days=(CUTOFF-ed).days if a_cut else None
        if lead_floor_days is None: act='no_covering_B_history'
        elif lead_floor_days>=1: act='B_preauthorized_before_event_day'
        else: act='B_auth_began_event_day_or_earlier_unknown_within_day'
        if a_cut: ret='A_authorization_right_censored_at_cutoff'
        elif acr: ret='A_authorization_retired_before_cutoff'
        else: ret='A_history_unresolved'
        out.append({'prefix':r['prefix'],'A':A,'B':B,'B_median':r['B_median'],'rrc_n':r['rrc_n'],'peer_n':r['peer_n'],
                    'B_auth_range_start':bstart.isoformat() if bstart else '','B_auth_lead_floor_calendar_days':lead_floor_days if lead_floor_days is not None else '',
                    'activation_class':act,'A_valid_at_event_date':bool(acr),'A_valid_at_cutoff_20261002':a_cut,
                    'retirement_class':ret,'retirement_lag_right_censor_floor_calendar_days':retirement_censor_days if retirement_censor_days is not None else ''})
    if out:
        with open(OUT/'clean_core.csv','w',newline='',encoding='utf8') as f:w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
    leads=[int(x['B_auth_lead_floor_calendar_days']) for x in out if x['B_auth_lead_floor_calendar_days']!='']
    cens=[int(x['retirement_lag_right_censor_floor_calendar_days']) for x in out if x['retirement_lag_right_censor_floor_calendar_days']!='']
    s={'clean_core_n':len(out),'activation_classes':dict(Counter(x['activation_class'] for x in out)),
       'B_preauthorized_at_least_previous_day_n':sum(x['activation_class']=='B_preauthorized_before_event_day' for x in out),
       'B_auth_same_event_calendar_day_n':sum(x['activation_class']=='B_auth_began_event_day_or_earlier_unknown_within_day' for x in out),
       'A_valid_at_event_date_n':sum(x['A_valid_at_event_date'] for x in out),
       'A_still_valid_at_cutoff_n':sum(x['A_valid_at_cutoff_20261002'] for x in out),
       'retirement_classes':dict(Counter(x['retirement_class'] for x in out)),
       'B_lead_floor_calendar_days_median':sorted(leads)[len(leads)//2] if leads else None,
       'retirement_right_censor_floor_days_median':sorted(cens)[len(cens)//2] if cens else None,
       'guardrail':'ROA date ranges are date-granular observations. A range ending at the Oct 2 study cutoff is treated as right-censored, not as an actual withdrawal date. Same-day B authorization cannot be ordered against BGP without RPKISPOOL sub-day CCR snapshots.'}
    (OUT/'summary.json').write_text(json.dumps(s,indent=2),encoding='utf8');print(json.dumps(s,indent=2))
if __name__=='__main__':main()
