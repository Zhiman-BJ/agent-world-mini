`timescale 1ns/1ps
module verification_dut (
    input wire clk, input wire rst,
    input wire [7:0] a, input wire [7:0] b,
    input wire drop_b, input wire truncate_carry,
    output wire [8:0] y,
    input wire [7:0] s_axis_tdata, input wire s_axis_tvalid,
    output wire s_axis_tready, input wire s_axis_tlast,
    output wire [7:0] m_axis_tdata, output wire m_axis_tvalid,
    input wire m_axis_tready, output wire m_axis_tlast,
    input wire corrupt_data, input wire block_ready
);
wire [8:0] sum = {1'b0,a}+{1'b0,(drop_b ? 8'b0 : b)};
assign y = truncate_carry ? {1'b0,sum[7:0]} : sum;
assign s_axis_tready = !rst && !block_ready && m_axis_tready;
assign m_axis_tvalid = !rst && !block_ready && s_axis_tvalid;
assign m_axis_tdata = s_axis_tdata ^ (corrupt_data ? 8'h01 : 8'h00);
assign m_axis_tlast = s_axis_tlast;
endmodule
