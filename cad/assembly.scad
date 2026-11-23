// Top-view assembly preview: every printed part placed in the case.
// Open in OpenSCAD and press F5, or build.cmd renders assembly_top.png.
// Grey see-through boxes = the real hardware (PC, Pi, brick, strip, hub, switch).
include <parts.scad>

show_hardware = true;

module hw(pos, size, z) { if (show_hardware) color([0.6, 0.6, 0.65, 0.45]) translate([pos[0], pos[1], z]) cube(size); }

// case interior outline at the rim
color([0.15, 0.15, 0.17]) translate([-(case_in[0] - tray[0]) / 2, -(case_in[1] - tray[1]) / 2, -2])
    linear_extrude(1) difference() { offset(r = 8) rrect(case_in, tray_r); rrect(case_in, tray_r); }

// floor tiles (with dovetail tabs, lane rib and switch holder built in)
for (c = [0 : tile_cols - 1]) for (r = [0 : tile_rows - 1])
    color(((c + r) % 2 == 0) ? [0.23, 0.23, 0.26] : [0.30, 0.30, 0.34]) floor_tile_placed(c, r);

z = tile_t;
color([0.11, 0.11, 0.13]) {
    translate([plinth_box[0], plinth_box[1], z]) k12_plinth();
    translate([hub_box[0], hub_box[1], z]) hub_pi_cradle();
    translate([power_box[0], power_box[1], z]) power_cradle();
}
color([0.3, 0.79, 0.94]) for (x = [40, 150, 260, 380])
    translate([x, rib_y, z]) rotate([90, 0, 90]) cable_comb();

// side panel on the left wall (outer plate outside, inner plate inside)
color([0.3, 0.79, 0.94]) translate([-2, panel_y_center - panel[0] / 2, z + 20]) rotate([90, 0, 90]) {
    translate([0, 0, -panel_outer_t - 4]) side_panel_outer();
    side_panel_inner();
}

// hardware
hw(k12_pos, k12, z + plinth_h);
hw([hub_box[0] + (bw(hub_box) - hub[0]) / 2, hub_box[1] + (bd(hub_box) - hub[1]) / 2], hub, z + hub_floor);
hw([pi_box[0] + 10.5, pi_box[1] + (bd(pi_box) - pi_case[1]) / 2], pi_case, z + pi_floor);
hw([power_box[0] + 10, power_box[1] + power_wall_t + 0.5], brick, z + power_floor + brick_rib_h);
hw([power_box[0] + 20, power_box[1] + 2 * power_wall_t + brick[1] + 1.5], strip, z + power_floor);
hw([switch_box[0] + 2.5, switch_box[1] + 2.5], switch_sz, z);

// keep-clear zone behind the PC (hot exhaust; [CHECK] UM870 vents)
color([1, 0.3, 0.3, 0.25]) translate([rear_air[0], rear_air[1], z]) cube([bw(rear_air), bd(rear_air), 1]);
