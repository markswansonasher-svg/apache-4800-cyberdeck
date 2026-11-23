// ===========================================================================
//  Apache 4800 cyberdeck v2 - part modules
//  Each module builds one part at its own origin, in print orientation unless
//  noted. The small *.scad files next to this one call these to export STLs.
// ===========================================================================
include <params.scad>

// ---------------------------------------------------------------- helpers --
function bw(b) = b[2] - b[0];
function bd(b) = b[3] - b[1];

// 2D rounded rectangle with its corner at the origin
module rrect(s, r = 0) {
    rr = min(r, s[0] / 2 - 0.01, s[1] / 2 - 0.01);
    if (rr <= 0) square(s);
    else translate([rr, rr]) offset(r = rr) square([s[0] - 2 * rr, s[1] - 2 * rr]);
}

// Extrude a 2D shape with a small step at the bottom edge (stops elephant foot)
module cext(h, c = chamfer) {
    linear_extrude(c) offset(delta = -c) children();
    translate([0, 0, c]) linear_extrude(h - c) children();
}

// Velcro strap path running along Y at x: two slots through a floor plus a
// shallow groove underneath so the strap lies flat against the tile.
module strap_y(x, y0, y1, floor_t) {
    for (y = [y0, y1])
        translate([x - strap_w / 2, y - strap_slot_t / 2, -1]) cube([strap_w, strap_slot_t, floor_t + 2]);
    hull() {
        translate([x - strap_w / 2 - strap_groove, y0 - strap_slot_t / 2, -1])
            cube([strap_w + 2 * strap_groove, y1 - y0 + strap_slot_t, 1]);
        translate([x - strap_w / 2, y0 - strap_slot_t / 2, strap_groove - 0.01])
            cube([strap_w, y1 - y0 + strap_slot_t, 0.01]);
    }
}
// Same, running along X at y (swap X and Y)
module strap_x(y, x0, x1, floor_t) {
    multmatrix([[0, 1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]) strap_y(y, x0, x1, floor_t);
}

// ------------------------------------------------------------ floor tiles --
function tile_rect(c, r) = let(g = tile_gap / 2)
    [c * tile_sz[0] + (c > 0 ? g : 0),
     r * tile_sz[1] + (r > 0 ? g : 0),
     (c + 1) * tile_sz[0] - (c < tile_cols - 1 ? g : 0),
     (r + 1) * tile_sz[1] - (r < tile_rows - 1 ? g : 0)];

// [x, y, angle] of every tile joint, in tray coordinates. Angle 0 = joint on a
// vertical seam (tab points +X), 90 = horizontal seam (tab points +Y).
joints = concat(
    [for (k = [1 : tile_cols - 1]) for (r = [0 : tile_rows - 1]) for (f = [0.25, 0.75])
        [k * tile_sz[0], (r + f) * tile_sz[1], 0, k - 1, r, k, r]],
    [for (r = [1 : tile_rows - 1]) for (c = [0 : tile_cols - 1]) for (f = [0.25, 0.75])
        [(c + f) * tile_sz[0], r * tile_sz[1], 90, c, r - 1, c, r]]);
// each entry: [x, y, angle, owner col, owner row, socket col, socket row]

// Dovetail tab, starting 1 mm inside the owner tile and reaching into the neighbour
module tab2d(d = 0) {
    offset(delta = d) polygon([[-1, -tab_neck / 2], [0, -tab_neck / 2], [tab_len, -tab_head / 2],
                               [tab_len, tab_head / 2], [0, tab_neck / 2], [-1, tab_neck / 2]]);
}

// Areas with no vent holes: under every cradle (glue area) and the cable lanes
vent_keepouts = [hub_box, pi_box, power_box, switch_box, plinth_box,
                 [0, signal_lane[1] - 4, tray[0], tray[1]]];

function box_hit(p, b, m) = let(h = vent_sz / 2)
    p[0] + h > b[0] - m && p[0] - h < b[2] + m && p[1] + h > b[1] - m && p[1] - h < b[3] + m;

function vent_ok(p) = let(
        c = floor(p[0] / tile_sz[0]), r = floor(p[1] / tile_sz[1]),
        lx = p[0] - c * tile_sz[0], ly = p[1] - r * tile_sz[1], h = vent_sz / 2)
    lx - h >= vent_margin && lx + h <= tile_sz[0] - vent_margin &&
    ly - h >= vent_margin && ly + h <= tile_sz[1] - vent_margin &&
    len([for (b = vent_keepouts) if (box_hit(p, b, 3)) 1]) == 0 &&
    len([for (k = joints) if (abs(p[0] - k[0]) < tab_head / 2 + tab_len + h + 4 &&
                              abs(p[1] - k[1]) < tab_head / 2 + tab_len + h + 4) 1]) == 0;

vents = [for (i = [0 : floor(tray[0] / vent_pitch)]) for (j = [0 : floor(tray[1] / vent_pitch)])
            let(p = [vent_pitch / 2 + i * vent_pitch, vent_pitch / 2 + j * vent_pitch]) if (vent_ok(p)) p];

// Tile in tray coordinates (used by the assembly).
// Includes its dovetail tabs, the lane rib (back row) and the switch holder (front-left).
module floor_tile_placed(c, r) {
    t = tile_rect(c, r);
    has_switch = switch_box[0] >= t[0] && switch_box[2] <= t[2] && switch_box[1] >= t[1] && switch_box[3] <= t[3];
    difference() {
        union() {
            cext(tile_t) difference() {
                union() {
                    intersection() {
                        rrect(tray, tray_r);
                        translate([t[0], t[1]]) square([t[2] - t[0], t[3] - t[1]]);
                    }
                    for (k = joints) if (k[3] == c && k[4] == r) translate([k[0], k[1]]) rotate(k[2]) tab2d();
                }
                for (k = joints) if (k[5] == c && k[6] == r) translate([k[0], k[1]]) rotate(k[2]) tab2d(tab_clear);
                for (p = vents) translate([p[0] - vent_sz / 2, p[1] - vent_sz / 2]) rrect([vent_sz, vent_sz], 2);
            }
            // cable-lane rib printed onto the back-row tiles
            if (r == tile_rows - 1)
                intersection() {
                    translate([t[0] + 1, rib_y - rib_t / 2, tile_t - 0.01]) cube([t[2] - t[0] - 2, rib_t, rib_h + 0.01]);
                    linear_extrude(tile_t + rib_h + 1) offset(delta = -2) rrect(tray, tray_r);   // stay inside the rounded corners
                }
            if (has_switch) translate([switch_box[0], switch_box[1], 0]) switch_holder_body(tile_t);
        }
        if (has_switch) translate([switch_box[0], switch_box[1], 0]) switch_strap_cuts(tile_t);
    }
}
// Tile moved to the origin for printing
module floor_tile(c, r) {
    t = tile_rect(c, r);
    translate([-t[0], -t[1], 0]) floor_tile_placed(c, r);
}

// ---------------------------------------------------------- PC plinth --
module honeycomb2d(size) {
    p = hex_cell + hex_wall;
    dy = p * sqrt(3) / 2;
    for (j = [-1 : ceil(size[1] / dy) + 1]) for (i = [-1 : ceil(size[0] / p) + 1])
        translate([i * p + (j % 2) * p / 2, j * dy]) rotate(30) circle(d = hex_cell / cos(30), $fn = 6);
}

module k12_plinth() {
    W = bw(plinth_box); D = bd(plinth_box); m = plinth_margin;
    tun_w = 10; tun_h = 10;                     // air tunnels: flat sides + 60 deg pointed roof
    strap_ys = [m + k12[1] * 0.25, m + k12[1] * 0.75];
    air_ys = [for (y = [m + 4 : 15 : D - m - 4])
                 if (min([for (s = strap_ys) abs(y - s)]) > strap_w / 2 + tun_w / 2 + 2) y];
    t = plinth_post_t; L = plinth_post_leg; c = k12_clear;
    difference() {
        union() {
            cext(plinth_h) difference() {
                rrect([W, D], 4);
                intersection() {
                    translate([plinth_rim, plinth_rim]) square([W - 2 * plinth_rim, D - 2 * plinth_rim]);
                    translate([0.37, 0.23]) honeycomb2d([W, D]);
                }
            }
            // L-shaped corner guides. Front ones taller; rear ones <= 10 mm (exhaust).
            for (ix = [0, 1]) for (iy = [0, 1]) {
                h = iy == 0 ? plinth_post_front_h : plinth_post_rear_h;
                x = ix == 0 ? m - c - t : m + k12[0] + c;
                y = iy == 0 ? m - c - t : m + k12[1] + c;
                translate([0, 0, plinth_h - 0.5]) {
                    translate([ix == 0 ? x : x + t - L, y, 0]) cube([L, t, h + 0.5]);
                    translate([x, iy == 0 ? y : y + t - L, 0]) cube([t, L, h + 0.5]);
                }
            }
        }
        // side windows: tunnels along X so air reaches the bottom intake fan
        for (y = air_ys) translate([-1, y, 0]) rotate([90, 0, 90]) linear_extrude(W + 2)
            polygon([[-tun_w / 2, -1], [tun_w / 2, -1], [tun_w / 2, tun_h - tun_w / 2 * tan(60)], [0, tun_h],
                     [-tun_w / 2, tun_h - tun_w / 2 * tan(60)]]);
        // two strap tunnels along X (strap goes under the plinth and over the PC)
        for (y = strap_ys) translate([-1, y - strap_w / 2, -1]) cube([W + 2, strap_w, 4]);
    }
}

// ---------------------------------------------------------- USB hub holder --
module hub_holder() {
    W = bw(hub_box); D = bd(hub_box);
    pk = [hub[0] + hub_fit, hub[1] + hub_fit];
    ox = (W - pk[0]) / 2; oy = (D - pk[1]) / 2;
    H = hub_floor + hub_wall_h;
    nx = hub_cable_end == "right" ? W - ox - 1 : -1;
    difference() {
        cext(H) rrect([W, D], 3);
        translate([ox, oy, hub_floor]) cube([pk[0], pk[1], H]);
        // low front wall so front-facing ports are reachable
        translate([ox, -1, hub_floor + hub_front_wall_h]) cube([pk[0], oy + 2, H]);
        // cable exit notch
        translate([nx, D / 2 - 6, hub_floor + 3]) cube([ox + 2, 12, H]);
        for (x = [W * 0.25, W * 0.75]) strap_y(x, oy + strap_slot_t / 2 + 0.5, D - oy - strap_slot_t / 2 - 0.5, hub_floor);
    }
}

// ---------------------------------------------------------- Pi bay cradle --
module pi_cradle() {
    W = bw(pi_box); D = bd(pi_box);
    pk = [pi_case[0] + pi_fit, pi_case[1] + pi_fit];
    px = 10; py = (D - pk[1]) / 2;
    t = 3; L = 10;
    cx0 = px + pk[0] + pi_vent_gap;     // cable cubby starts here
    difference() {
        union() {
            cext(pi_floor) square([W, D]);   // square corners: walls never overhang the edge
            // short corner guides: sides stay open for vents and plugs
            for (ix = [0, 1]) for (iy = [0, 1]) {
                x = ix == 0 ? px - t : px + pk[0];
                y = iy == 0 ? py - t : py + pk[1];
                translate([0, 0, pi_floor]) {
                    translate([ix == 0 ? x : x + t - L, y, 0]) cube([L, t, pi_guide_h]);
                    translate([x, iy == 0 ? y : y + t - L, 0]) cube([t, L, pi_guide_h]);
                }
            }
            // cable cubby: front, back and right walls, open toward the Pi
            translate([0, 0, pi_floor]) {
                translate([cx0, 0, 0]) cube([W - cx0, t, cubby_wall_h]);
                translate([cx0, D - t, 0]) cube([W - cx0, t, cubby_wall_h]);
                translate([W - t, 0, 0]) cube([t, D, cubby_wall_h]);
            }
        }
        // two strap positions along X - use whichever misses your case's fan
        for (y = [py + 3 + strap_w / 2, D - py - 3 - strap_w / 2])
            strap_x(y, px - t - strap_slot_t / 2 - 1.2, px + pk[0] + t + strap_slot_t / 2 + 1.2, pi_floor);
    }
}

// ---------------------------------------------------------- power bay cradle --
module power_cradle() {
    W = bw(power_box); D = bd(power_box);
    t = power_wall_t; f = power_floor;
    bi = brick[1] + 1; si = strip[1] + 1;
    yb0 = t; yb1 = t + bi; ys0 = yb1 + t; ys1 = ys0 + si;
    assert(ys1 + t <= D, "brick + strip don't fit in power_box depth");
    difference() {
        union() {
            cext(f) square([W, D]);
            // U-channels run along X, open at both ends for air and cables
            for (y = [0, yb1, ys1]) translate([0, y, f]) cube([W, t, power_wall_h]);
            // ribs: air gap under the brick
            for (i = [1 : 4]) translate([0, yb0 + i * bi / 5 - 1, f]) cube([W, 2, brick_rib_h]);
        }
        // strip cord notch in the rear wall, toward the side panel
        translate([W * 0.15 - 7, ys1 - 1, f + 5]) cube([14, t + 2, power_wall_h]);
        for (x = [W * 0.25, W * 0.75]) {
            strap_y(x, yb0 + 2.5, yb1 - 2.5, f);
            strap_y(x, ys0 + 2.5, ys1 - 2.5, f);
        }
    }
}

// ---------------------------------------------------------- switch holder --
// Printed as part of floor tile c0_r0 (the tile is its floor).
module switch_holder_body(fl = switch_floor) {
    W = bw(switch_box); D = bd(switch_box);
    pk = [switch_sz[0] + switch_fit, switch_sz[1] + switch_fit];
    ox = (W - pk[0]) / 2; oy = (D - pk[1]) / 2;
    H = fl + switch_wall_h;
    difference() {
        cext(H) rrect([W, D], 2);
        translate([ox, oy, fl]) cube([pk[0], pk[1], H]);
        translate([ox, D - oy - 1, fl]) cube([pk[0], oy + 2, H]);   // ports face back: open
    }
}
module switch_strap_cuts(fl = switch_floor) {
    W = bw(switch_box); D = bd(switch_box);
    ox = (W - switch_sz[0] - switch_fit) / 2;
    for (y = [D * 0.3, D * 0.7]) strap_x(y, ox + 2.5, W - ox - 2.5, fl);
}
module switch_holder() { difference() { switch_holder_body(); switch_strap_cuts(); } }

// ---------------------------------------------------------- cable lanes --
module lane_rib(len = tile_sz[0] - tile_gap) {
    cext(rib_foot_t) square([len, rib_foot_w]);
    translate([0, (rib_foot_w - rib_t) / 2, 0]) cube([len, rib_t, rib_h]);
}

// Cable comb: caps the lane rib and holds cables down in both lanes.
// 2D profile: u = offset across the lanes from the rib centre, v = height.
module comb2d() {
    a = 12.5; s = (rib_t + 0.4) / 2; z0 = rib_h - 6; lip = rib_h - 4;
    difference() {
        union() {
            translate([-s - 2, z0]) square([2 * s + 4, rib_h + 2.5 - z0]);
            translate([-a, rib_h]) square([2 * a, 2.5]);
            translate([-a, lip]) square([2, rib_h - lip + 0.01]);
            translate([a - 2, lip]) square([2, rib_h - lip + 0.01]);
        }
        translate([-s, z0 - 1]) square([2 * s, 6 + 1]);
    }
}
module cable_comb() { linear_extrude(comb_w) comb2d(); }     // prints on its side

// ---------------------------------------------------------- side panel --
function ks_x(i) = (panel[0] - wall_cutout[0]) / 2 + 2 + keystone_relief[0] / 2 + i * keystone_pitch;
pwr_x = ks_x(4) + keystone_relief[0] / 2 + 2 + iec_relief[0] / 2;
panel_cy = panel[1] / 2;
bolt_pts = [for (x = [10, panel[0] / 2, panel[0] - 10]) for (y = [4.5, panel[1] - 4.5]) [x, y]];

// Laid out as seen from OUTSIDE the case (x to your right)
module panel_outer_view() {
    difference() {
        cext(panel_outer_t) rrect(panel, 4);
        for (i = [0 : 4]) translate([ks_x(i), panel_cy, 0]) {
            translate([0, 0, -1]) linear_extrude(panel_outer_t + 2) square(keystone_hole, center = true);
            translate([0, 0, keystone_latch_t]) linear_extrude(panel_outer_t) square(keystone_relief, center = true);
        }
        translate([pwr_x, panel_cy, 0]) {
            if (power_entry == "iec") {
                translate([0, 0, -1]) linear_extrude(panel_outer_t + 2) square(iec_hole, center = true);
                translate([0, 0, iec_latch_t]) linear_extrude(panel_outer_t) square(iec_relief, center = true);
            } else {
                translate([0, 0, -1]) linear_extrude(panel_outer_t + 2)
                    hull() for (s = [-1, 1]) translate([s * (cord_slot[0] - cord_slot[1]) / 2, 0]) circle(d = cord_slot[1]);
            }
        }
        for (p = bolt_pts) translate([p[0], p[1], -1]) cylinder(d = m4_hole, h = panel_outer_t + 2);
        // engraved labels on the outside face
        for (i = [0 : 5]) translate([i < 5 ? ks_x(i) : pwr_x, panel_cy + 12.5, -0.01])
            linear_extrude(0.6) text(labels[i], size = 3.6, halign = "center", valign = "center",
                                     font = "Liberation Sans:style=Bold");
    }
}
// Print orientation: outside face down on the bed
module side_panel_outer() { translate([panel[0], 0, 0]) mirror([1, 0, 0]) panel_outer_view(); }

module side_panel_inner() {
    difference() {
        cext(panel_inner_t) rrect(panel, 4);
        translate([(panel[0] - wall_cutout[0]) / 2, (panel[1] - wall_cutout[1]) / 2, -1])
            linear_extrude(panel_inner_t + 2) rrect(wall_cutout, 2);
        for (p = bolt_pts) translate([p[0], p[1], -1]) cylinder(d = m4_hole, h = panel_inner_t + 2);
    }
}

// Snap-on travel cover (friction ribs). Prints top-down.
module travel_cover() {
    ic = [panel[0] + 0.8, panel[1] + 0.8]; w = 2; top = 2;
    H = top + cover_clear + panel_outer_t;
    difference() {
        cext(H) translate([-w, -w]) rrect([ic[0] + 2 * w, ic[1] + 2 * w], 4.4 + w);
        translate([0, 0, top]) linear_extrude(H) rrect(ic, 4.4);
        translate([ic[0] / 2 - 12, -w - 1, H - 3]) cube([24, w + 2, 4]);   // finger notch
    }
    // friction ribs on the long walls, only where the plate sits
    for (x = [25, ic[0] / 2, ic[0] - 25]) for (y = [0, ic[1]])
        hull() {   // tapered bottom end, no flat ledge
            translate([x - 0.6, y == 0 ? -0.01 : ic[1] - 0.35, top + cover_clear]) cube([1.2, 0.36, panel_outer_t]);
            translate([x - 0.6, y == 0 ? -0.01 : ic[1], top + cover_clear - 0.6]) cube([1.2, 0.01, 0.6]);
        }
}

// One keystone opening + one power-entry opening, to check fit first
module keystone_test() {
    difference() {
        cext(panel_outer_t) rrect([76, 40], 3);
        translate([18, 20, 0]) {
            translate([0, 0, -1]) linear_extrude(panel_outer_t + 2) square(keystone_hole, center = true);
            translate([0, 0, keystone_latch_t]) linear_extrude(panel_outer_t) square(keystone_relief, center = true);
        }
        translate([54, 20, 0]) {
            translate([0, 0, -1]) linear_extrude(panel_outer_t + 2) square(iec_hole, center = true);
            translate([0, 0, iec_latch_t]) linear_extrude(panel_outer_t) square(iec_relief, center = true);
        }
    }
}

// ---------------------------------------------------------- lid monitor clips --
// Built for the monitor's BOTTOM-LEFT corner at the origin (monitor at x>0, y>0),
// standing on the lid. lid_clip() mirrors it for the other corners and flips it
// upside-down for printing (lip on the bed, 45 deg buttress = no supports).
function clip_h(thick) = foam_t + (thick ? mon_thick : mon_thin) + foam_t + clip_lip_t;

module clip_body(thick, side_leg) {
    zl = clip_h(thick) - clip_lip_t;
    H = clip_h(thick);
    g = foam_t; t = clip_wall_t; b = clip_buttress;
    o = -(g + t);
    lx = clip_leg; ly = side_leg;
    // along the bottom edge (X)
    translate([o, o, 0]) cube([lx - o, t, H]);
    translate([o, o, zl]) cube([lx - o, t + g + clip_lip, clip_lip_t]);
    hull() {
        translate([o - b, o - 0.01, 0]) cube([lx - o + b, 0.01, H]);
        translate([o - b, o - b, 0]) cube([lx - o + b, 0.01, max(H - b, 2)]);
    }
    // along the side edge (Y)
    translate([o, o, 0]) cube([t, ly - o, H]);
    translate([o, o, zl]) cube([t + g + clip_lip, ly - o, clip_lip_t]);
    hull() {
        translate([o - 0.01, o - b, 0]) cube([0.01, ly - o + b, H]);
        translate([o - b, o - b, 0]) cube([0.01, ly - o + b, max(H - b, 2)]);
    }
}

// corner: "BL", "BR", "TL", "TR" as you look at the open lid (B = hinge side)
module lid_clip_placed(corner) {
    bottom = corner[0] == "B";
    right = corner[1] == "R";
    thick = (thick_edge == "bottom") == bottom;
    side_leg = right ? clip_port_leg : clip_leg;
    mirror([right ? 1 : 0, 0, 0]) mirror([0, bottom ? 0 : 1, 0]) clip_body(thick, side_leg);
}
module lid_clip(corner) {
    bottom = corner[0] == "B";
    thick = (thick_edge == "bottom") == bottom;
    translate([0, 0, clip_h(thick)]) rotate([180, 0, 0]) lid_clip_placed(corner);
}

// =================================================== combined print plates ==
// Hub holder + Pi cradle as ONE part (they sit side by side in the middle column)
module hub_pi_cradle() {
    gap = pi_box[1] - hub_box[3];
    hub_holder();
    translate([0, bd(hub_box) - 1, 0]) cube([bw(hub_box), gap + 2, pi_floor]);   // joins the two floors
    translate([0, pi_box[1] - hub_box[1], 0]) pi_cradle();
}

// Both side-panel plates on one bed
module side_panels_plate() {
    side_panel_outer();
    translate([0, panel[1] + 6, 0]) side_panel_inner();
}

// Travel cover + 6 cable combs on one bed
module cover_combs_plate() {
    w = 2;
    translate([w, w, 0]) travel_cover();
    for (i = [0 : 5]) translate([(i % 3) * 32 + 14, panel[1] + 0.8 + 2 * w + 8 + floor(i / 3) * 22 + 4.5, 0]) cable_comb();
}

// All four lid clips on one bed (each clip's corner placed at a grid cell)
module lid_clips_plate() {
    o = clip_buttress + foam_t + clip_wall_t;
    p = o + clip_leg + 5;                      // cell pitch = clip size + 5 mm
    translate([o, clip_leg, 0])               lid_clip("BL");
    translate([o + p, o, 0])                  lid_clip("TL");
    translate([clip_leg, p + clip_port_leg, 0]) lid_clip("BR");
    translate([clip_leg + p, p + o, 0])         lid_clip("TR");
}
