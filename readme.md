# WLED Network Controller

A terminal-based Python controller for WLED devices — auto-discovers them on your network via mDNS and gives you full control through the WLED JSON API.

## Features
- 🔍 Auto-discovery via mDNS (`_wled._tcp.local.`)
- 📋 Lists all WLED devices on your network
- 🎛️ Full JSON API control (power, brightness, color, effects, palettes, presets, nightlight)
- 🖥️ Rich terminal UI with tables and menus
- ⌨️ Manual IP fallback if mDNS fails

## Requirements
- Python 3.10+
- WLED device(s) on the same network

## Installation
```bash
git clone https://github.com/yourname/wled-controller.git
cd wled-controller
pip install -r requirements.txt
```

## Dependencies (`requirements.txt`)
```
zeroconf
requests
rich
```

## Usage
```bash
python wled_controller.py
```

The script will scan for 5 seconds, list found devices, then open an interactive menu.

## Menu Options
| # | Action |
|---|--------|
| 1 | Show device status (firmware, WiFi, segments) |
| 2 | Toggle on/off |
| 3 | Set brightness (0–255) |
| 4 | Set RGB color per segment |
| 5 | Set effect (picked from device's effect list) |
| 6 | Set palette (picked from device's palette list) |
| 7 | Set speed & intensity |
| 8 | Nightlight mode |
| 9 | Apply preset |
| 10 | Raw JSON POST (advanced) |
| 11 | Reboot device |

## API Reference
Built on the [WLED JSON API](https://kno.wled.ge/interfaces/json-api/). Key endpoints used:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/json` | GET | Full state, info, effects, palettes |
| `/json/state` | GET | Current light state |
| `/json/state` | POST | Control lights |
| `/json/info` | GET | Device info |
| `/json/eff` | GET | Effect list |
| `/json/pal` | GET | Palette list |
