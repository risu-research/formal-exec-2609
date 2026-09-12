module masked_eqcount(
    input wire clk,
    input wire [2:0] opcode
);
    reg bad;
    initial bad = 1'b0;

    // One nontrivial environment assumption in both revisions. R0 remains safe
    // throughout its legal domain; opcode 7 is irrelevant to the later bug.
    always @(posedge clk) begin
        assume(opcode != 3'b111);
        bad <= 1'b0;
        assert(!bad);
    end
endmodule
