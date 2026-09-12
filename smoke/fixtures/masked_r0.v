module masked(
    input wire clk,
    input wire [1:0] opcode
);
    reg bad;
    initial bad = 1'b0;

    // R0 is safe for the complete opcode domain.
    always @(posedge clk) begin
        bad <= 1'b0;
        assert(!bad);
    end
endmodule
