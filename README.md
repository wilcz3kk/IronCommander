# IronCommander — Hearts of Iron IV Tactical Memory Suite

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20x64-0078D6.svg)](#)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB.svg)](#)
[![Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Pure%20Win32%20API)-success.svg)](#)
[![Target](https://img.shields.io/badge/Target-Clausewitz%20Engine%20(hoi4.exe)-critical.svg)](#)

**IronCommander** is a lightweight, open-source single-player tactical deck, real-time memory inspector, and trainer suite for **Hearts of Iron IV** (`hoi4.exe`, Paradox Clausewitz Engine, x86_64 PE32+).

Designed for modders, scenario testers, and reverse engineering enthusiasts, it provides an intuitive dark-themed dashboard, live ASLR dynamic pointer resolution, 14 non-invasive in-engine tactical toggles, and zero-dependency runtime.

---

## ✨ Features

* **⚡ Zero External Dependencies:** Written in 100% pure standard Python (`ctypes`, `tkinter`, `struct`) interacting directly with the native Windows kernel (`kernel32.dll`, `user32.dll`). No pip packages required.
* **🖥️ Modern Tactical Dashboard (GUI):** Sleek dark interface with animated pill-switches, live telemetry monitor, and global hotkeys (`F1`–`F11`).
* **🎯 Non-Invasive `.data` Toggles:** Modifies native single-byte engine flags in the `.data` section instead of patching executable code (`.text`). Perfectly stable, crash-free, and reversible at runtime.
* **🔍 Dynamic Pointer Resolution:** Automatically tracks ASLR base offsets, reads the singleton `CGameState*`, and resolves the active player's `CCountry*` instance and country tag in real time.
* **🔒 Resource Freezing (Unlimited Mode):** High-frequency thread locks resources and XP at specified values.
* **🛡️ Ironman Console Unlocker:** One-click toggle of the internal Ironman flag (`[CGameState + 0xC0]`), allowing the developer debug console (`~`) to be used without restarting or altering save files.
* **📦 Native Windows Launcher:** Includes `HOI4_Commander.exe` (with embedded high-res icon) and open C# source code (`Launcher.cs`) that compiles using Windows' built-in `csc.exe`.
* **📑 Cheat Engine Table:** Includes `hoi4_political_power.CT` with pointer structures and toggle addresses.

---

## 🗂️ Project Structure

| File | Description |
| :--- | :--- |
| **`HOI4_Commander.exe`** | Pre-compiled native Windows GUI launcher with embedded application icon. |
| **`hoi4_trainer.py`** | Main tactical GUI application (Dark Theme, live telemetry, hotkey daemon, stat lockers). |
| **`hoi4_pp_trainer.py`** | Standalone command-line interface (CLI) with AOB signature scanner and memory analyzer. |
| **`hoi4_political_power.CT`** | Cheat Engine 7.x table with pointer maps and `.data` flag list. |
| **`Launcher.cs`** | C# source code for the standalone executable launcher. |
| **`build_launcher.bat`** | One-click build script to compile `Launcher.cs` using Windows native `csc.exe`. |
| **`hoi4_trainer.ico`** | Application icon resource. |

---

## 🚀 Quick Start

### Option 1: Native Launcher (Easiest)
Simply double-click **`HOI4_Commander.exe`**. It will automatically locate your Python installation and launch the tactical deck.

### Option 2: Running via Python
Ensure Python 3.8+ is installed on your system. Run:
```bash
python hoi4_trainer.py
```

### Option 3: Command-Line (CLI) Mode
To inspect memory or test via terminal:
```bash
# Verify binary AOB signatures statically
python hoi4_trainer.py --verify-static

# Run CLI scanner
python hoi4_pp_trainer.py --help
```

---

## ⌨️ Tactical Toggles & Hotkeys

All toggles interact directly with Clausewitz engine flags in `.data`:

| Hotkey | Feature | Engine RVA | Description |
| :---: | :--- | :---: | :--- |
| **`F1`** | **Instant Construction** | `0x33DDA98` | Buildings, infrastructure, and factories finish in 1 day (`ic`). |
| **`F2`** | **Instant Army Training** | `0x33DDA9B` | Military divisions recruit and deploy instantly (`it`). |
| **`F3`** | **Instant Ship Refit** | `0x33DDA99` | Ship upgrading and drydock overhaul complete in 1 day. |
| **`F4`** | **Instant Research** | `0x33DDA86` | Any selected technology researches instantly on click. |
| **`F5`** | **Fast National Focus** | `0x33DDA9C` / `A0` / `A1` | Focuses complete in 1 day; bypasses prerequisite checks. |
| **`F6`** | **Fast Decisions** | `0x33DDAA2` / `A3` | Political decisions complete instantly with bypassed criteria. |
| **`F7`** | **Instant Agency Upgrades**| `0x33DDA93` .. `9F` | Agency branch upgrades and operative operations complete in 1 day. |
| **`F8`** | **Instant Unit Teleport** | `0x33DDA8B` | Divisions move instantly to target provinces upon order. |
| **`F9`** | **Diplomacy Freedom** | `0x33DDA87` / `89` | Enables `allowdiplo` and `yesman` (AI always accepts diplomatic proposals). |
| **`F10`** | **Instant War Goal & NoCB**| `0x33DDAB4` / `B5` | Fabricate war goals in 1 day; declare war without justification. |
| **`F11`** | **Disable Fog of War** | `0x33DDAAA` | Removes global fog of war (`fow off`). |
| — | **Zero Supply Penalties** | `0x312AC80` | Suppresses division attrition from low logistics or bad terrain. |
| — | **Ignore Naval Superiority**| `0x33DDAA4` | Enables naval invasions regardless of regional naval dominance. |
| — | **Bypass Focus Prereqs** | `0x33DDA9C` | Unlocks mutually exclusive or locked focus tree branches. |

---

## 🔬 Reverse Engineering & Memory Architecture

```
hoi4.exe (PE32+ Base Image = 0x140000000 + ASLR Slide)
  │
  ├── [hoi4.exe + 0x33DD6B0] ──────────────────────► CGameState* Singleton
  │                                                    ├── +0x0C0  Ironman Flag (Bit 0)
  │                                                    ├── +0x310  CCountry* Table Ptr
  │                                                    ├── +0x340  Tag-to-Index Array Ptr
  │                                                    ├── +0x358  3-Letter Tag String Array
  │                                                    └── +0x520  Active Player Tag ID (int32)
  │
  └── CCountry* (Active Player State)
        ├── +0x1F0   Command Power (CFixedPoint, scale: 100,000)
        ├── +0x340   Manpower (int32, scale: 1:1)
        ├── +0xF90   CPoliticalStatus* ──► +0xE0   Political Power (scale: 100,000)
        ├── +0x10D0  Stability (scale: 1,000 per 1%)
        ├── +0x10D8  War Support (scale: 1,000 per 1%)
        ├── +0x1310  CNuclearManager*   ──► +0x18   Nukes Count (scale: 100,000,000)
        ├── +0x1580  CFuelManager*      ──► +0x08   Current Fuel (Q49.15, scale: 32,768)
        └── +0x1588  CCountryXP*        ──► +0x10   Air XP (Q49.15, scale: 32,768)
                                        ──► +0x28   Navy XP (Q49.15, scale: 32,768)
                                        ──► +0x40   Army XP (Q49.15, scale: 32,768)
```

### Numeric Scaling Conventions:
* **`CFixedPoint` (`100,000` / `0x186A0`):** Used for Political Power and Command Power ($1.0 = 100\,000$).
* **`CFixedPoint64 Q49.15` (`32,768` / `1 << 15`):** Binary fixed-point used for Military Experience (Army, Navy, Air) and Fuel ($1.0 = 32\,768$).
* **Percentage Scale (`1,000` per `1%`):** Used for Stability and War Support ($100\% = 100\,000$).
* **Nuclear Scale (`100,000,000` per bomb):** Scaled integer representation.

---

## 🛠️ Building the Launcher

If you want to recompile the native `HOI4_Commander.exe` launcher yourself, simply run:
```cmd
build_launcher.bat
```
This uses the built-in Microsoft .NET Framework compiler (`csc.exe`) included with Windows. No external SDKs or Visual Studio required.

---

## ⚠️ Disclaimer

* This tool is strictly intended for **educational, scenario-modding, and single-player** entertainment purposes.
* Do **not** use this software in multiplayer matches or competitive gameplay.
* Hearts of Iron IV and Clausewitz Engine are registered trademarks of Paradox Interactive AB. This project is not affiliated with, authorized, endorsed, or sponsored by Paradox Interactive.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
