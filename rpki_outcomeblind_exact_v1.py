#!/usr/bin/env python3
import csv, hashlib, ipaddress, io, json, lzma, math, re, time, urllib.parse, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
INP = BASE / 'results' / 'rpki_lifecycle_panel_v1_20261002' / 'screened_panel.csv'
OUT = BASE / 'results' / 'rpki_outcomeblind_exact_v1_20261002'
OUT.mkdir(parents=True, exist_ok=True)
UA = 'risu-rpki-outcomeblind-exact-v1/1.0 research-contact-moon1002'
EXACT_WORKERS = 8
RPKI_WORKERS = 8
MIN_RRCS = 3
TALS = ['afrinic.tal','apnic.tal','arin.tal','lacnic.tal','ripencc.tal']


def get_bytes(url, retries=5, timeout=120):
    err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            err = e
            time.sleep(min(12, 1.5 * (2 ** i)))
    raise RuntimeError(f'GET failed {url}: {err}')


def get_json(url, retries=5, timeout=120):
    return json.loads(get_bytes(url, retries=retries, timeout=timeout).decode('utf-8', 'replace'))


def to_ts(x):
    if x is None or x == '':
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if re.fullmatch(r'\d+(?:\.\d+)?', s):
        return float(s)
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def iso(t):
    if t is None:
        return ''
    return datetime.fromtimestamp(float(t), timezone.utc).isoformat().replace('+00:00', 'Z')


def day(x):
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    s = str(x).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        t = to_ts(s)
        return datetime.fromtimestamp(t, timezone.utc).date() if t is not None else None


def origin_from_path(path):
    if not path:
        return None
    vals = []
    if isinstance(path, list):
        for x in path:
            vals += re.findall(r'\d+', str(x))
    else:
        vals = re.findall(r'\d+', str(path))
    return int(vals[-1]) if vals else None


