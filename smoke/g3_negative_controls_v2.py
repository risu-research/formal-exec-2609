#!/usr/bin/env python3
"""Execution-only Amendment 05 overlay for the frozen G3 negative controls.

No candidate order, semantic query, selection rule, witness rule, or pass/fail rule
is changed.  Only the RTLIL representation of NC1 constant-false is repaired for
clocked formal-monitor cells so that A and EN remain in the same temporal class.
"""
import copy
import g3_negative_controls as g


def registered_false(o, n):
    o = copy.deepcopy(o)
    cs = g.mod(o)['cells']
    target = cs[n]
    A = copy.deepcopy(target['connections']['A'])
    qb = A[0] if len(A) == 1 and isinstance(A[0], int) else None
    dff = None
    for dn, dc in sorted(cs.items()):
        if dc.get('type') == '$dff' and qb in dc.get('connections', {}).get('Q', []):
            if dff is not None:
                raise RuntimeError('ambiguous assumption dff')
            dff = (dn, dc)

    if dff is None:
        target['connections']['A'] = ['0']
        return o

    _, dc = dff
    q = g.mb(o) + 1
    dn = '__g3_nc1_false_dff__'
    while dn in cs:
        dn += 'x'
    cs[dn] = {
        'type': '$dff',
        'parameters': copy.deepcopy(dc.get('parameters', {})),
        'attributes': {},
        'port_directions': {'CLK': 'input', 'D': 'input', 'Q': 'output'},
        'connections': {
            'CLK': copy.deepcopy(dc['connections']['CLK']),
            'D': ['0'],
            'Q': [q],
        },
    }
    target['connections']['A'] = [q]
    return o


g.m1 = registered_false

if __name__ == '__main__':
    g.main()
