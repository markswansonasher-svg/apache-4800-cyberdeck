#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Apache 4800 cyberdeck - Raspberry Pi 5 setup
#
# Run on the Pi (Raspberry Pi OS Lite 64-bit, Bookworm or newer):
#     cd ~/pi5 && sudo bash setup-pi.sh
#
# Safe to run again: every step checks whether it's already done.
#
# What it does:
#   1. Static IP 10.48.0.2 on eth0 (the case network; no gateway, so internet
#      stays on Wi-Fi)
#   2. Installs Tailscale and starts sign-in
#   3. Installs Docker + Uptime Kuma (port 3001)
#   4. Installs the deck services, all running as the user "deck":
#        deckpanel  - web page on port 8080 (Minecraft, temps, Wi-Fi, travel)
#        deckwatch  - temperature + player watchdog, idle stop
#        deckbot    - Discord bot (turned on by deck-discord-setup)
#   5. Travel mode: dnsmasq (DNS only) on 10.48.0.2 + the deck-net helper
#   6. Discord bot: Python venv with discord.py
#   7. Tries to learn the PC's MAC address automatically
# ---------------------------------------------------------------------------
set -euo pipefail

CASE_IP="10.48.0.2/24"
K12_IP="10.48.0.10"
PANEL_PORT=8080
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!!  %s\033[0m\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo:  sudo bash $0"; exit 1
fi
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"

# --- 0. packages ------------------------------------------------------------
say "Updating packages"
apt-get update -y
apt-get install -y curl ca-certificates python3 python3-venv iputils-ping jq \
  openssh-client iptables dnsmasq

# --- 1. case network on eth0 ------------------------------------------------
say "Case network: eth0 -> ${CASE_IP} (no gateway)"
CASE_LAN_DONE=1
if ip route show default 2>/dev/null | grep -q ' dev eth0'; then
  # e.g. set up on a desk with the Pi plugged into a PC/router for internet: taking eth0
  # over now would cut this script off from the internet (and any SSH session) mid-way.
  warn "eth0 is this Pi's internet connection right now - leaving it alone for now."
  warn "Once the Pi is wired into the case (internet over Wi-Fi), run this script again"
  warn "to put eth0 on the case network."
  CASE_LAN_DONE=0
elif command -v nmcli >/dev/null 2>&1; then
  # Turn off any auto (DHCP) profiles bound to eth0 so they don't fight ours.
  while IFS=: read -r name _dev type; do
    [[ "$type" == "802-3-ethernet" && "$name" != "case-lan" ]] || continue
    nmcli connection modify "$name" connection.autoconnect no || true
  done < <(nmcli -t -f NAME,DEVICE,TYPE connection show)

  if ! nmcli -t -f NAME connection show | grep -qx case-lan; then
    nmcli connection add type ethernet ifname eth0 con-name case-lan \
      ipv4.method manual ipv4.addresses "$CASE_IP" ipv4.never-default yes \
      ipv4.ignore-auto-dns yes ipv6.method disabled connection.autoconnect yes
  else
    nmcli connection modify case-lan ipv4.method manual ipv4.addresses "$CASE_IP" \
      ipv4.never-default yes ipv4.ignore-auto-dns yes
  fi
  nmcli connection up case-lan || warn "eth0 not connected yet - it will come up when the cable is in."
else
  warn "NetworkManager not found. Wi-Fi page and travel mode need it (Bookworm has it by default)."
fi

# --- 2. Tailscale -------------------------------------------------------------
say "Tailscale"
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled
if ! tailscale status >/dev/null 2>&1; then
  echo "Open the link below on your phone or PC to add the Pi to your tailnet."
  # --ssh lets you SSH in from the Tailscale app without managing keys.
  tailscale up --ssh --hostname=deckpi || warn "Tailscale sign-in skipped; run: sudo tailscale up --ssh --hostname=deckpi"
else
  echo "Already signed in to Tailscale."
fi

# --- 3. Docker + Uptime Kuma --------------------------------------------------
say "Docker + Uptime Kuma"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker "$REAL_USER" || true
systemctl enable --now docker
mkdir -p /opt/uptime-kuma
cp "$HERE/uptime-kuma-compose.yml" /opt/uptime-kuma/compose.yml
docker compose -f /opt/uptime-kuma/compose.yml up -d

# --- 4. deck user, files, services -------------------------------------------
say "Deck services (panel on port ${PANEL_PORT}, watchdog, bot)"
if ! id deck >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/deck --shell /usr/sbin/nologin deck
fi
install -d -o deck -g deck -m 755 /var/lib/deck
install -d -o deck -g deck -m 700 /var/lib/deck/.ssh
install -d -o deck -g deck -m 750 /var/lib/deck/backups   # Minecraft world backups
install -d -m 755 /opt/deck /etc/deck

# SSH key the Pi uses to reach the PC's agent (only allowed commands, see deck-agent.ps1)
if [[ ! -f /var/lib/deck/.ssh/id_ed25519 ]]; then
  sudo -u deck ssh-keygen -q -t ed25519 -N "" -C "deck@deckpi" -f /var/lib/deck/.ssh/id_ed25519
fi
# copy the public key next to this script so the PC script can find it on the USB drive
cp /var/lib/deck/.ssh/id_ed25519.pub "$HERE/deck-pi.pub" 2>/dev/null || true

for f in decklib.py deckpanel.py deckwatch.py deckbot.py panel.html \
         manifest.json icon.svg icon-180.png icon-512.png; do
  install -m 644 "$HERE/$f" "/opt/deck/$f"
done
chmod 755 /opt/deck/deckpanel.py /opt/deck/deckwatch.py /opt/deck/deckbot.py

# config: keep yours; carry the PC MAC over from the old /etc/deckpanel location
if [[ ! -f /etc/deck/config.json ]]; then
  install -m 644 "$HERE/config.example.json" /etc/deck/config.json
  if [[ -f /etc/deckpanel/config.json ]]; then
    old_mac="$(jq -r '.k12_mac // ""' /etc/deckpanel/config.json)"
    if [[ -n "$old_mac" ]]; then
      tmp="$(mktemp)"; jq --arg m "$old_mac" '.k12_mac=$m' /etc/deck/config.json > "$tmp" \
        && install -m 644 "$tmp" /etc/deck/config.json && rm -f "$tmp"
    fi
  fi
else
  # add any new settings this version knows about, keep your values
  tmp="$(mktemp)"
  jq -s '.[0] * .[1]' "$HERE/config.example.json" /etc/deck/config.json > "$tmp" \
    && install -m 644 "$tmp" /etc/deck/config.json && rm -f "$tmp"
fi
rm -rf /opt/deckpanel   # old location (v1)

install -m 755 "$HERE/wake-k12"           /usr/local/bin/wake-k12
install -m 755 "$HERE/deck-status"        /usr/local/bin/deck-status
install -m 755 "$HERE/deck-set-mac"       /usr/local/bin/deck-set-mac
install -m 755 "$HERE/deck-link-k12"      /usr/local/bin/deck-link-k12
install -m 755 "$HERE/deck-discord-setup" /usr/local/bin/deck-discord-setup
install -m 755 "$HERE/deck-tailscale-setup" /usr/local/bin/deck-tailscale-setup
install -m 755 "$HERE/deck-curseforge-setup" /usr/local/bin/deck-curseforge-setup
for u in deckpanel deckwatch deckbot deck-net-restore; do
  install -m 644 "$HERE/$u.service" "/etc/systemd/system/$u.service"
done

# --- 5. travel mode ---------------------------------------------------------
say "Travel mode: deck-net helper + DNS for the case network"
install -o root -g root -m 755 "$HERE/deck-net" /usr/local/sbin/deck-net
install -o root -g root -m 440 "$HERE/sudoers-deck" /etc/sudoers.d/deck
visudo -cf /etc/sudoers.d/deck >/dev/null || { warn "sudoers file invalid - removed"; rm -f /etc/sudoers.d/deck; }
if command -v pihole >/dev/null 2>&1; then
  warn "Pi-hole is installed and also wants port 53. This kit doesn't use it any more:"
  warn "remove it with  pihole uninstall  then run this script again."
fi
install -m 644 "$HERE/dnsmasq-deck.conf" /etc/dnsmasq.d/deck.conf
systemctl enable dnsmasq >/dev/null 2>&1 || true
systemctl restart dnsmasq || warn "dnsmasq didn't start (is something else using port 53?)"

systemctl daemon-reload
systemctl enable --now deck-net-restore deckwatch deckpanel
systemctl restart deckwatch deckpanel

# --- 6. Discord bot -------------------------------------------------------------
say "Discord bot (Python venv)"
if [[ ! -x /opt/deck/venv/bin/python ]]; then
  python3 -m venv /opt/deck/venv
fi
/opt/deck/venv/bin/pip install --quiet --upgrade pip
/opt/deck/venv/bin/pip install --quiet "discord.py>=2.5,<3"
if [[ -f /etc/deck/discord.env ]]; then
  systemctl enable deckbot >/dev/null; systemctl restart deckbot
  echo "Bot restarted."
else
  echo "Bot installed but not set up. When you have a bot token (README > Discord bot):"
  echo "    sudo deck-discord-setup"
fi

# --- 7. learn PC MAC -----------------------------------------------------------
say "Looking for the PC on the case network (${K12_IP})"
if /usr/local/bin/deck-set-mac --auto; then
  :
else
  warn "PC not found yet. Once the PC setup script has run and the PC is on,"
  warn "run:   sudo deck-set-mac"
fi

TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"

cat <<EOF

============================================================
 Done.
   Deck Panel:   http://deckpi:${PANEL_PORT}   (via Tailscale)  ${TS_IP:+ / http://${TS_IP}:${PANEL_PORT}}
                 http://10.48.0.2:${PANEL_PORT}  (from the laptop)
   Uptime Kuma:  http://${TS_IP:-deckpi}:3001   (make an account on first visit)
   Commands:     wake-k12   deck-status   sudo deck-set-mac
                 sudo deck-link-k12 <user>   sudo deck-discord-setup
                 sudo deck-tailscale-setup   (for /mc invite)
                 sudo deck-curseforge-setup  (for modpacks)

 Next:
$( [[ $CASE_LAN_DONE == 1 ]] || echo "   * NOT DONE: case network on eth0 (it was the internet). Run this script again
     once the Pi is in the case with Wi-Fi for internet." )
   * On the PC: k12-windows\\setup-k12-agent.cmd  (it prints a
     "sudo deck-link-k12 ..." line - run that here).
   * Discord bot: README > "Discord bot".
   * Log out and back in so 'docker' works without sudo.
============================================================
EOF
