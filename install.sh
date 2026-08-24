#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  CL-Cam-Tester Install Script
#  Raspberry Pi 5 + Touch Display 2 (5") + Stream Deck Mini
#
#  Usage (fresh Raspberry Pi OS Lite 64-bit):
#    curl -sSL https://raw.githubusercontent.com/GCrot/CL-Cam-Tester/main/install.sh | sudo bash
#
#  Or clone and run locally:
#    git clone https://github.com/GCrot/CL-Cam-Tester.git
#    cd CL-Cam-Tester
#    sudo bash install.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Must be root ─────────────────────────────────────────────────────────────
[ "$EUID" -ne 0 ] && error "Please run as root: sudo bash install.sh"

REAL_USER=${SUDO_USER:-pi}
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
APP_DIR="$REAL_HOME/camtester"
REPO_URL="https://raw.githubusercontent.com/GCrot/CL-Cam-Tester/main"

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  CL-Cam-Tester Installer${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
info "Installing for user: $REAL_USER"
info "App directory: $APP_DIR"
echo ""

# ─────────────────────────────────────────────
#  1. SYSTEM UPDATE
# ─────────────────────────────────────────────
info "Updating system packages…"
apt-get update -qq
apt-get upgrade -y -qq
success "System updated"

# ─────────────────────────────────────────────
#  2. CORE PACKAGES
# ─────────────────────────────────────────────
info "Installing core packages…"
apt-get install -y -qq \
    xorg \
    openbox \
    python3 \
    python3-tk \
    python3-pip \
    mpv \
    ffmpeg \
    nmap \
    arping \
    dnsmasq \
    avahi-utils \
    unclutter \
    xdotool \
    wmctrl \
    chromium \
    git \
    curl \
    wget \
    libhidapi-hidraw0 \
    libhidapi-libusb0 \
    fonts-dejavu
success "Core packages installed"

# ─────────────────────────────────────────────
#  3. PYTHON PACKAGES
# ─────────────────────────────────────────────
info "Installing Python packages…"
pip3 install --break-system-packages \
    ndi-python \
    netifaces \
    pillow \
    numpy \
    streamdeck \
    wsdiscovery 2>/dev/null || true
success "Python packages installed"

# ─────────────────────────────────────────────
#  4. DISPLAY ROTATION (Touch Display 2 — landscape)
# ─────────────────────────────────────────────
info "Configuring display rotation…"
CONFIG_FILE="/boot/firmware/config.txt"
sed -i '/display_rotate/d' "$CONFIG_FILE"
echo "" >> "$CONFIG_FILE"
echo "# CL-Cam-Tester: Touch Display 2 landscape rotation" >> "$CONFIG_FILE"
echo "display_rotate=1" >> "$CONFIG_FILE"

mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/40-touch-rotate.conf << 'EOF'
Section "InputClass"
    Identifier "Touch Display 2 rotation"
    MatchIsTouchscreen "on"
    Option "TransformationMatrix" "0 1 0 -1 0 1 0 0 1"
EndSection
EOF

cat > /etc/X11/xorg.conf.d/90-display-rotate.conf << 'EOF'
Section "Monitor"
    Identifier "DSI-1"
    Option "Rotate" "right"
EndSection

Section "Screen"
    Identifier "Screen0"
    Monitor "DSI-1"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "1280x720"
    EndSubSection
EndSection
EOF
success "Display rotation configured"

# ─────────────────────────────────────────────
#  5. OPENBOX AUTOSTART
# ─────────────────────────────────────────────
info "Configuring Openbox autostart…"
OB_DIR="$REAL_HOME/.config/openbox"
mkdir -p "$OB_DIR"
cat > "$OB_DIR/autostart" << EOF
# CL-Cam-Tester autostart
unclutter -idle 1 -root &
xsetroot -solid black &
xset s off &
xset -dpms &
xset s noblank &
xrandr --output DSI-1 --rotate right &
nmcli connection up eth0-static 2>/dev/null &

sleep 1
python3 $APP_DIR/camtester.py &
EOF
chown -R "$REAL_USER:$REAL_USER" "$OB_DIR"
success "Openbox autostart configured"

# ─────────────────────────────────────────────
#  6. AUTO-LOGIN & AUTO-START X
# ─────────────────────────────────────────────
info "Configuring auto-login…"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $REAL_USER --noclear %I \$TERM
EOF

BASH_PROFILE="$REAL_HOME/.bash_profile"
if ! grep -q "startx" "$BASH_PROFILE" 2>/dev/null; then
    cat >> "$BASH_PROFILE" << 'EOF'

# Auto-start Openbox on tty1
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec startx /usr/bin/openbox-session
fi
EOF
fi
chown "$REAL_USER:$REAL_USER" "$BASH_PROFILE"
success "Auto-login configured"

# ─────────────────────────────────────────────
#  7. STATIC IP (192.168.100.1/24)
# ─────────────────────────────────────────────
info "Configuring DHCP server for cameras (dnsmasq)…"
cat > /etc/dnsmasq.d/camtester.conf << 'EOF'
interface=eth0
bind-interfaces
dhcp-range=192.168.100.50,192.168.100.99,255.255.255.0,1h
dhcp-option=3
dhcp-option=6
EOF
systemctl enable dnsmasq
systemctl restart dnsmasq
success "dnsmasq configured (192.168.100.50-99 on eth0)"
# Static IP for camera testing + link-local for cameras that default to DHCP
nmcli connection delete eth0-static 2>/dev/null || true
nmcli connection add \
  con-name "eth0-static" \
  ifname eth0 \
  type ethernet \
  ipv4.method manual \
  ipv4.addresses "192.168.100.1/24" \
  ipv4.never-default yes \
  connection.autoconnect yes 2>/dev/null || true

# Add link-local separately
nmcli connection modify eth0-static +ipv4.addresses "169.254.1.1/16" 2>/dev/null || true

# DHCP profile — used only for updates, autoconnect off
nmcli connection delete eth0-dhcp 2>/dev/null || true
nmcli connection add \
  con-name "eth0-dhcp" \
  ifname eth0 \
  type ethernet \
  ipv4.method auto \
  ipv4.dhcp-timeout 15 \
  connection.autoconnect no 2>/dev/null || true

success "Network configured (static: 192.168.100.1/24)"

# ─────────────────────────────────────────────
#  ETH MANAGER SERVICE
# ─────────────────────────────────────────────
info "Configuring ethernet manager service…"
cat > /usr/local/bin/eth-manager.sh << 'SCRIPT'
#!/bin/bash
IFACE="eth0"
FALLBACK_IP="192.168.100.1/24"

ip link set "$IFACE" up
nmcli connection up eth0-dhcp 2>/dev/null

for i in $(seq 1 5); do
    sleep 1
    IP=$(ip -4 addr show "$IFACE" | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '169.254')
    if [ -n "$IP" ]; then
        logger "eth-manager: DHCP got $IP"
        while true; do
            sleep 5
            IP=$(ip -4 addr show "$IFACE" | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '169.254')
            if [ -z "$IP" ]; then
                ip addr add "$FALLBACK_IP" dev "$IFACE" 2>/dev/null
                logger "eth-manager: DHCP dropped, applied fallback"
                break
            fi
        done
        break
    fi
done

IP=$(ip -4 addr show "$IFACE" | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '169.254')
if [ -z "$IP" ]; then
    ip addr add "$FALLBACK_IP" dev "$IFACE" 2>/dev/null
    logger "eth-manager: no DHCP, applied fallback $FALLBACK_IP"
fi

while true; do
    sleep 5
    ip link set "$IFACE" up 2>/dev/null
    IP=$(ip -4 addr show "$IFACE" | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '169.254')
    if [ -z "$IP" ]; then
        ip addr add "$FALLBACK_IP" dev "$IFACE" 2>/dev/null
        logger "eth-manager: re-applied fallback"
    fi
done
SCRIPT
chmod +x /usr/local/bin/eth-manager.sh

cat > /etc/systemd/system/eth-fallback.service << 'EOF'
[Unit]
Description=Ethernet interface manager
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
ExecStart=/usr/local/bin/eth-manager.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable eth-fallback.service
success "Ethernet manager configured"

# ─────────────────────────────────────────────
#  8. STREAM DECK UDEV RULE
# ─────────────────────────────────────────────
info "Configuring Stream Deck permissions…"
cat > /etc/udev/rules.d/70-streamdeck.rules << 'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0fd9", GROUP="plugdev", MODE="0660"
EOF
udevadm control --reload-rules
usermod -aG plugdev,video,input,netdev,render "$REAL_USER"
success "Stream Deck permissions configured"

# ─────────────────────────────────────────────
#  9. SUDOERS — passwordless ip and reboot
# ─────────────────────────────────────────────
info "Configuring sudoers…"
echo "$REAL_USER ALL=(ALL) NOPASSWD: /sbin/ip, /sbin/reboot, /usr/sbin/arping, /usr/bin/nmcli" \
  > /etc/sudoers.d/camtester
chmod 440 /etc/sudoers.d/camtester
success "Sudoers configured"

# ─────────────────────────────────────────────
#  10. DISABLE NM WAIT ONLINE (faster boot)
# ─────────────────────────────────────────────
info "Optimising boot time…"
systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
success "Boot optimised"

# ─────────────────────────────────────────────
#  11. DOWNLOAD APP FILES
# ─────────────────────────────────────────────
info "Downloading CL-Cam-Tester…"
mkdir -p "$APP_DIR"

curl -sSL "$REPO_URL/camtester.py" -o "$APP_DIR/camtester.py"
curl -sSL "$REPO_URL/config.json"  -o "$APP_DIR/config.json"

chown -R "$REAL_USER:$REAL_USER" "$APP_DIR"
success "App downloaded to $APP_DIR"

# ─────────────────────────────────────────────
#  12. NDI-FIND WRAPPER
# ─────────────────────────────────────────────
info "Creating NDI discovery wrapper…"
cat > /usr/local/bin/ndi-find << 'PYEOF'
#!/usr/bin/env python3
import sys, time
timeout = 3
args = sys.argv[1:]
if "--timeout" in args:
    try:
        timeout = int(args[args.index("--timeout") + 1])
    except Exception:
        pass
try:
    import NDIlib as ndi
    if not ndi.initialize():
        sys.exit(1)
    finder = ndi.find_create_v2(ndi.FindCreate())
    if not finder:
        ndi.destroy()
        sys.exit(1)
    time.sleep(timeout)
    for s in ndi.find_get_current_sources(finder):
        print(f"{s.ndi_name} | {s.url_address} | ")
    ndi.find_destroy(finder)
    ndi.destroy()
except Exception:
    pass
PYEOF
chmod +x /usr/local/bin/ndi-find
success "NDI wrapper created"

# ─────────────────────────────────────────────
#  DONE
# ─────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  App:      $APP_DIR/camtester.py"
echo "  Config:   $APP_DIR/config.json"
echo "  Static IP: 192.168.100.1/24"
echo ""
echo -e "${YELLOW}  Reboot to start the app:${NC}"
echo "  sudo reboot"
echo ""
