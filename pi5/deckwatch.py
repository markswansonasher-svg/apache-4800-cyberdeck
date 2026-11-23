#!/usr/bin/env python3
"""
deckwatch - the cyberdeck's watchdog. Runs on the Pi as a service, every 30 s:

  * PC temperatures (from LibreHardwareMonitor on the PC) and the Pi's own
  * Minecraft: online?, who's playing, join/leave messages
  * Stops an empty Minecraft server after idle_stop_minutes
    (and shuts the PC down again if the deck woke it for Minecraft)
  * Too hot: warns players, saves and stops the server, then shuts the PC down
    if it stays hot

Writes /var/lib/deck/state.json + history.json; events go to events.jsonl
(the Discord bot and the Deck Panel show them).
"""
import json
import sys
import threading
import time
import urllib.parse
import urllib.request

import decklib as d

PERIOD = 30


class Watch:
    def __init__(self):
        self.cfg = d.load_config()
        self.last_names = None
        self.mc_was_online = None
        self.k12_was_up = None
        self.idle_since = None
        self.level = "ok"            # ok | warn | hot | crit
        self.above_warn_since = None
        self.above_hot_since = None
        self.hot_stopped_at = None
        self.pi_level = "ok"
        self.temps_missing_noted = False
        self.agent_info = {}
        self.agent_info_at = 0
        self.history = d.read_json("history.json", []) or []
        self.last_hist = 0

    # ------------------------------------------------------------ helpers --
    def push_kuma(self, ok, msg):
        url = self.cfg.get("kuma_push_url")
        if not url:
            return
        q = urllib.parse.urlencode({"status": "up" if ok else "down", "msg": msg[:100]})
        try:
            urllib.request.urlopen(url + ("&" if "?" in url else "?") + q, timeout=5).close()
        except Exception:
            pass

    def run_bg_safe(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except d.Busy as e:
            print(e, flush=True)
        except Exception as e:
            d.add_event(f"Watchdog action failed: {e}", "bad", "watch")

    # ------------------------------------------------------------ one tick --
    def tick(self):
        cfg = self.cfg = d.load_config()
        now = time.time()
        k12 = d.k12_up(cfg)
        temps = d.k12_temps(cfg) if k12 else None
        pit = d.pi_temp()
        mc = d.mc_status(cfg["k12_ip"], int(cfg["mc_port"])) if k12 else d.mc_status("0.0.0.0", 1, 0.01)

        # PC agent info (Tailscale IP etc.) every 5 minutes while it's up
        if k12 and now - self.agent_info_at > 300:
            info = d.agent(cfg, "info", timeout_s=20)
            if info.get("ok"):
                self.agent_info = info
                # remembered for /mc invite, which may run while the PC is off
                if info.get("tailscale_ip"):
                    d.write_json("k12_ids.json", {"tailscale_ip": info["tailscale_ip"], "host": info.get("host", "")})
                d.whitelist_apply_pending(cfg)
                # keep the PC in step with travel mode (e.g. it was off when you switched)
                pi_travel = d.travel_on()
                if bool(info.get("travel")) != pi_travel:
                    r = d.agent(cfg, "travel", "on" if pi_travel else "off", timeout_s=40)
                    d.add_event(f"PC switched to travel mode {'ON' if pi_travel else 'OFF'}."
                                if r.get("ok") else f"Couldn't set travel mode on the PC: {r.get('error')}",
                                "info" if r.get("ok") else "warn", "travel")
            self.agent_info_at = now
        if not k12:
            self.agent_info_at = 0

        # --- PC up/down
        if self.k12_was_up is not None and k12 != self.k12_was_up:
            d.add_event("PC is on." if k12 else "PC is off.", "info", "k12")
            if not k12:
                d.remove("woke")
                self.reset_temp_state(quiet=True)   # it's off, not "back to normal"
        self.k12_was_up = k12

        # --- Minecraft online / players
        if self.mc_was_online is not None and mc["online"] != self.mc_was_online:
            if mc["online"]:
                addr = d.join_address(cfg, {"k12": {"tailscale_ip": self.agent_info.get("tailscale_ip")}})
                d.add_event("Minecraft is up" + (f" - join at {addr}" if addr else "") + ".", "good", "mc_up")
            else:
                d.add_event("Minecraft is down.", "warn" if k12 else "info", "mc_down")
                self.last_names = None
        self.mc_was_online = mc["online"]
        if mc["online"]:
            names = set(mc["names"])
            if self.last_names is not None:
                for n in sorted(names - self.last_names):
                    d.add_event(f"{n} joined ({mc['players']}/{mc['max']}).", "info", "join")
                for n in sorted(self.last_names - names):
                    d.add_event(f"{n} left ({mc['players']}/{mc['max']}).", "info", "leave")
            self.last_names = names

        # --- idle stop
        idle_min = int(cfg.get("idle_stop_minutes") or 0)
        if mc["online"] and mc["players"] == 0 and idle_min > 0 and not d.current_action():
            self.idle_since = self.idle_since or now
            if now - self.idle_since >= idle_min * 60:
                woke = d.read_json("woke")
                d.add_event(f"Nobody has played for {idle_min} min - stopping the server"
                            + (" and shutting the PC down (the deck woke it)." if woke else "."), "info", "idle")
                # in its own thread: the backup can take minutes and the watchdog
                # must keep reading temperatures meanwhile
                threading.Thread(target=self.run_bg_safe, args=(d.stop_minecraft, cfg, "idle timer"),
                                 kwargs={"power_off": bool(woke)}, daemon=True).start()
                self.idle_since = None
        else:
            self.idle_since = None

        # --- temperatures
        if k12 and temps is None and not self.temps_missing_noted:
            d.add_event("Can't read PC temperatures - is LibreHardwareMonitor running "
                        "with its web server on? (README)", "warn", "temp")
            self.temps_missing_noted = True
        if temps is not None:
            self.temps_missing_noted = False
            self.check_k12_temp(cfg, temps.get("cpu"), mc["online"], now)
        self.check_pi_temp(cfg, pit)

        ok = self.level == "ok" and self.pi_level == "ok"
        self.push_kuma(ok, "temps ok" if ok else f"PC {self.level}, Pi {self.pi_level}")

        # --- history: one point a minute, 24 h
        if now - self.last_hist >= 60:
            self.history.append([int(now), temps.get("cpu") if temps else None, pit])
            self.history = self.history[-1440:]
            d.write_json("history.json", self.history)
            self.last_hist = now

        d.write_json("state.json", {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), "t": now,
            "k12": {"up": k12, "ip": cfg["k12_ip"], "temps": temps,
                    "tailscale_ip": self.agent_info.get("tailscale_ip") if k12 else None,
                    "docker": self.agent_info.get("docker") if k12 else None,
                    "woke_by_deck": bool(d.read_json("woke"))},
            "pi": {"temp": pit},
            "mc": mc, "join": d.join_address(cfg, {"k12": {"tailscale_ip": self.agent_info.get("tailscale_ip")}}),
            "idle_left_s": (max(0, int(idle_min * 60 - (now - self.idle_since)))
                            if self.idle_since and idle_min else None),
            "temp_level": self.level, "pi_level": self.pi_level,
            "mac_configured": bool(cfg.get("k12_mac")), "linked": bool(cfg.get("k12_user")),
        })
        d.trim_events()

    def reset_temp_state(self, quiet=False):
        if self.level != "ok" and not quiet:
            d.add_event("PC temperature back to normal.", "good", "temp")
        self.level = "ok"
        self.above_warn_since = self.above_hot_since = self.hot_stopped_at = None

    def check_k12_temp(self, cfg, cpu, mc_online, now):
        if cpu is None:
            return
        warn, hot = float(cfg["k12_cpu_warn"]), float(cfg["k12_cpu_hot"])
        self.above_warn_since = (self.above_warn_since or now) if cpu >= warn else None
        self.above_hot_since = (self.above_hot_since or now) if cpu >= hot else None

        if self.level in ("warn", "hot", "crit") and cpu < warn - 5 and not self.above_hot_since:
            self.reset_temp_state()
            return

        if self.above_hot_since and now - self.above_hot_since >= int(cfg["k12_cpu_hot_s"]) and self.level in ("ok", "warn"):
            self.level = "hot"
            d.add_event(f"PC is too hot ({cpu:.0f} C). " + ("Saving the world and stopping Minecraft." if mc_online else
                        "Minecraft isn't running - shutting the PC down if it stays this hot."), "bad", "temp")
            if mc_online:
                self.run_bg_safe(d.say_in_game, cfg, "The server is overheating - saving and stopping in 20 seconds.")
                time.sleep(20)
                self.run_bg_safe(d.stop_minecraft, cfg, "temperature watchdog", backup=False)
            self.hot_stopped_at = now
            return

        if (self.level == "hot" and self.hot_stopped_at and cpu >= hot
                and now - self.hot_stopped_at >= int(cfg["k12_crit_after_s"])):
            self.level = "crit"
            d.add_event(f"PC still too hot ({cpu:.0f} C) with Minecraft stopped - shutting it down. "
                        "Open the lid / check the case fans and vents.", "bad", "temp")
            self.run_bg_safe(d.stop_minecraft, cfg, "temperature watchdog", power_off=True, backup=False)
            return

        if self.above_warn_since and now - self.above_warn_since >= int(cfg["k12_cpu_warn_s"]) and self.level == "ok":
            self.level = "warn"
            d.add_event(f"PC is running warm ({cpu:.0f} C). Open the lid or give the case some air.", "warn", "temp")

    def check_pi_temp(self, cfg, t):
        if t is None:
            return
        new = "hot" if t >= float(cfg["pi_hot"]) else "warn" if t >= float(cfg["pi_warn"]) else "ok"
        if new == "ok" and self.pi_level != "ok" and t > float(cfg["pi_warn"]) - 5:
            return  # hysteresis
        if new != self.pi_level:
            if new == "ok":
                d.add_event("Pi temperature back to normal.", "good", "temp")
            else:
                d.add_event(f"Pi is {'too hot' if new == 'hot' else 'warm'} ({t:.0f} C). "
                            "It slows itself down at 85 C - check its fan.", "bad" if new == "hot" else "warn", "temp")
            self.pi_level = new

    def run(self):
        print("deckwatch running", flush=True)
        while True:
            start = time.time()
            try:
                self.tick()
            except Exception as e:  # never die - log and keep watching
                print(f"tick failed: {e!r}", file=sys.stderr, flush=True)
            time.sleep(max(1, PERIOD - (time.time() - start)))


if __name__ == "__main__":
    if "--once" in sys.argv:
        w = Watch()
        w.tick()
        print(json.dumps(d.read_json("state.json"), indent=2))
    else:
        Watch().run()