def exact_transition(r):
    p = r['representative_prefix']
    A = int(r['A']); B = int(r['B'])
    ed = date.fromisoformat(r['event_date'][:10])
    t0 = datetime.combine(ed - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc)
    t1 = datetime.combine(ed + timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc)
    q = urllib.parse.urlencode({
        'resource': p,
        'starttime': t0.isoformat().replace('+00:00','Z'),
        'endtime': t1.isoformat().replace('+00:00','Z'),
        'unix_timestamps': 'TRUE',
        'sourceapp': 'risu-rpki-study'
    })
    data = get_json('https://stat.ripe.net/data/bgplay/data.json?' + q).get('data', {})
    sources = {str(s.get('id')): s for s in data.get('sources', []) or []}
    state = {}
    for e in data.get('initial_state', []) or []:
        a = e.get('attrs', e) or {}
        sid = str(a.get('source_id', e.get('source_id', '')))
        tp = a.get('target_prefix', e.get('target_prefix'))
        if tp == p and sid:
            state[sid] = origin_from_path(a.get('path', e.get('path')))

    last_a_withdraw = {}
    hits = []
    for e in sorted(data.get('events', []) or [], key=lambda z: to_ts(z.get('timestamp')) or 0):
        t = to_ts(e.get('timestamp'))
        a = e.get('attrs', {}) or {}
        sid = str(a.get('source_id', e.get('source_id', '')))
        tp = a.get('target_prefix', e.get('target_prefix'))
        if t is None or tp != p or not sid:
            continue
        typ = e.get('type') or a.get('type')
        old = state.get(sid)
        if typ == 'W':
            if old == A:
                last_a_withdraw[sid] = t
            state[sid] = None
        elif typ == 'A':
            new = origin_from_path(a.get('path', e.get('path')))
            if new is None:
                continue
            if new == B and old != B:
                direct = old == A
                wa = last_a_withdraw.get(sid)
                via_withdraw = old is None and wa is not None and 0 <= t - wa <= 3600
                if direct or via_withdraw:
                    hits.append({
                        'source_id': sid,
                        'rrc': sources.get(sid, {}).get('rrc'),
                        'peer_asn': sources.get(sid, {}).get('as_number'),
                        'A_off': t if direct else wa,
                        'B_on': t,
                        'mode': 'direct' if direct else 'withdraw_then_B'
                    })
            state[sid] = new

    first = {}
    for h in hits:
        sid = h['source_id']
        if sid not in first or h['B_on'] < first[sid]['B_on']:
            first[sid] = h
    hits = list(first.values())
    if not hits:
        return {
            'representative_prefix': p, 'event_date': r['event_date'], 'A': A, 'B': B,
            'exact_peer_n': 0, 'exact_rrc_n': 0, 'B_route_median': '', 'B_route_spread_s': '',
            'direct_n': 0, 'withdraw_then_B_n': 0
        }
    bt = sorted(h['B_on'] for h in hits)
    at = sorted(h['A_off'] for h in hits)
    med = lambda x: x[(len(x)-1)//2]
    rrcs = sorted({str(h['rrc']) for h in hits if h.get('rrc') is not None})
    return {
        'representative_prefix': p, 'event_date': r['event_date'], 'A': A, 'B': B,
        'exact_peer_n': len(hits), 'exact_rrc_n': len(rrcs), 'rrcs': ','.join(rrcs),
        'B_route_first': iso(bt[0]), 'B_route_median': iso(med(bt)), 'B_route_last': iso(bt[-1]),
        'B_route_spread_s': bt[-1]-bt[0] if len(bt) > 1 else 0,
        'A_off_median': iso(med(at)),
        'direct_n': sum(h['mode']=='direct' for h in hits),
        'withdraw_then_B_n': sum(h['mode']!='direct' for h in hits)
    }


def subnet_of(observed, vrp_prefix):
    try:
        q = ipaddress.ip_network(observed, strict=False)
        v = ipaddress.ip_network(vrp_prefix, strict=False)
        return q.version == v.version and q.subnet_of(v)
    except Exception:
        return False


def matching_vrp(observed, origin, vrp_prefix, vrp_asn, maxlen):
    try:
        q = ipaddress.ip_network(observed, strict=False)
        v = ipaddress.ip_network(vrp_prefix, strict=False)
        mx = int(maxlen) if maxlen not in (None, '') else v.prefixlen
        return q.version == v.version and q.subnet_of(v) and q.prefixlen <= mx and int(vrp_asn) == int(origin)
    except Exception:
        return False


def roa_history(prefix):
    q = urllib.parse.urlencode({'prefix': prefix, 'exact': 'false', 'page_size': 1000})
    j = get_json('https://api.bgpkit.com/v3/roas/search?' + q)
    data = j.get('data', j.get('items', [])) if isinstance(j, dict) else []
    if isinstance(data, dict):
        data = data.get('data', data.get('items', data.get('results', [])))
    return data if isinstance(data, list) else []


def active_on(record, ed):
    ranges = record.get('date_ranges', record.get('dateRanges')) or []
    for x in ranges:
        if not isinstance(x, (list, tuple)) or len(x) < 2:
            continue
        a = day(x[0]); b = day(x[1])
        if a is not None and b is not None and a <= ed <= b:
            return True
    return False


def classify_from_history(records, prefix, origin, ed):
    active_covering = []
    for z in records:
        vp = z.get('prefix') or ''
        if not vp or not active_on(z, ed) or not subnet_of(prefix, vp):
            continue
        asn = z.get('asn', z.get('origin_asn', 0))
        try:
            asn = int(str(asn).upper().removeprefix('AS'))
        except Exception:
            asn = 0
        mx = z.get('max_len', z.get('maxLength', z.get('max_length')))
        active_covering.append((vp, asn, mx))
    if not active_covering:
        return 'notfound'
    if any(matching_vrp(prefix, origin, vp, asn, mx) for vp, asn, mx in active_covering):
        return 'valid'
    return 'invalid'


def classify_exact(r):
    if int(r.get('exact_rrc_n') or 0) < MIN_RRCS or not r.get('B_route_median'):
        return None
    p = r['representative_prefix']; A = int(r['A']); B = int(r['B'])
    ed = datetime.fromisoformat(r['B_route_median'].replace('Z','+00:00')).date()
    rec = roa_history(p)
    return {**r, 'exact_event_date': ed.isoformat(),
            'A_rov': classify_from_history(rec, p, A, ed),
            'B_rov': classify_from_history(rec, p, B, ed),
            'roa_records_n': len(rec)}


def archive_day(ed):
    rows = []
    y, m, d = ed.strftime('%Y'), ed.strftime('%m'), ed.strftime('%d')
    for tal in TALS:
        url = f'https://ftp.ripe.net/rpki/{tal}/{y}/{m}/{d}/roas.csv.xz'
        raw = get_bytes(url, retries=4, timeout=120)
        text = lzma.decompress(raw).decode('utf-8', 'replace')
        rd = csv.DictReader(io.StringIO(text))
        for z in rd:
            vp = (z.get('IP Prefix') or z.get('prefix') or '').strip()
            aa = (z.get('ASN') or z.get('asn') or '').strip().upper().removeprefix('AS')
            if not vp or not aa.isdigit():
                continue
            mx = z.get('Max Length') or z.get('maxLength') or z.get('max_length') or ''
            rows.append((vp, int(aa), mx))
    return rows


def archive_classify(vrps, prefix, origin):
    cov = [(vp, a, mx) for vp, a, mx in vrps if subnet_of(prefix, vp)]
    if not cov:
        return 'notfound'
    if any(matching_vrp(prefix, origin, vp, a, mx) for vp, a, mx in cov):
        return 'valid'
    return 'invalid'


def write_csv(path, rows):
    if not rows:
        path.write_text('', encoding='utf-8')
        return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader(); w.writerows(rows)


def main():
    if not INP.exists():
        raise SystemExit(f'missing input {INP}')
    source_rows = list(csv.DictReader(open(INP, encoding='utf-8')))

    # Selection freeze. Only routing/context identifiers are copied. No RPKI outcome field
    # is read or used to rank cases. We attempt the full clean 419-case audit panel.
    selected = []
    for r in source_rows:
        if r.get('context_class') != 'clean_context_proxy':
            continue
        selected.append({
            'event_date': r['event_date'],
            'representative_prefix': r['representative_prefix'],
            'A': r['A'], 'B': r['B'],
            'cluster_prefix_n': r.get('cluster_prefix_n','')
        })
    selected.sort(key=lambda r: (r['event_date'], r['representative_prefix'], int(r['A']), int(r['B'])))
    write_csv(OUT/'selection.csv', selected)
    selector_bytes = (OUT/'selection.csv').read_bytes()
    selection_sha = hashlib.sha256(selector_bytes).hexdigest()
    (OUT/'selection.sha256').write_text(selection_sha + '  selection.csv\n', encoding='utf-8')
    (OUT/'selection_protocol.json').write_text(json.dumps({
        'input': str(INP.relative_to(BASE)),
        'rule': "context_class == clean_context_proxy; include every case; sort by event_date,prefix,A,B",
        'fields_used': ['context_class','event_date','representative_prefix','A','B','cluster_prefix_n'],
        'forbidden_for_selection': ['A_auth_at_event','B_auth_at_event','B_auth_start','B_next_auth_start','A_auth_last_observed_date','A_linger_observed_days'],
        'selected_n': len(selected),
        'selection_sha256': selection_sha,
        'minimum_exact_rrcs': MIN_RRCS
    }, indent=2), encoding='utf-8')
    print('selection frozen', len(selected), selection_sha, flush=True)

    exact = []; exact_errors = []
    with ThreadPoolExecutor(max_workers=EXACT_WORKERS) as ex:
        fs = {ex.submit(exact_transition, r): r['representative_prefix'] for r in selected}
        for i, f in enumerate(as_completed(fs), 1):
            try:
                exact.append(f.result())
            except Exception as e:
                exact_errors.append({'prefix': fs[f], 'error': repr(e)})
            if i % 25 == 0 or i == len(fs):
                print('exact', i, '/', len(fs), 'errors', len(exact_errors), flush=True)
    exact.sort(key=lambda r: (r['event_date'], r['representative_prefix']))
    write_csv(OUT/'exact_reconstruction.csv', exact)
    (OUT/'exact_errors.json').write_text(json.dumps(exact_errors, indent=2), encoding='utf-8')

    exact3 = [r for r in exact if int(r.get('exact_rrc_n') or 0) >= MIN_RRCS and r.get('B_route_median')]
    classified = []; rpki_errors = []
    with ThreadPoolExecutor(max_workers=RPKI_WORKERS) as ex:
        fs = {ex.submit(classify_exact, r): r['representative_prefix'] for r in exact3}
        for i, f in enumerate(as_completed(fs), 1):
            try:
                z = f.result()
                if z: classified.append(z)
            except Exception as e:
                rpki_errors.append({'prefix': fs[f], 'error': repr(e)})
            if i % 25 == 0 or i == len(fs):
                print('rpki', i, '/', len(fs), 'errors', len(rpki_errors), flush=True)
    classified.sort(key=lambda r: (r['exact_event_date'], r['representative_prefix']))
    write_csv(OUT/'exact_rov.csv', classified)
    (OUT/'rpki_errors.json').write_text(json.dumps(rpki_errors, indent=2), encoding='utf-8')

    inversions = [r for r in classified if r['A_rov']=='valid' and r['B_rov']=='invalid']
    archive_results = []
    archive_errors = []
    by_date = {}
    for r in inversions:
        by_date.setdefault(r['exact_event_date'], []).append(r)
    # Independent archive reconstruction, cached once per transition date.
    for i, (ds, rs) in enumerate(sorted(by_date.items()), 1):
        ed = date.fromisoformat(ds)
        try:
            vrps = archive_day(ed)
            for r in rs:
                archive_results.append({
                    'representative_prefix': r['representative_prefix'], 'A': r['A'], 'B': r['B'],
                    'exact_event_date': ds,
                    'primary_A_rov': r['A_rov'], 'primary_B_rov': r['B_rov'],
                    'archive_A_rov': archive_classify(vrps, r['representative_prefix'], int(r['A'])),
                    'archive_B_rov': archive_classify(vrps, r['representative_prefix'], int(r['B'])),
                    'archive_vrp_n': len(vrps)
                })
        except Exception as e:
            archive_errors.append({'date': ds, 'case_n': len(rs), 'error': repr(e)})
        print('archive', i, '/', len(by_date), ds, flush=True)
    archive_results.sort(key=lambda r: (r['exact_event_date'], r['representative_prefix']))
    write_csv(OUT/'inversion_archive_crosscheck.csv', archive_results)
    (OUT/'archive_errors.json').write_text(json.dumps(archive_errors, indent=2), encoding='utf-8')

    matrix = Counter((r['A_rov'], r['B_rov']) for r in classified)
    reproduced = sum(r['archive_A_rov']=='valid' and r['archive_B_rov']=='invalid' for r in archive_results)
    exact_rrcs = Counter(int(r.get('exact_rrc_n') or 0) for r in exact)
    spreads = sorted(float(r['B_route_spread_s']) for r in exact3 if r.get('B_route_spread_s') not in ('',None))
    qtile = lambda vals,q: vals[int(math.ceil(q*(len(vals)-1)))] if vals else None
    summary = {
        'design': {
            'selection': 'all 419 clean-context cases from deterministic 500-case audit panel',
            'selection_outcome_blind': True,
            'selection_sha256': selection_sha,
            'exact_source': 'RIPEstat BGPlay / RIPE RIS',
            'exact_window': '+/-7 days around weekly event checkpoint',
            'exact_requirement': f'>={MIN_RRCS} distinct RIS RRCs with reconstructed A->B transition',
            'primary_rpki_source': 'BGPKIT historical ROA search, queried only after exact sample is frozen',
            'independent_crosscheck': 'RIPE NCC daily validated ROA archive across five trust anchors for primary inversions'
        },
        'selection_n': len(selected),
        'exact_attempted_n': len(selected),
        'exact_returned_n': len(exact),
        'exact_errors_n': len(exact_errors),
        'exact_any_transition_n': sum(int(r.get('exact_peer_n') or 0)>0 for r in exact),
        'exact_ge3_rrc_n': len(exact3),
        'exact_rrc_histogram': dict(sorted(exact_rrcs.items())),
        'rpki_classified_n': len(classified),
        'rpki_errors_n': len(rpki_errors),
        'joint_state_matrix': {f'{a}/{b}': n for (a,b),n in sorted(matrix.items())},
        'old_valid_new_invalid_n': len(inversions),
        'old_valid_new_invalid_fraction_of_classified': (len(inversions)/len(classified) if classified else None),
        'old_valid_new_invalid_fraction_of_selected_lower_bound': (len(inversions)/len(selected) if selected else None),
        'independent_archive_crosscheck_cases_n': len(archive_results),
        'independent_archive_reproduced_n': reproduced,
        'independent_archive_errors_n': len(archive_errors),
        'visibility_spread_seconds': {'n': len(spreads), 'median': qtile(spreads,.5), 'p90': qtile(spreads,.9), 'max': max(spreads) if spreads else None},
        'guardrails': [
            'No RPKI authorization field is used to select or rank cases.',
            'The full clean audit panel is attempted, avoiding a post-selection 100-150 case sample unless API failure makes the full run incomplete.',
            'Fractions over exact-resolved cases describe the deterministic audit panel, not Internet-wide prevalence.',
            'The selected-denominator fraction is reported only as a conservative lower bound when exact reconstruction is unresolved for some cases.'
        ]
    }
    (OUT/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
