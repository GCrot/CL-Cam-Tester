# CL-Cam-Tester

A handheld camera testing tool for NDI and RTSP cameras. Built on Raspberry Pi 5 with a 5" touchscreen and Stream Deck Mini for shop use.

---

## Hardware Required

| Component | Model |
|-----------|-------|
| Single-board computer | [Raspberry Pi 5 4GB](https://www.pishop.ca/product/raspberry-pi-5-4gb/) |
| PoE HAT | [Power Over Ethernet HAT (F) For Raspberry Pi 5](https://www.pishop.ca/product/power-over-ethernet-hat-f-for-raspberry-pi-5-cooling-fan-802-3af-at/) |
| Display | [Raspberry Pi 5" Touch Display 2](https://www.pishop.ca/product/raspberry-pi-5-touch-display-2-portrait/) |
| Input | [Stream Deck Module (6 Keys)](https://www.elgato.com/us/en/p/stream-deck-module-6-keys) |
| PoE Injector | [MokerLink 3 Port Gigabit PoE Passthrough Switch](https://www.amazon.ca/dp/B0C2828P3F) |
| Storage | microSD card (16GB minimum, Class 10) |
| Cables | Assorted cables for interconnection |

---

## Software Install

### Step 1 — Flash the SD Card

Download and install **Raspberry Pi Imager** from [raspberrypi.com/software](https://www.raspberrypi.com/software).

Flash with these settings:
- **Device:** Raspberry Pi 5
- **OS:** Raspberry Pi OS Lite (64-bit)
- **Storage:** Your SD card

In **Edit Settings** before flashing:
- Set hostname: `camtester`
- Set username: `pi` and password: `admin`
- **Configure WiFi** with your network credentials (required for setup and updates)
- Enable SSH under the Services tab

> **Important:** WiFi must be configured. The device uses WiFi for internet/SSH and dedicates the ethernet port to cameras.

### Step 2 — First Boot

Insert the SD card, power on, and SSH in over WiFi:

```bash
ssh pi@camtester.local
```

### Step 3 — Install

Download and run the installer detached, so it survives any brief network drop during setup:

```bash
curl -sSL https://raw.githubusercontent.com/GCrot/CL-Cam-Tester/main/install.sh -o /tmp/install.sh
sudo nohup bash /tmp/install.sh > /tmp/install.log 2>&1 &
```

Watch progress with:

```bash
tail -f /tmp/install.log
```

The install takes 10–15 minutes. When you see the completion banner in the log, press Ctrl+C to stop watching, then reboot:

```bash
sudo reboot
```

The app starts automatically on the touchscreen.

---

## Network Architecture

The device uses two network interfaces with distinct roles:

- **WiFi (wlan0)** — DHCP from your network. Provides internet for updates and SSH access. Always active.
- **Ethernet (eth0)** — Static `192.168.100.1/24` plus link-local `169.254.1.1/16`, dedicated to cameras. Never carries internet traffic.

The Pi runs a DHCP server (dnsmasq) on the ethernet port serving `192.168.100.50–99`, so factory-defaulted cameras that boot into DHCP mode automatically get an address.

### SSH Access

Over WiFi (normal): `ssh pi@camtester.local` or `ssh pi@<wifi-ip>`

Over ethernet (direct): set your computer's ethernet adapter to `192.168.100.x`, then `ssh pi@192.168.100.1`

---

## Using the Device

1. **Connect a camera** to the ethernet port (via the PoE injector)
2. **Press SCAN** (Stream Deck button 1) — discovers NDI and RTSP cameras
3. **Press CONNECT** — views the camera stream
4. **Zoom** (NDI PTZ cameras) — hold buttons 1 and 4
5. **Factory Reset** — resets the camera to defaults
   - NDI (AIDA): resets and loads recommended settings
   - RTSP (Hanwha): resets to factory defaults

### RTSP Camera Password Setup

Hanwha/Wisenet cameras require a password to be set on first use. If a camera is found but locked, the app offers:
- **OPEN BROWSER** — opens the camera's web page on the touchscreen
- **AUTO-FILL PASSWORD** — automatically sets the password to `Repair2023!`
- **DONE** — closes the browser and rescans

### Software Updates

Hold the bottom row of the Stream Deck (buttons 4+5+6) to trigger a hidden update check. The device pulls the latest version from GitHub over WiFi.

---

## Supported Cameras

### NDI
- Auto-discovered via mDNS multicast
- Tested with AIDA HD-NDI-X20
- Factory reset with recommended settings push
- PTZ zoom control via VISCA-over-IP

### RTSP
- Auto-discovered via ONVIF WS-Discovery
- Tested with Hanwha XNZ-L6320A (Robe RoboSpot)
- Stream via `/profile2/media.smp` (H.264 1080p)
- Factory reset via Hanwha CGI API

---

## Default Credentials

| Item | Value |
|------|-------|
| Pi login | `pi` / `admin` |
| Camera shop default | `admin` / `Repair2023!` |
| AIDA NDI default | `admin` / `admin` |
| Robe RoboSpot default | `admin` / `RoboSpot10` |

---

## Files

| File | Description |
|------|-------------|
| `camtester.py` | Main application |
| `install.sh` | Full installation script |
| `config.json` | AIDA camera recommended default settings |

---

## Troubleshooting

**App doesn't start after reboot**
```bash
DISPLAY=:0 python3 ~/camtester/camtester.py
```
Check the output for errors.

**No internet / can't update**
```bash
ip route show
```
The default route must be via `wlan0`. If eth0 has a default route, the WiFi internet is blocked.

**Stream Deck not detected**
```bash
python3 -c "from StreamDeck.DeviceManager import DeviceManager; print(DeviceManager().enumerate())"
```
If empty, check the USB connection and reboot.

**Camera not found**
- Ensure the camera is powered (PoE) and connected to eth0
- Check `ip addr show eth0` shows `192.168.100.1/24`
- Check `journalctl -u dnsmasq -n 20` to see if the camera got a DHCP lease
- Try scanning again — discovery can take a few seconds

**SSH not reachable**
- Connect a keyboard and monitor
- Press Ctrl+Alt+F2 for a terminal
- Check `ip addr show wlan0` for the WiFi IP
