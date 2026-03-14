#!/usr/bin/env python3
"""
WLED Controller - mDNS Discovery + Full JSON API
Discovers WLED devices on the network via mDNS (_wled._tcp.local.)
and provides a full-featured CLI to control them via the WLED JSON API.

Dependencies:
    pip install zeroconf requests rich

WLED JSON API reference: https://kno.wled.ge/interfaces/json-api/
"""

import json
import sys
import time
import threading
import ipaddress
from typing import Optional

import requests
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich import print as rprint
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

console = Console()

# ─────────────────────────────────────────────
# mDNS Discovery
# ─────────────────────────────────────────────

class WLEDListener(ServiceListener):
    """Listens for WLED mDNS announcements (_wled._tcp.local.)"""

    def __init__(self):
        self.devices: list[dict] = []
        self._lock = threading.Lock()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info:
            ip = ".".join(str(b) for b in info.addresses[0]) if info.addresses else None
            port = info.port or 80
            hostname = info.server or name
            props = {k.decode(): v.decode() if isinstance(v, bytes) else v
                     for k, v in (info.properties or {}).items()}
            device = {
                "name": name.replace("._wled._tcp.local.", ""),
                "hostname": hostname,
                "ip": ip,
                "port": port,
                "props": props,
            }
            with self._lock:
                # Avoid duplicates
                if not any(d["ip"] == ip for d in self.devices):
                    self.devices.append(device)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with self._lock:
            self.devices = [d for d in self.devices
                            if d["name"] != name.replace("._wled._tcp.local.", "")]


def discover_wled_devices(timeout: float = 5.0) -> list[dict]:
    """Scan the network for WLED devices via mDNS for `timeout` seconds."""
    zc = Zeroconf()
    listener = WLEDListener()
    browser = ServiceBrowser(zc, "_wled._tcp.local.", listener)  # noqa

    with console.status(
        f"[bold cyan]Scanning for WLED devices via mDNS ({timeout:.0f}s)…[/bold cyan]",
        spinner="dots",
    ):
        time.sleep(timeout)

    zc.close()
    return listener.devices


# ─────────────────────────────────────────────
# WLED JSON API wrapper
# ─────────────────────────────────────────────

