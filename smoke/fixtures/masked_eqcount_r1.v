module masked_eqcount(
    input wire clk,
    input wire [2:0] opcode
);
    reg bad;
    initial bad = 1'b0;

    // Exactly one assumption remains, but its semantic domain contracts by also
    // excluding opcode 3. FORMAL_SCOPE_OLD_ENV removes only that extra conjunct.
    always @(posedge clk) begin
`ifdef FORMAL_SCOPE_OLD_ENV
        assume(opcode != 3'b111);
`else
        assume((opcode != 3'b111) && (opcode != 3'b011));
`endif
        if (opcode == 3'b011)
            bad <= 1'b1;
        else
            bad <= 1'b0;
        assert(!bad);
    end
endmodule
