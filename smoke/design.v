module top(
    input wire a,
    input wire b,
    input wire c
);
`ifdef FORMAL
    always @(*) assume(a);
    always @(*) assert(b);
    always @(*) cover(c);
`endif
endmodule
