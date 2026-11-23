// ===========================================================================
//  Apache 4800 cyberdeck v2 - shared parameters (all mm)
//  Every part file includes this through parts.scad.
//  Values marked [CHECK] are defaults from the spec: measure the real part and
//  change them here, then run build.cmd again to re-export every STL.
// ===========================================================================

// ---- printer limits --------------------------------------------------------
bed_max    = 175;      // max footprint X and Y
height_max = 150;

// ---- case: Harbor Freight Apache 4800 ---------------------------------------
case_in      = [454, 327];  // interior at the rim (listed)
tray         = [444, 312];  // [CHECK] printed tray outline = floor at the very bottom
tray_r       = 20;          // [CHECK] inside corner radius
base_depth   = 115;         // [CHECK] floor to rim
lid_depth    = 44;          // [CHECK]
height_limit = 100;         // nothing in the base taller than this above the floor

// ---- floor tiles (3 x 2) -----------------------------------------------------
tile_cols = 3;
tile_rows = 2;
tile_t    = 4;
tile_gap  = 0.4;                       // total gap between neighbouring tiles
tile_sz   = [tray[0] / tile_cols, tray[1] / tile_rows];   // 148 x 156
vent_sz     = 15;
vent_pitch  = 25;
vent_margin = 8;                       // keep vents this far from tile edges
chamfer     = 0.5;                     // bottom-edge chamfer (elephant foot)

// ---- tile joints (dovetail tabs printed on the tiles - no loose keys) -------
tab_neck  = 10;      // width where the tab leaves its tile
tab_head  = 16;      // width at the tip (wider = locks)
tab_len   = 10;      // how far it reaches into the next tile
tab_clear = 0.2;     // gap around the tab in the socket (per side); raise if too tight

// ---- Velcro straps ------------------------------------------------------------
strap_w      = 22;   // slot length (20 mm strap + room)
strap_slot_t = 3.5;  // slot width
strap_groove = 1.6;  // groove depth under a floor for the strap (leaves >= 2.4 mm of floor)

// ---- layout (top view; origin = front-left of tray, X right, Y to hinge) ----
// [x0, y0, x1, y1]
hub_box    = [75,   6, 245,  56];
pi_box     = [75,  60, 245, 138];
power_box  = [75, 142, 245, 282];
switch_box = [2.5, 12.5, 72.5, 117.5];
signal_lane = [0, 286, tray[0], 298];
power_lane  = [0, 300, tray[0], 312];

// ---- the PC: Minisforum UM870 Slim (names still say k12 so nothing else breaks) ----
k12      = [130, 126.5, 50.4];    // listed (was GMKtec K12: 154 x 151 x 73.6)
k12_pos  = [266, 40];             // front-left corner on the tray (same spot as the K12)
// [CHECK] open air behind the PC: assumes it blows hot air out the back.
// Once the UM870 is here, check where its vents really are.
rear_air = [k12_pos[0], k12_pos[1] + k12[1], 420, 286];
plinth_margin = 8;
plinth_h      = 15;
plinth_rim    = 8;                // solid border: corner guides sit fully on it
hex_cell      = 10;               // across flats
hex_wall      = 2;
plinth_post_front_h = 20;         // above the honeycomb
plinth_post_rear_h  = 10;         // keep the rear exhaust clear (spec: <= 10)
plinth_post_leg     = 15;
plinth_post_t       = 3;
k12_clear           = 1;
plinth_box = [k12_pos[0] - plinth_margin, k12_pos[1] - plinth_margin,
              k12_pos[0] + k12[0] + plinth_margin, k12_pos[1] + k12[1] + plinth_margin];

// ---- USB hub (Vantisan) -----------------------------------------------------------
hub      = [161, 42, 25];        // measured
hub_fit  = 1;                    // clearance (total)
hub_wall_h       = 16;           // back/side walls above the floor
hub_front_wall_h = 8;            // [CHECK] low front wall: ports face up/front
hub_floor        = 4;
hub_cable_end    = "right";      // cable exit notch end: "right" (toward the PC) or "left"

// ---- Raspberry Pi 5 in CanaKit case ------------------------------------------------
pi_case   = [95, 65, 30];        // measured (height est.)
pi_fit    = 1;
pi_floor  = 4;
pi_guide_h  = 12;
pi_vent_gap = 15;                // open space between Pi and the cable cubby
cubby_wall_h = 15;

// ---- power bay ------------------------------------------------------------------
brick  = [150, 65, 35];          // [CHECK] PC 120 W brick (UM870: 19 V / 6.32 A) - measure it
strip  = [130, 60, 40];          // [CHECK] compact 3-outlet surge strip
power_floor  = 4;
power_wall_h = 30;               // above the floor
power_wall_t = 3;
brick_rib_h  = 4;                // air gap under the brick

// ---- network switch -----------------------------------------------------------------
switch_sz   = [65, 100, 25];     // [CHECK] (X = width, Y = length, ports at back)
switch_fit  = 1;
switch_floor  = 4;
switch_wall_h = 12;

// ---- cable lanes ---------------------------------------------------------------------
rib_h      = 10;
rib_t      = 2.4;
rib_foot_w = 8;
rib_foot_t = 2;
rib_y      = 299;                // centre line between the lanes
comb_w     = 20;                 // comb thickness along the lane

// ---- left-wall side panel -------------------------------------------------------------
panel      = [150, 60];
panel_outer_t = 4;
panel_inner_t = 3;
wall_cutout   = [135, 42];       // [CHECK] must sit inside the flat area of the wall
panel_y_center = 215;            // along the case wall (tray Y)
keystone_hole  = [14.5, 16.0];   // print keystone_test.stl first and adjust
keystone_relief = [17.5, 24];    // back-side pocket so the latch sees a thin plate
keystone_latch_t = 2.0;          // plate thickness where the keystone clips on
keystone_pitch = 19.5;
// Power entry: "iec" = snap-in C14 inlet (recommended - see README) or "slot" = cord slot
power_entry  = "iec";
iec_hole     = [27.5, 19.8];     // [CHECK] snap-in C14 inlet cutout
iec_relief   = [31, 24];
iec_latch_t  = 2.0;
cord_slot    = [25, 12];
m4_hole      = 4.3;
labels = ["LAN", "PC", "MIC", "AUD", "USB", "PWR"];
cover_clear  = 9;                // room in front of the plate for couplers/bolt heads

// ---- lid monitor clips (ViewSonic VA1655) ------------------------------------------------
monitor     = [359, 227];        // listed (spec value used; re-measure the width)
mon_thick   = 20;                // thick edge (measured)
mon_thin    = 10;                // thin edge (measured)
thick_edge  = "bottom";          // [CHECK] "bottom" = hinge side when the lid is open
foam_t      = 1.5;               // foam on every face touching the monitor
clip_wall_t = 3;
clip_lip    = 3;                 // overlap onto the bezel only
clip_lip_t  = 2.5;
clip_buttress = 12;              // VHB foot width outside the wall
clip_leg    = 45;                // leg length along the monitor edge, from the corner
clip_port_leg = 20;              // [CHECK] shorter leg along the RIGHT edge (port zone)

// ---- print settings reference (set these in the slicer) ---------------------------------
// 0.4 nozzle, 0.2 layers, 4 walls, 25-30% gyroid
// holes already include +0.3 mm
$fn = 48;
