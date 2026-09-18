`timescale 1ns/1ps
module tb;
  reg clk = 0;
  always #5 clk = ~clk;
  reg rst_n = 0;
  reg s_axis_tvalid = 0;
  reg [7:0] s_axis_tdata = 0;
  reg s_axis_tlast = 0;
  reg m_axis_tready = 1;
  wire old_sr, new_sr, old_mv, new_mv, old_ml, new_ml;
  wire [7:0] old_md,new_md;
  wire [3:0] old_pc,new_pc;
  integer fd;
  integer step = 0;
  integer i;
  axis_pkt_fifo #(.DATA_WIDTH(8),.DEPTH(8)) a (
    .clk(clk),.rst_n(rst_n),.s_axis_tvalid(s_axis_tvalid),
    .s_axis_tready(old_sr),.s_axis_tdata(s_axis_tdata),.s_axis_tlast(s_axis_tlast),
    .m_axis_tvalid(old_mv),.m_axis_tready(m_axis_tready),
    .m_axis_tdata(old_md),.m_axis_tlast(old_ml),.pkt_count(old_pc));
  axis_pkt_fifo_new #(.DATA_WIDTH(8),.DEPTH(8)) b (
    .clk(clk),.rst_n(rst_n),.s_axis_tvalid(s_axis_tvalid),
    .s_axis_tready(new_sr),.s_axis_tdata(s_axis_tdata),.s_axis_tlast(s_axis_tlast),
    .m_axis_tvalid(new_mv),.m_axis_tready(m_axis_tready),
    .m_axis_tdata(new_md),.m_axis_tlast(new_ml),.pkt_count(new_pc));
  task check;
    begin
      if ({old_sr,old_mv,old_md,old_ml,old_pc} !==
          {new_sr,new_mv,new_md,new_ml,new_pc}) begin
        $display("MISMATCH step=%0d old=%b new=%b",step,
          {old_sr,old_mv,old_md,old_ml,old_pc},
          {new_sr,new_mv,new_md,new_ml,new_pc});
        $fatal(1,"NATIVE_RTL_OUTPUT_DIVERGENCE");
      end
      if ({a.wptr,a.rptr,a.commit_ptr} !== {b.wptr,b.rptr,b.commit_ptr})
        $fatal(1,"NATIVE_RTL_POINTER_DIVERGENCE");
      $fdisplay(fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",step,
        a.wptr,a.rptr,a.commit_ptr,old_sr,old_mv,old_pc,
        ((b.wptr-b.commit_ptr)<8),rst_n);
      step=step+1;
    end
  endtask
  initial begin
    fd=$fopen("evidence/native_trace.csv","w");
    if (fd==0) $fatal(1,"TRACE_OPEN_FAILED");
    $fdisplay(fd,"step,wptr,rptr,commit_ptr,s_ready,m_valid,pkt_count,contract_fits,rst_n");
    repeat(2) @(posedge clk);
    #1; check();
    @(negedge clk);rst_n=1;
    for (i=0;i<8;i=i+1) begin
      @(negedge clk);
      s_axis_tvalid=1;
      s_axis_tlast=0;
      s_axis_tdata=i;
      #1;
      if (old_sr!==1'b1 || new_sr!==1'b1)
        $fatal(1,"PREMATURE_FULL %0d",i);
      @(posedge clk);
      #1;check();
    end
    if (a.wptr!==4'd8 || a.rptr!==4'd0 || a.commit_ptr!==4'd0 ||
        old_sr!==1'b0 || old_mv!==1'b0 || old_pc!==4'd0)
      $fatal(1,"EXPECTED_REACHABLE_OVERSIZE_STATE_NOT_FOUND");
    if (((b.wptr-b.commit_ptr)<8) !== 1'b0)
      $fatal(1,"NEW_CONTRACT_NOT_VIOLATED");
    $display("REACHABLE_ENVIRONMENT_LOSS PASS: eight accepted non-TLAST beats, wptr=8, commit=0");
    @(negedge clk);
    s_axis_tlast=1;
    #1;
    if (old_sr!==1'b0 || new_sr!==1'b0) $fatal(1,"EXTRA_TLAST_ACCEPTED");
    repeat(4) begin @(posedge clk); #1;check(); end
    if (old_mv!==1'b0 || new_mv!==1'b0)
      $fatal(1,"UNEXPECTED_OUTPUT");
    $display("NATIVE_RTL_OLD_NEW_OUTPUT_AND_POINTER_REPLAY PASS");
    $fclose(fd);
    $finish;
  end
endmodule
