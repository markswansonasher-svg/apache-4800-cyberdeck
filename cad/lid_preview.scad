// Lid preview: monitor + 4 clips inside the lid, seen from where you sit.
// Bottom of the picture = hinge side. Grey = monitor, blue = clips.
include <parts.scad>

ox = (case_in[0] - monitor[0]) / 2;
oy = (case_in[1] - monitor[1]) / 2;

color([0.15, 0.15, 0.17]) linear_extrude(1) difference() { offset(r = 8) rrect(case_in, tray_r); rrect(case_in, tray_r); }
color([0.35, 0.35, 0.4]) translate([0, 0, -1]) linear_extrude(1) rrect(case_in, tray_r);
// monitor (wedge: thick edge at the bottom by default)
color([0.55, 0.55, 0.6, 0.85]) translate([ox, oy, 0])
    hull() {
        cube([monitor[0], 1, thick_edge == "bottom" ? mon_thick : mon_thin]);
        translate([0, monitor[1] - 1, 0]) cube([monitor[0], 1, thick_edge == "bottom" ? mon_thin : mon_thick]);
    }
// port zone on the right edge (keep clear)
color([1, 0.35, 0.3]) translate([ox + monitor[0] - 1, oy + clip_port_leg + 8, 0]) cube([3, monitor[1] - 2 * (clip_port_leg + 8), 12]);
color([0.16, 0.62, 0.85]) {
    translate([ox, oy, 0]) lid_clip_placed("BL");
    translate([ox + monitor[0], oy, 0]) lid_clip_placed("BR");
    translate([ox, oy + monitor[1], 0]) lid_clip_placed("TL");
    translate([ox + monitor[0], oy + monitor[1], 0]) lid_clip_placed("TR");
}
