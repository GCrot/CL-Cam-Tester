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
- Enable SSH under the Services tab
- Do not set up WiFi

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

The Pi is configured with a static IP of **192.168.100.1/24** on the ethernet port. The app automatically configures the network to reach cameras on any subnet.

To SSH in after install, connect your computer directly to the Pi via ethernet and set your computer's ethernet adapter to a static IP in the `192.168.100.x` range (e.g. `192.168.100.100`), then:

```bash
ssh pi@192.168.100.1
```

---

## Supported Cameras

### NDI
- Auto-discovered via mDNS multicast
- Tested with AIDA HD-NDI-X20
- Factory reset and recommended settings push supported via AIDA HTTP API

---

## Factory Reset (AIDA NDI Cameras)

The factory reset workflow:
1. Sends factory reset command via AIDA HTTP API
2. Waits for camera to reboot (~30–60 seconds)
3. Connects to camera at default IP (`192.168.1.188`)
4. Pushes recommended default settings from `config.json`
5. Shows confirmation screen when complete

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
If empty, check the USB connection and reboot.

**NDI camera not found**
- Ensure camera is powered and connected via ethernet
- Check `ip addr show eth0` — Pi should show `192.168.100.1/24`
- Try scanning again — NDI discovery can take a few seconds

**SSH not reachable after install**
- Connect a keyboard and monitor to the Pi
- Press Ctrl+Alt+F2 to get a terminal
- Check `ip addr show eth0` for the current IP
