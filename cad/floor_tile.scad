// Floor tile with dovetail tabs built in. Export with -D col=0..2 -D row=0..1 (build.cmd does all six).
// Back-row tiles (row=1) include the cable-lane rib; tile col=0,row=0 includes the switch holder.
include <parts.scad>
col = 0;
row = 0;
floor_tile(col, row);
