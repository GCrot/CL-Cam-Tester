# CL-Cam-Tester

A handheld camera testing tool for NDI and RTSP cameras. Built on Raspberry Pi 5 with a 5" touchscreen and Stream Deck Mini for field use.

---

## Hardware Required

| Component | Model |
|-----------|-------|
| Single-board computer | Raspberry Pi 5 (4GB or 8GB) |
| Display | Raspberry Pi Touch Display 2 (5") |
| Input | Elgato Stream Deck Mini |
| Storage | microSD card (16GB minimum, Class 10) |
| Power | USB-C power supply (5V/5A recommended) |
| Networking | Ethernet cable |

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
- Set username: `pi` and a password
- Enable SSH under the Services tab
- Optionally set WiFi credentials for initial setup

### Step 2 — First Boot

Insert the SD card, connect ethernet to your network, and power on. SSH in:

```bash
ssh pi@camtester.local
```

### Step 3 — Install

Run the one-line installer:

```bash
curl -sSL https://raw.githubusercontent.com/GCrot/CL-Cam-Tester/main/install.sh | sudo bash
```

This will take 5–10 minutes. When complete, reboot:

```bash
sudo reboot
```

The app will start automatically on the touchscreen.

---

## Network

The Pi is configured with a static IP of **192.168.100.1/24** on the ethernet port. This allows direct connection to cameras on any subnet — the app automatically adds a matching IP when a camera is discovered.

To SSH in after install, connect your computer directly to the Pi via ethernet and set your computer's ethernet adapter to a static IP in the `192.168.100.x` range (e.g. `192.168.100.100`), then:

```bash
ssh pi@192.168.100.1
```

---

## Stream Deck Button Layout

The Stream Deck Mini has 6 buttons (2 rows × 3 columns). Button assignments change per screen:

| Screen | Button 1 | Button 4 | Button 5 | Button 6 |
|--------|----------|----------|----------|----------|
| Home | SCAN | — | — | REBOOT |
| Scanning | — | — | — | CANCEL |
| Results | RESCAN | CONNECT | — | HOME |
| Playback | — | — | RESET | STOP |
| Reset confirm | RESET | — | — | CANCEL |
| Reset success | DONE | — | — | — |

---

## Supported Cameras

### NDI
- Auto-discovered via mDNS multicast
- Tested with AIDA HD-NDI-X20
- Factory reset and config push supported via AIDA HTTP API

### RTSP
- Discovered via network port scan (ports 554 and 8554)
- Played back via mpv
- Common credential combinations tried automatically

---

## Factory Reset (AIDA NDI Cameras)

The factory reset workflow:
1. Sends factory reset command via AIDA HTTP API
2. Waits for camera to reboot (~30–60 seconds)
3. Connects to camera at default IP (`192.168.1.188`)
4. Pushes recommended default settings from `config.json`
5. Shows confirmation screen

Default camera credentials: `admin:admin`

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

**Stream Deck not detected**
```bash
python3 -c "from StreamDeck.DeviceManager import DeviceManager; print(DeviceManager().enumerate())"
```
If empty, check the USB connection and that the udev rules applied correctly (may need a reboot).

**NDI camera not found**
- Ensure camera is powered and connected via ethernet
- Check `ip addr show eth0` — Pi should show `192.168.100.1/24`
- Try scanning again — NDI discovery can take a few seconds

**SSH not reachable after install**
- Connect keyboard and monitor
- Press Ctrl+Alt+F2 to get a terminal
- Check `ip addr show eth0` for the current IP
