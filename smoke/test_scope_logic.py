#!/usr/bin/env python3
"""Adversarial unit gates for the partial-order classifier.
These cases specifically kill line matching, count matching, and scalar stronger/weaker shortcuts.
"""
import z3
from tnf_scope import _relation

def rel(a,b,expect):
    got=_relation(a,b,2000)[0]
    if got!=expect: raise SystemExit(f'{expect} expected, got {got}')

a,b,c=z3.Bools('a b c')
rel(z3.And(a,b), z3.And(b,a), 'EQ')
rel(a, z3.And(a,b), 'CONTRACT')
rel(z3.And(a,b), a, 'EXPAND')
rel(a,b,'INCOMPARABLE')
# Duplicate/redundant target must not create a fake regression.
rel(z3.And(a,b), z3.And(a,b,z3.Or(a,b),a), 'EQ')
# A single revision may gain one condition while losing another.
rel(z3.And(a,b), z3.And(a,c), 'INCOMPARABLE')
print('PASS 6/6 semantic-order adversarial cases')
