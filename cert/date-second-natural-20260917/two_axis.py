#!/usr/bin/env python3
"""Source-pinned relational invariant for two unmodified historical UARTs.
Certification ports are only exposed in disposable derived copies; functional RTL stays intact.
"""
from pathlib import Path
import json, hashlib, re, sys

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('cert-out/two-axis')
OUT.mkdir(parents=True, exist_ok=True)
PORTS = [('cert_state','[3:0]','state'),
         ('cert_baud','[TIMING_BITS-1:0]','baud_counter'),
         ('cert_zero','','zero_baud_counter'),
         ('cert_busy','','r_busy'),
         ('cert_data','[7:0]','lcl_data')]

def instrument(side):
    original = (OUT / (side + '.original.v')).read_text()
    assert original.count('module txuartlite') == 1
    cut = re.search(r'`ifdef\s+FORMAL\b', original)
    assert cut, (side, 'FORMAL block missing')
    # Strip only the original FORMAL block from the disposable proof copy;
    # obligation analysis uses the untouched originals separately.
    src = original[:cut.start()]
    src = src.replace('module txuartlite', 'module '+side+'_txuartlite', 1)
    begin = src.index('module '+side+'_txuartlite')
    close = src.find(');', begin)
    assert close >= 0, 'module header missing'
    if side == 'old':
        insertion = ', '+', '.join(x[0] for x in PORTS)
        src = src[:close] + insertion + src[close:]
        close += len(insertion)
        decls = '\n'.join('output wire '+(width+' ' if width else '')+name+';' for name,width,_ in PORTS)
        src = src[:close+2] + '\n'+decls+'\n' + src[close+2:]
    else:
        insertion = ',\n' + ',\n'.join('output wire '+(width+' ' if width else '')+name for name,width,_ in PORTS) + '\n'
        src = src[:close]+insertion+src[close:]
    src += '\n' + '\n'.join('assign '+name+' = '+sig+';' for name,_,sig in PORTS) + '\nendmodule\n'
    (OUT / (side+'.cert.v')).write_text(src)
    return dict(source_sha256=hashlib.sha256(original.encode()).hexdigest(),
                copy_sha256=hashlib.sha256(src.encode()).hexdigest(),
                formal_block_removed_in_instrumented_copy=True)

meta = {side:instrument(side) for side in ('old','new')}
(OUT/'source-manifest.json').write_text(json.dumps(meta,indent=2,sort_keys=True)+'\n')

def wrapper(mutant=False):
    w = ['`default_nettype none',
         'module uart_pair(input wire i_clk, input wire i_wr, input wire [7:0] i_data,',
         ' output wire joint, output wire output_equal, output wire legal);',
         'wire ot0, ot1, ob0, ob1;',
         'wire [3:0] os, ns;',
         'wire [4:0] oc, nc;',
         'wire oz,nz,orbusy,nrbusy;',
         'wire [7:0] od,nd;']
    for prefix,label in (('o','old'),('n','new')):
        out = ('ot0','ob0') if prefix=='o' else ('ot1','ob1')
        w += [label+'_txuartlite #(.TIMING_BITS(5),.CLOCKS_PER_BAUD(8)) '+prefix+'(',
              ' .i_clk(i_clk),.i_wr(i_wr),.i_data(i_data),',
              ' .o_uart_tx('+out[0]+'),.o_busy('+out[1]+'),',
              ' .cert_state('+prefix+'s),.cert_baud('+prefix+'c),',
              ' .cert_zero('+prefix+'z),.cert_busy('+prefix+'rbusy),.cert_data('+prefix+'d));']
    w += ["assign legal = ((os <= 4'd9)||(os == 4'd15)) && ((ns <= 4'd9)||(ns == 4'd15));",
          'assign output_equal = (ot0 == '+ ('(ot1 ^ i_wr)' if mutant else 'ot1')+') && (ob0 == ob1);',
          'assign joint = legal && output_equal && (os==ns) && (oc==nc) && (oz==nz) && (orbusy==nrbusy) && (od==nd);',
          'endmodule','`default_nettype wire']
    return '\n'.join(w)+'\n'
(OUT/'uart_pair.v').write_text(wrapper())
(OUT/'uart_pair_mutant.v').write_text(wrapper(mutant=True))
(OUT/'prove.ys').write_text('''read_verilog -formal old.cert.v new.cert.v uart_pair.v
hierarchy -check -top uart_pair
proc
flatten
opt
sat -verify -tempinduct -prove joint 1 -seq 1 -maxsteps 8 -show-inputs -show-outputs
''')
(OUT/'mutation.ys').write_text('''read_verilog -formal old.cert.v new.cert.v uart_pair_mutant.v
hierarchy -check -top uart_pair
proc
flatten
opt
sat -falsify -prove output_equal 1 -seq 1 -show-inputs -show-outputs
''')
print('PAIR_GENERATED',json.dumps(meta,sort_keys=True))
