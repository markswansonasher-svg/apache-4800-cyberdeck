// 1:1 paper template for cutting the case wall. build.cmd exports it as SVG.
// Print at 100% / "actual size" and check the 50 mm scale bar with a ruler.
// Tape it on the OUTSIDE of the left wall, centred on the flat area.
include <parts.scad>

lw = 0.35;   // line width
module outline(s, r) { difference() { rrect(s, r); offset(delta = -lw) rrect(s, r); } }

// outer plate outline (dashed look not needed - thin line)
outline(panel, 4);
// wall cutout - thicker line: CUT ON THE INSIDE OF THIS LINE
translate([(panel[0] - wall_cutout[0]) / 2, (panel[1] - wall_cutout[1]) / 2])
    difference() { rrect(wall_cutout, 2); offset(delta = -0.8) rrect(wall_cutout, 2); }
// bolt holes: circle + crosshair
for (p = bolt_pts) translate(p) {
    difference() { circle(d = m4_hole); circle(d = m4_hole - 2 * lw); }
    square([6, lw], center = true);
    square([lw, 6], center = true);
}
// corner pilot holes for the cutout (drill these first, 8 mm)
for (sx = [0, 1]) for (sy = [0, 1])
    translate([(panel[0] - wall_cutout[0]) / 2 + 6 + sx * (wall_cutout[0] - 12),
               (panel[1] - wall_cutout[1]) / 2 + 6 + sy * (wall_cutout[1] - 12)])
        difference() { circle(d = 8); circle(d = 8 - 2 * lw); }
translate([panel[0] / 2, panel[1] / 2 + 4]) text("CUT 135 x 42", size = 4, halign = "center");
translate([panel[0] / 2, panel[1] / 2 - 4]) text("M4 holes 4.3 mm", size = 3, halign = "center");
// scale bar
translate([0, -14]) {
    square([50, 0.8]);
    for (x = [0, 10, 20, 30, 40, 50]) translate([x, 0]) square([lw, 3]);
    translate([55, -1]) text("50 mm - check with a ruler", size = 3);
}
