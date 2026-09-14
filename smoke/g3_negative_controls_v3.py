#!/usr/bin/env python3
"""Execution-only Amendment-07 semantic overlay.

This module preserves every Amendment-05 negative-control mutation, candidate,
witness, replay, and pass/fail rule.  It adds exactly one frozen semantic
primitive to the TNF interpreter: unsigned width-4 Yosys $reduce_or -> 1-bit
Booleanization of A != 0.  No other unsupported primitive is authorized.
"""
from __future__ import annotations
import z3
import g3_negative_controls_v2 as v2

g = v2.g

_ORIGINAL_CELL = g.IR.cell

def _pi(v):
    if isinstance(v, int):
        return v
    try:
        return int(v, 2)
    except Exception:
        return int(v)

def _cell_with_frozen_reduce_or(self, cn, c):
    if c.get('type') != '$reduce_or':
        return _ORIGINAL_CELL(self, cn, c)
    if cn in self.cell_cache:
        return self.cell_cache[cn]
    con = c.get('connections', {})
    par = c.get('parameters', {})
    aw = _pi(par.get('A_WIDTH', len(con.get('A', []))))
    yw = _pi(par.get('Y_WIDTH', len(con.get('Y', []))))
    signed = _pi(par.get('A_SIGNED', 0))
    if aw != 4 or yw != 1 or signed != 0 or len(con.get('A', [])) != 4 or len(con.get('Y', [])) != 1:
        self.unsupported.add('$reduce_or')
        raise RuntimeError(f'G3 Amendment-07 permits only unsigned $reduce_or A_WIDTH=4 Y_WIDTH=1, got A_WIDTH={aw} Y_WIDTH={yw} A_SIGNED={signed}')
    a = self.sig(con['A'], False, cn + ':A')
    out = z3.If(a != z3.BitVecVal(0, a.size()), z3.BitVecVal(1, 1), z3.BitVecVal(0, 1))
    self.cell_cache[cn] = z3.simplify(out)
    return self.cell_cache[cn]

if not getattr(g.IR, '_g3_amendment07_reduce_or', False):
    g.IR.cell = _cell_with_frozen_reduce_or
    g.IR._g3_amendment07_reduce_or = True

if __name__ == '__main__':
    g.main()
