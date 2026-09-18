#!/usr/bin/env python3
"""Generate exactly four controlled Bill FIFO cells and one negative control.
Historical sources are immutable; the pkt_count mutation is intentionally synthetic.
"""
import hashlib,json,os
from pathlib import Path
up=Path(os.environ.get('G4_UPSTREAM','/tmp/g4-bill'))
out=Path(os.environ.get('G4_OUT','work/factorial'));out.mkdir(parents=True,exist_ok=True)
a=(up/'rtl/axis_pkt_fifo.sv').read_bytes();sby=(up/'formal/axis_pkt_fifo_bmc.sby').read_bytes()
h=lambda v:hashlib.sha256(v).hexdigest()
assert h(a)=='148a851add9dd29671b1f493cd01af54ab87ad6fa5a97a2511ebd8cfbca46475'
assert h(sby)=='84a723e9a63e85a29ffb6b8c7598c114781053c6f30e258a1c2c1e0350f788d0'
s=a.decode(); anchor='    // -------------------------------------------------------------------------\n    // GROUP 1 — AXI master protocol compliance (stable-until-accepted, no spurious).'
assume='''    // Original historical successor's environment contract, without cover edit.
    always @(posedge clk) begin
        if (rst_n) m_pkt_fits: assume ((wptr - commit_ptr) < DEPTH[ADDR_WIDTH:0]);
    end

'''
orig='    assign pkt_count = wr_pkts - rd_pkts;'
mut='    assign pkt_count = wr_pkts - rd_pkts + (wr_pkts != rd_pkts);'
ready='    assign s_axis_tready = !full;'
assert s.count(anchor)==s.count(orig)==s.count(ready)==1
assert 'assert (pkt_count' not in s and 'assume (pkt_count' not in s
manifest={'purpose':'Precommitted controlled 2x2: O=original successor assume, B=deliberate physical output bug; covers are not safety F; NOT natural historical mutation','upstream_parent':'8fd43ee5973422c6cb3c9e2cb293b8f3bd9067c3','original_rtl_sha256':h(a),'original_sby_sha256':h(sby),'cells':{}}
for o in range(2):
 for b in range(2):
  label=f'O{o}_B{b}';v=s.replace(anchor,assume+anchor,1) if o else s
  if b:v=v.replace(orig,mut,1)
  assert v.count('m_pkt_fits:')==o and v.count(mut)==b
  d=out/label;(d/'rtl').mkdir(parents=True,exist_ok=True);(d/'formal').mkdir(parents=True,exist_ok=True)
  (d/'rtl/axis_pkt_fifo.sv').write_bytes(v.encode());(d/'formal/axis_pkt_fifo_bmc.sby').write_bytes(sby)
  manifest['cells'][label]={'source_sha256':h(v.encode()),'sby_sha256':h(sby),'O':o,'B':b,'expected_count_after_one_packet':1+b}
  (out/f'sim_{label}.sv').write_text(v.replace('module axis_pkt_fifo #(',f'module axis_pkt_fifo_{o}{b} #(',1))
# A deliberate monitor-violating negative control; should be detected by original Yices BMC.
neg=s.replace(ready,'    assign s_axis_tready = full;',1)
d=out/'BAD_TREADY_CONTROL';(d/'rtl').mkdir(parents=True,exist_ok=True);(d/'formal').mkdir(parents=True,exist_ok=True)
(d/'rtl/axis_pkt_fifo.sv').write_text(neg);(d/'formal/axis_pkt_fifo_bmc.sby').write_bytes(sby)
manifest['cells']['BAD_TREADY_CONTROL']={'source_sha256':h(neg.encode()),'sby_sha256':h(sby),'expected':'FAIL: a_tready_iff_room'}
assert manifest['cells']['O0_B0']['source_sha256']==h(a)
assert manifest['cells']['O0_B1']['source_sha256']=='5e3872ec8eb141bf94ee730738061b14a09a3e1548f5ec990ad936601fba0e3b'
assert manifest['cells']['O1_B0']['source_sha256']=='55e62d53dc868bf4a68da4cc7bba0f343be1e6d041ae06b047f91f14aceb97e7'
assert manifest['cells']['O1_B1']['source_sha256']=='8800bc2fe7b3fa0bf347410ec8366e75fd549805c8cda079619ace682acff6e2'
(out/'SOURCE_MANIFEST.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
# In the non-FORMAL simulation: one accepted single-beat packet while output is stalled.
tb='''`timescale 1ns/1ps
module tb;
reg clk=0; always #5 clk=~clk;
reg rst_n=0,s_axis_tvalid=0,s_axis_tlast=0,m_axis_tready=0;
reg [7:0] s_axis_tdata=8'h3c;
wire [3:0] sready,mvalid,mlast;
wire [7:0] mdata[0:3];wire [3:0] count[0:3];
'''
for i,(o,b) in enumerate(((0,0),(0,1),(1,0),(1,1))):
 tb+=f'''axis_pkt_fifo_{o}{b} #(.DATA_WIDTH(8),.DEPTH(8)) dut{i} (
.clk(clk),.rst_n(rst_n),.s_axis_tvalid(s_axis_tvalid),.s_axis_tready(sready[{i}]),
.s_axis_tdata(s_axis_tdata),.s_axis_tlast(s_axis_tlast),
.m_axis_tvalid(mvalid[{i}]),.m_axis_tready(m_axis_tready),
.m_axis_tdata(mdata[{i}]),.m_axis_tlast(mlast[{i}]),.pkt_count(count[{i}]));
'''
tb+='''integer n;
initial begin
  #1;
  if (sready !== 4'b1111) $fatal(1,"reset readiness");
  #5; // reset sampled at first posedge
  if (count[0] !== 0 || count[1] !== 0 || count[2] !== 0 || count[3] !== 0) $fatal(1,"reset count");
  rst_n=1;s_axis_tvalid=1;s_axis_tlast=1;
  #10; // first packet accepted at posedge t15
  if (count[0] !== 4'd1 || count[1] !== 4'd2 || count[2] !== 4'd1 || count[3] !== 4'd2) $fatal(1,"2x2 count mismatch %d %d %d %d",count[0],count[1],count[2],count[3]);
  if (sready !== 4'b1111 || mvalid !== 4'b0000) $fatal(1,"other port mismatch");
  $display("G4_INITIALIZED_WITNESS_PASS O0B0=1 O0B1=2 O1B0=1 O1B1=2 (4-state case equality)");
  s_axis_tvalid=0;
  #10;
  for(n=0;n<4;n=n+1) if(sready[n] !== sready[0] || mvalid[n] !== mvalid[0]) $fatal(1,"other output divergence");
  $display("G4_OTHER_PORTS_MATCH_PASS");$finish;
end
endmodule
'''
(out/'tb_factorial.sv').write_text(tb)
print('G4_SOURCE_PRECOMMITTED',json.dumps(manifest['cells'],sort_keys=True),flush=True)