class WLEDAPI:
    """Thin wrapper around the WLED JSON API."""

    def __init__(self, ip: str, port: int = 80, timeout: int = 5):
        self.base = f"http://{ip}:{port}"
        self.timeout = timeout

    # ── GET helpers ──────────────────────────

    def get_all(self) -> dict:
        """GET /json  →  {state, info, effects, palettes}"""
        return self._get("/json")

    def get_state(self) -> dict:
        """GET /json/state"""
        return self._get("/json/state")

    def get_info(self) -> dict:
        """GET /json/info"""
        return self._get("/json/info")

    def get_effects(self) -> list[str]:
        """GET /json/eff  →  list of effect names"""
        return self._get("/json/eff")

    def get_palettes(self) -> list[str]:
        """GET /json/pal  →  list of palette names"""
        return self._get("/json/pal")

    # ── POST helpers ─────────────────────────

    def set_state(self, payload: dict) -> dict:
        """POST /json/state  with a partial state object."""
        return self._post("/json/state", payload)

    # ── Convenience methods ───────────────────

    def toggle(self) -> dict:
        return self.set_state({"on": "t", "v": True})

    def turn_on(self) -> dict:
        return self.set_state({"on": True})

    def turn_off(self) -> dict:
        return self.set_state({"on": False})

    def set_brightness(self, bri: int) -> dict:
        """bri: 0-255"""
        return self.set_state({"bri": max(0, min(255, bri))})

    def set_color(self, r: int, g: int, b: int, segment: int = 0) -> dict:
        """Set primary color of a segment (RGB 0-255 each)."""
        return self.set_state({"seg": [{"id": segment, "col": [[r, g, b]]}]})

    def set_effect(self, effect_id: int, segment: int = 0) -> dict:
        return self.set_state({"seg": [{"id": segment, "fx": effect_id}]})

    def set_palette(self, palette_id: int, segment: int = 0) -> dict:
        return self.set_state({"seg": [{"id": segment, "pal": palette_id}]})

    def set_speed(self, speed: int, segment: int = 0) -> dict:
        """speed: 0-255"""
        return self.set_state({"seg": [{"id": segment, "sx": max(0, min(255, speed))}]})

    def set_intensity(self, intensity: int, segment: int = 0) -> dict:
        """intensity: 0-255"""
        return self.set_state({"seg": [{"id": segment, "ix": max(0, min(255, intensity))}]})

    def nightlight(self, on: bool = True, duration: int = 60, mode: int = 1) -> dict:
        """Nightlight mode. mode: 0=instant,1=fade,2=color fade,3=sunrise."""
        return self.set_state({"nl": {"on": on, "dur": duration, "mode": mode}})

    def set_preset(self, preset_id: int) -> dict:
        return self.set_state({"ps": preset_id})

    def reboot(self) -> dict:
        return self._post("/json/state", {"rb": True})

    # ── Internal ──────────────────────────────

    def _get(self, path: str) -> dict | list:
        r = requests.get(self.base + path, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        r = requests.post(
            self.base + path,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()


# ─────────────────────────────────────────────
# Rich UI helpers
# ─────────────────────────────────────────────

def show_device_list(devices: list[dict]) -> None:
    table = Table(title="[bold magenta]Discovered WLED Devices[/bold magenta]",
                  show_lines=True, border_style="bright_blue")
    table.add_column("#", style="bold yellow", justify="right")
    table.add_column("Name", style="bold white")
    table.add_column("IP Address", style="cyan")
    table.add_column("Hostname", style="dim")
    table.add_column("Port", justify="center")

    for i, d in enumerate(devices, 1):
        table.add_row(str(i), d["name"], d["ip"] or "?", d["hostname"], str(d["port"]))

    console.print(table)


def show_device_status(api: WLEDAPI, name: str) -> None:
    try:
        data = api.get_all()
    except Exception as e:
        console.print(f"[red]Error fetching status: {e}[/red]")
        return

    state = data.get("state", {})
    info = data.get("info", {})

    # Info panel
    info_text = (
        f"[bold]Firmware:[/bold] {info.get('ver', '?')}   "
        f"[bold]SSID:[/bold] {info.get('wifi', {}).get('ssid', '?')}   "
        f"[bold]Signal:[/bold] {info.get('wifi', {}).get('signal', '?')}%\n"
        f"[bold]LEDs:[/bold] {info.get('leds', {}).get('count', '?')}   "
        f"[bold]Free RAM:[/bold] {info.get('freeheap', '?')} bytes   "
        f"[bold]Uptime:[/bold] {info.get('uptime', '?')}s"
    )
    console.print(Panel(info_text, title=f"[bold green]📡 {name}[/bold green]",
                        border_style="green"))

    # State table
    on = "[green]ON[/green]" if state.get("on") else "[red]OFF[/red]"
    bri = state.get("bri", "?")
    ps = state.get("ps", -1)
    pl = state.get("pl", -1)

    segs = state.get("seg", [])
    effects = data.get("effects", [])
    palettes = data.get("pal", [])

    state_table = Table(show_header=False, box=None, padding=(0, 2))
    state_table.add_column("Key", style="bold cyan")
    state_table.add_column("Value", style="white")
    state_table.add_row("Power", on)
    state_table.add_row("Brightness", f"{bri}/255  ({round(bri/255*100)}%)" if isinstance(bri, int) else str(bri))
    state_table.add_row("Active Preset", str(ps))
    state_table.add_row("Active Playlist", str(pl))
    console.print(state_table)

    if segs:
        seg_table = Table(title="Segments", show_lines=True, border_style="dim")
        seg_table.add_column("ID", justify="center")
        seg_table.add_column("On", justify="center")
        seg_table.add_column("Effect", style="yellow")
        seg_table.add_column("Palette", style="magenta")
        seg_table.add_column("Speed", justify="center")
        seg_table.add_column("Intensity", justify="center")
        seg_table.add_column("Color (RGB)", style="cyan")

        for seg in segs:
            fx_id = seg.get("fx", 0)
            pal_id = seg.get("pal", 0)
            fx_name = effects[fx_id] if effects and fx_id < len(effects) else str(fx_id)
            pal_name = palettes[pal_id] if palettes and pal_id < len(palettes) else str(pal_id)
            col = seg.get("col", [[]])[0] if seg.get("col") else []
            col_str = f"rgb({col[0]},{col[1]},{col[2]})" if len(col) >= 3 else "?"
            seg_table.add_row(
                str(seg.get("id", "?")),
                "[green]✓[/green]" if seg.get("on") else "[red]✗[/red]",
                fx_name, pal_name,
                str(seg.get("sx", "?")),
                str(seg.get("ix", "?")),
                col_str,
            )
        console.print(seg_table)


def pick_from_list(items: list[str], label: str) -> Optional[int]:
    """Show a numbered list and let the user pick an index."""
    table = Table(title=label, show_lines=False, border_style="dim")
    table.add_column("ID", justify="right", style="yellow")
    table.add_column("Name")
    for i, name in enumerate(items):
        if name not in ("RSVD", "-"):
            table.add_row(str(i), name)
    console.print(table)
    choice = IntPrompt.ask("Enter ID (or -1 to cancel)", default=-1)
    return None if choice < 0 else choice


# ─────────────────────────────────────────────
# Interactive menu
# ─────────────────────────────────────────────

def device_menu(device: dict) -> None:
    api = WLEDAPI(device["ip"], device["port"])
    name = device["name"]

    while True:
        console.rule(f"[bold magenta]{name}  ({device['ip']})[/bold magenta]")
        console.print("""
[bold cyan]1)[/bold cyan] Show status
[bold cyan]2)[/bold cyan] Toggle on/off
[bold cyan]3)[/bold cyan] Set brightness
[bold cyan]4)[/bold cyan] Set color (RGB)
[bold cyan]5)[/bold cyan] Set effect
[bold cyan]6)[/bold cyan] Set palette
[bold cyan]7)[/bold cyan] Set speed / intensity
[bold cyan]8)[/bold cyan] Nightlight mode
[bold cyan]9)[/bold cyan] Apply preset
[bold cyan]10)[/bold cyan] Raw JSON POST (advanced)
[bold cyan]11)[/bold cyan] Reboot device
[bold cyan]0)[/bold cyan] ← Back
""")
        choice = Prompt.ask("Choose", choices=[str(i) for i in range(12)], default="1")

        try:
            if choice == "0":
                break

            elif choice == "1":
                show_device_status(api, name)

            elif choice == "2":
                result = api.toggle()
                state = "[green]ON[/green]" if result.get("on") else "[red]OFF[/red]"
                console.print(f"Toggled → {state}  bri={result.get('bri')}")

            elif choice == "3":
                bri = IntPrompt.ask("Brightness (0-255)", default=128)
                result = api.set_brightness(bri)
                console.print(f"[green]Brightness set → {result.get('bri')}[/green]")

            elif choice == "4":
                r = IntPrompt.ask("Red   (0-255)", default=255)
                g = IntPrompt.ask("Green (0-255)", default=0)
                b = IntPrompt.ask("Blue  (0-255)", default=0)
                seg = IntPrompt.ask("Segment ID", default=0)
                api.set_color(r, g, b, seg)
                console.print(f"[green]Color set → rgb({r},{g},{b}) on segment {seg}[/green]")

            elif choice == "5":
                effects = api.get_effects()
                fx_id = pick_from_list(effects, "Effects")
                if fx_id is not None:
                    seg = IntPrompt.ask("Segment ID", default=0)
                    api.set_effect(fx_id, seg)
                    console.print(f"[green]Effect set → {effects[fx_id]} (id={fx_id})[/green]")

            elif choice == "6":
                palettes = api.get_palettes()
                pal_id = pick_from_list(palettes, "Palettes")
                if pal_id is not None:
                    seg = IntPrompt.ask("Segment ID", default=0)
                    api.set_palette(pal_id, seg)
                    console.print(f"[green]Palette set → {palettes[pal_id]} (id={pal_id})[/green]")

            elif choice == "7":
                seg = IntPrompt.ask("Segment ID", default=0)
                speed = IntPrompt.ask("Speed (0-255)", default=128)
                intensity = IntPrompt.ask("Intensity (0-255)", default=128)
                api.set_speed(speed, seg)
                api.set_intensity(intensity, seg)
                console.print(f"[green]Speed={speed}, Intensity={intensity} on segment {seg}[/green]")

            elif choice == "8":
                on = Confirm.ask("Enable nightlight?", default=True)
                dur = IntPrompt.ask("Duration (minutes)", default=60)
                mode = IntPrompt.ask("Mode (0=instant 1=fade 2=color fade 3=sunrise)", default=1)
                api.nightlight(on, dur, mode)
                console.print(f"[green]Nightlight {'enabled' if on else 'disabled'}[/green]")

            elif choice == "9":
                ps = IntPrompt.ask("Preset ID", default=1)
                api.set_preset(ps)
                console.print(f"[green]Preset {ps} applied[/green]")

            elif choice == "10":
                console.print("[dim]Enter raw JSON payload (single line):[/dim]")
                raw = Prompt.ask("JSON")
                payload = json.loads(raw)
                result = api.set_state(payload)
                console.print_json(json.dumps(result))

            elif choice == "11":
                if Confirm.ask("[red]Reboot the device?[/red]", default=False):
                    api.reboot()
                    console.print("[yellow]Reboot command sent.[/yellow]")
                    break

        except requests.RequestException as e:
            console.print(f"[red]Network error: {e}[/red]")
        except json.JSONDecodeError as e:
            console.print(f"[red]Invalid JSON: {e}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        console.print()
        time.sleep(0.3)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main() -> None:
    console.print(Panel.fit(
        "[bold magenta]WLED Network Controller[/bold magenta]\n"
        "[dim]mDNS discovery + JSON API[/dim]",
        border_style="magenta",
    ))

    while True:
        # ── Discovery ──────────────────────────
        devices = discover_wled_devices(timeout=5.0)

        if not devices:
            console.print("[yellow]No WLED devices found via mDNS.[/yellow]")
            manual = Confirm.ask("Connect manually by IP?", default=True)
            if manual:
                ip = Prompt.ask("IP address")
                try:
                    ipaddress.ip_address(ip)
                except ValueError:
                    console.print("[red]Invalid IP address.[/red]")
                    continue
                port = IntPrompt.ask("Port", default=80)
                devices = [{"name": ip, "hostname": ip, "ip": ip, "port": port, "props": {}}]
            else:
                if not Confirm.ask("Scan again?", default=True):
                    break
                continue

        show_device_list(devices)

        if len(devices) == 1:
            selected = devices[0]
            console.print(f"[dim]Auto-selecting the only device: {selected['name']}[/dim]")
        else:
            idx = IntPrompt.ask(
                f"Select device (1-{len(devices)}, 0 to rescan)",
                default=1,
            )
            if idx == 0:
                continue
            if not 1 <= idx <= len(devices):
                console.print("[red]Invalid selection.[/red]")
                continue
            selected = devices[idx - 1]

        device_menu(selected)

        if not Confirm.ask("\nReturn to device list?", default=True):
            break

    console.print("[bold green]Goodbye![/bold green]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]Interrupted.[/bold red]")
        sys.exit(0)