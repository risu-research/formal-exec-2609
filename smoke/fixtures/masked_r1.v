module masked(
    input wire clk,
    input wire [1:0] opcode
);
    reg bad;
    initial bad = 1'b0;

    // R1 contains a real bug at opcode 3. Its native proof remains green only
    // because the new environment excludes that opcode. PROOFSCOPE_OLD_ENV
    // removes only the new assumption for impact replay.
    always @(posedge clk) begin
`ifndef PROOFSCOPE_OLD_ENV
        assume(opcode != 2'b11);
`endif
        if (opcode == 2'b11)
            bad <= 1'b1;
        else
            bad <= 1'b0;
        assert(!bad);
    end
endmodule
