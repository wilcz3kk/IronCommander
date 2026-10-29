#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HOI4 IronCommander: Real-Time Tactical Deck & Reverse Engineering Suite
==============================================================================
Target Binary: Hearts of Iron IV (hoi4.exe, Clausewitz Engine, x86_64 PE32+)
Scope: Strictly Single-Player Campaign Educational Memory Inspector & Trainer.

Reverse-Engineered Memory Layout (Static ImageBase = 0x140000000):
  * CGameState* Singleton:         [hoi4.exe + 0x33DD6B0]
  * Player Country Tag ID (int32): [CGameState + 0x520] (fallback +0x524)
  * Tag-to-Index Table (int32[]):  *[CGameState + 0x340] + TagID * 4
  * CCountry* Array (ptr[]):       *[CGameState + 0x310] + CountryIndex * 8
  * Tag 3-Letter String:           *[CGameState + 0x358] + TagID * 32

Inside CCountry*:
  * Command Power (CFixedPoint):   [CCountry + 0x1F0]               (scale: 100,000)
  * Manpower (int32):              [CCountry + 0x340]               (scale: 1)
  * Political Power (CFixedPoint): *([CCountry + 0xF90]) + 0xE0     (scale: 100,000)
  * Base Stability (CFixedPoint):  [CCountry + 0x10D0]              (scale: 1,000 per 1%)
  * War Support (CFixedPoint):     [CCountry + 0x10D8]              (scale: 1,000 per 1%)
  * Nuclear Bombs (int64):         *([CCountry + 0x1310]) + 0x18    (scale: 100,000,000)
  * Current Fuel (Q49.15):         *([CCountry + 0x1580]) + 0x08    (scale: 32,768)
  * Max Fuel Capacity (Q49.15):    *([CCountry + 0x1580]) + 0x10    (scale: 32,768)
  * Air XP (Q49.15):               *([CCountry + 0x1588]) + 0x10    (scale: 32,768)
  * Navy XP (Q49.15):              *([CCountry + 0x1588]) + 0x28    (scale: 32,768)
  * Army XP (Q49.15):              *([CCountry + 0x1588]) + 0x40    (scale: 32,768)
"""

import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox

# ============================================================================
# 1. STAŁE REVERSE ENGINEERINGU (RVA, OFFSETY, SKALE FIXED-POINT, AOB)
# ============================================================================

DEFAULT_HOI4_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV\hoi4.exe"
STATIC_IMAGE_BASE = 0x140000000

# Skale stałoprzecinkowe silnika Clausewitz
SCALE_FIXED_POINT = 100_000          # 1.0 = 100,000 (0x186A0) -> PP, Command Power
SCALE_PERCENT_FP = 1_000             # 1% = 1,000 (100% = 100,000) -> Stability, War Support
SCALE_Q49_15 = 32_768                # 1.0 = 32,768 (1 << 15) -> Fuel, Army/Navy/Air XP
SCALE_NUKES = 100_000_000            # 1 Nuke = 1000 * 100,000 = 100,000,000

# Singleton CGameState* w sekcji .data
RVA_CGAMESTATE_PTR = 0x33DD6B0

# Offsety w CGameState
OFF_GAMESTATE_IRONMAN_FLAGS = 0xC0   # uint32 (bit 0 = Ironman enabled)
OFF_GAMESTATE_COUNTRIES_ARR = 0x310
OFF_GAMESTATE_TAG_TO_INDEX = 0x340
OFF_GAMESTATE_TAG_NAMES_ARR = 0x358
OFF_GAMESTATE_PLAYER_TAG = 0x520
OFF_GAMESTATE_PLAYER_TAG_ALT = 0x524

# Offsety w CCountry i podstrukturach
OFF_COUNTRY_COMMAND_POWER = 0x1F0    # int64 (scale 100,000)
OFF_COUNTRY_MANPOWER = 0x340         # int32 (1:1, wewnątrz sub-struktury +0x328 + 0x18)
OFF_COUNTRY_POLITICAL_PTR = 0xF90    # CPoliticalStatus*
OFF_POLITICAL_POWER_VAL = 0xE0       # int64 (scale 100,000)
OFF_COUNTRY_STABILITY = 0x10D0       # int64 (scale 1,000 na 1%)
OFF_COUNTRY_WAR_SUPPORT = 0x10D8     # int64 (scale 1,000 na 1%)
OFF_COUNTRY_NUKES_PTR = 0x1310       # CNuclearManager*
OFF_NUKES_COUNT_VAL = 0x18           # int64 (scale 100,000,000)
OFF_COUNTRY_FUEL_PTR = 0x1580        # CFuelManager*
OFF_FUEL_CURRENT_VAL = 0x08          # int64 (scale 32,768)
OFF_FUEL_MAX_VAL = 0x10              # int64 (scale 32,768)
OFF_COUNTRY_XP_PTR = 0x1588          # CCountryExperience*
OFF_XP_AIR_VAL = 0x10                # int64 (scale 32,768)
OFF_XP_NAVY_VAL = 0x28               # int64 (scale 32,768)
OFF_XP_ARMY_VAL = 0x40               # int64 (scale 32,768)

# Globalne flagi przełączników gry w sekcji .data
# Format: (id_klucza, nazwa_pl, opis_mechanizmu, lista_ (RVA, wartość_ON, wartość_OFF), klawisz_vk, nazwa_klawisza)
GAME_TOGGLES = [
    (
        "instant_construction",
        "Natychmiastowa Budowa (Instant Construction)",
        "Flaga .data [hoi4.exe+0x33DDA98] = 1 (odpowiednik 'ic' — budynki kończą się w 1 dzień)",
        [(0x33DDA98, 1, 0)],
        0x70, "F1",
    ),
    (
        "instant_training",
        "Natychmiastowe Szkolenie Armii (Fast Recruiting)",
        "Flaga .data [hoi4.exe+0x33DDA9B] = 1 (odpowiednik 'it' — dywizje szkolą się natychmiast)",
        [(0x33DDA9B, 1, 0)],
        0x71, "F2",
    ),
    (
        "instant_ship_refit",
        "Natychmiastowa Modernizacja Okrętów (Ship Refit)",
        "Flaga .data [hoi4.exe+0x33DDA99] = 1 (przebudowa okrętów w stoczniach trwa 1 dzień)",
        [(0x33DDA99, 1, 0)],
        0x72, "F3",
    ),
    (
        "instant_research",
        "Natychmiastowe Badania po Kliknięciu (Fast Research)",
        "Flaga .data [hoi4.exe+0x33DDA86] = 1 (kliknięcie ikony w drzewku technologii bada ją od razu)",
        [(0x33DDA86, 1, 0)],
        0x73, "F4",
    ),
    (
        "fast_focus",
        "Natychmiastowe Cele Narodowe (Fast National Focus)",
        "Flagi .data [0x33DDA9C, 0x33DDAA0, 0x33DDAA1] = 1 (AutoComplete + NoChecks + IgnorePrerequisites)",
        [(0x33DDA9C, 1, 0), (0x33DDAA0, 1, 0), (0x33DDAA1, 1, 0)],
        0x74, "F5",
    ),
    (
        "fast_decisions",
        "Decyzje bez Wymagań i Czasu (Fast Decisions)",
        "Flagi .data [0x33DDAA2, 0x33DDAA3] = 1 (Decision.NoChecks + Decision.FastRemove)",
        [(0x33DDAA2, 1, 0), (0x33DDAA3, 1, 0)],
        0x75, "F6",
    ),
    (
        "instant_agency",
        "Natychmiastowy Wywiad i Operacje (Instant Agency)",
        "Flagi .data [0x33DDA93..9F, 0x33DDAB9, 0x33DDABD] = 1 (agencja, siatka, operacje i brak wykrycia)",
        [
            (0x33DDA93, 1, 0), (0x33DDA94, 1, 0), (0x33DDA9A, 1, 0),
            (0x33DDA9D, 1, 0), (0x33DDA9F, 1, 0), (0x33DDAB9, 1, 0), (0x33DDABD, 1, 0),
        ],
        0x76, "F7",
    ),
    (
        "instant_movement",
        "Natychmiastowy Ruch / Teleportacja Jednostek",
        "Flaga .data [hoi4.exe+0x33DDA8B] = 1 (odpowiednik 'tp' — prawy przycisk myszy teleportuje dywizje)",
        [(0x33DDA8B, 1, 0)],
        0x77, "F8",
    ),
    (
        "allow_diplo_yesman",
        "Pełna Dyplomacja + AI Zawsze Akceptuje (Yesman)",
        "Flagi .data [0x33DDA87 (allowdiplo), 0x33DDA89 (yesman)] = 1",
        [(0x33DDA87, 1, 0), (0x33DDA89, 1, 0)],
        0x78, "F9",
    ),
    (
        "instant_wargoal_nocb",
        "Natychmiastowy Cel Wojenny + Wojna bez CB (NoCB)",
        "Flagi .data [0x33DDAB4 (instant_wargoal), 0x33DDAB5 (nocb)] = 1",
        [(0x33DDAB4, 1, 0), (0x33DDAB5, 1, 0)],
        0x79, "F10",
    ),
    (
        "allow_ideas_traits",
        "Odblokuj Wszystkie Idee Rządu i Cechy Dowódców",
        "Flagi .data [0x33DDAB8 (allowideas), 0x33DDA88 (allowtraits)] = 1",
        [(0x33DDAB8, 1, 0), (0x33DDA88, 1, 0)],
        0, "—",
    ),
    (
        "no_supply_penalties",
        "Brak Kar za Zaopatrzenie (No Supply Penalties)",
        "Flaga .data [hoi4.exe+0x312AC80] = 0 (wartość 0 wyłącza kary za przepływ zaopatrzenia)",
        [(0x312AC80, 0, 1)],
        0, "—",
    ),
    (
        "ignore_naval_superiority",
        "Inwazje Morskie bez Dominacji Morskiej",
        "Flaga .data [hoi4.exe+0x33DDAA4] = 0 (wartość 0 wyłącza sprawdzanie dominacji morskiej)",
        [(0x33DDAA4, 0, 1)],
        0, "—",
    ),
    (
        "disable_fow",
        "Wyłącz Mgłę Wojny (Disable Fog of War)",
        "Flaga .data [hoi4.exe+0x33DDAAA] = 0 (wartość 0 odkrywa całą mapę i jednostki)",
        [(0x33DDAAA, 0, 1)],
        0x7A, "F11",
    ),
]

# Sygnatury AOB do weryfikacji statycznej i odporności na drobne patche
AOB_SIGNATURES = {
    "PoliticalPower_AddPP": (
        0xBFA723,
        "48 8B 97 E0 00 00 00 48 03 D3 48 8B 5C 24 40 48 89 97 E0 00 00 00 48 8B 0D ?? ?? ?? ?? 48 8B 05 ?? ?? ?? ??",
    ),
    "PoliticalPower_ChangePP": (
        0xBFA813,
        "48 8B 87 E0 00 00 00 48 8D 0C 18 48 8B 5C 24 40 48 85 C0 78 09 48 85 C9 79 0E 33 C9 EB 19 48 3B C8 7D 05 48 8B C8 EB 0F 48 3B 0D ?? ?? ?? ?? 48 0F 4F 0D ?? ?? ?? ?? 48 89 8F E0 00 00 00",
    ),
    "Stability_Writer": (
        0x719ED0,
        "4C 8B 81 D0 10 00 00 4C 03 C2 4C 89 81 D0 10 00 00 48 8B 15 ?? ?? ?? ??",
    ),
    "WarSupport_Writer": (
        0x71B040,
        "4C 8B 81 D8 10 00 00 4C 03 C2 4C 89 81 D8 10 00 00 48 8B 15 ?? ?? ?? ??",
    ),
    "CommandPower_Writer": (
        0x71794F,
        "48 8B 02 48 8B F1 48 01 81 F0 01 00 00 48 8D 54 24 30 48 81 C1 B8 05 00 00",
    ),
}

AOB_PLAYER_RESOLVER = (
    "48 8B 05 ?? ?? ?? ?? 83 B8 20 05 00 00 00 B9 24 05 00 00 BA 20 05 00 00 0F 4F CA 48 03 C8"
)


# ============================================================================
# 2. POMOCNICY AOB I WERYFIKACJA STATYCZNA PLIKU PE
# ============================================================================

def parse_aob(pattern_str: str):
    tokens = pattern_str.strip().split()
    pattern = bytearray()
    mask = bytearray()
    for tok in tokens:
        if tok in ("?", "??"):
            pattern.append(0)
            mask.append(0)
        else:
            pattern.append(int(tok, 16))
            mask.append(1)
    return bytes(pattern), bytes(mask)


def scan_buffer_aob(buf: bytes, pattern_str: str) -> list:
    pat, mask = parse_aob(pattern_str)
    plen = len(pat)
    anchors = [(i, pat[i]) for i in range(plen) if mask[i] == 1]
    if not anchors:
        return []
    first_idx, first_byte = anchors[0]
    other_anchors = anchors[1:]
    matches = []
    start = 0
    limit = len(buf) - plen
    while start <= limit:
        pos = buf.find(first_byte, start + first_idx)
        if pos == -1:
            break
        cand = pos - first_idx
        if cand > limit:
            break
        if all(buf[cand + idx] == val for idx, val in other_anchors):
            matches.append(cand)
        start = cand + 1
    return matches


def verify_static_binary(exe_path: str = DEFAULT_HOI4_PATH) -> bool:
    print("=" * 76)
    print("[*] WERYFIKACJA STATYCZNA IRONCOMMANDER NA PLIKU HOI4.EXE")
    print("=" * 76)
    if not os.path.isfile(exe_path):
        print(f"[!] Nie znaleziono pliku: {exe_path}")
        return False

    with open(exe_path, "rb") as f:
        data = f.read()

    print(f"[+] Wczytano binarkę: {exe_path} ({len(data):,} bajtów)")
    all_ok = True
    for name, (expected_rva, aob) in AOB_SIGNATURES.items():
        matches = scan_buffer_aob(data, aob)
        if len(matches) == 1:
            rva = matches[0] - 0x400 + 0x1000
            status = "OK" if rva == expected_rva else f"RVA=0x{rva:X}"
            print(f"[+] Sygnatura AOB {name:24s}: UNIKALNA (RVA 0x{rva:X}, {status})")
        else:
            print(f"[!] Sygnatura AOB {name:24s}: {len(matches)} trafień!")
            all_ok = False

    # Sprawdź relację RIP-relative dla CGameState*
    m = scan_buffer_aob(data, AOB_PLAYER_RESOLVER)
    if m:
        foff = m[0]
        rva = foff - 0x400 + 0x1000
        disp = struct.unpack_from("<i", data, foff + 3)[0]
        target_rva = rva + 7 + disp
        print(f"[+] Rozwiązany RVA CGameState* ({len(m)} miejsc): 0x{target_rva:X} (oczekiwano 0x{RVA_CGAMESTATE_PTR:X})")
        if target_rva != RVA_CGAMESTATE_PTR:
            all_ok = False

    print(f"[+] Liczba zmapowanych przełączników taktycznych (.data): {len(GAME_TOGGLES)}")
    print("=" * 76)
    return all_ok


# ============================================================================
# 3. SILNIK PAMIĘCI WIN32 API (CTYPES)
# ============================================================================

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", wt.WCHAR * 256),
        ("szExePath", wt.WCHAR * 260),
    ]


class HOI4MemoryEngine:
    """Niskopoziomowy interfejs Win32 API do odczytu i modyfikacji pamięci hoi4.exe."""

    def __init__(self):
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u32 = ctypes.WinDLL("user32", use_last_error=True)
        self._setup_apis()
        self.pid = None
        self.h_proc = None
        self.base_addr = None
        self.mod_size = None
        self.cgamestate_rva = RVA_CGAMESTATE_PTR

    def _setup_apis(self):
        self.k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        self.k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        self.k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        self.k32.Process32FirstW.restype = wt.BOOL
        self.k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        self.k32.Process32NextW.restype = wt.BOOL
        self.k32.Module32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        self.k32.Module32FirstW.restype = wt.BOOL
        self.k32.Module32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        self.k32.Module32NextW.restype = wt.BOOL
        self.k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.k32.OpenProcess.restype = wt.HANDLE
        self.k32.ReadProcessMemory.argtypes = [
            wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
        ]
        self.k32.ReadProcessMemory.restype = wt.BOOL
        self.k32.WriteProcessMemory.argtypes = [
            wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
        ]
        self.k32.WriteProcessMemory.restype = wt.BOOL
        self.k32.CloseHandle.argtypes = [wt.HANDLE]
        self.k32.CloseHandle.restype = wt.BOOL
        self.u32.GetAsyncKeyState.argtypes = [ ctypes.c_int ]
        self.u32.GetAsyncKeyState.restype = ctypes.c_short

    def is_key_pressed(self, vk_code: int) -> bool:
        if not vk_code:
            return False
        return (self.u32.GetAsyncKeyState(vk_code) & 0x8000) != 0

    def attach(self) -> bool:
        """Szuka procesu hoi4.exe i podpina uchwyt pamięci."""
        if self.h_proc:
            # Sprawdź czy proces nadal żyje poprzez odczyt nagłówka MZ
            mz = self.read_bytes(self.base_addr, 2)
            if mz == b"MZ":
                return True
            self.detach()

        snap = self.k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == wt.HANDLE(-1).value:
            return False
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found_pid = None
        try:
            if self.k32.Process32FirstW(snap, ctypes.byref(pe)):
                while True:
                    if pe.szExeFile.lower() == "hoi4.exe":
                        found_pid = pe.th32ProcessID
                        break
                    if not self.k32.Process32NextW(snap, ctypes.byref(pe)):
                        break
        finally:
            self.k32.CloseHandle(snap)

        if not found_pid:
            return False

        msnap = self.k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, found_pid)
        if msnap == wt.HANDLE(-1).value:
            return False
        me = MODULEENTRY32W()
        me.dwSize = ctypes.sizeof(MODULEENTRY32W)
        base_addr, mod_size = None, None
        try:
            if self.k32.Module32FirstW(msnap, ctypes.byref(me)):
                while True:
                    if me.szModule.lower() == "hoi4.exe":
                        base_addr = int(me.modBaseAddr)
                        mod_size = int(me.modBaseSize)
                        break
                    if not self.k32.Module32NextW(msnap, ctypes.byref(me)):
                        break
        finally:
            self.k32.CloseHandle(msnap)

        if not base_addr:
            return False

        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE
        h = self.k32.OpenProcess(access, False, found_pid)
        if not h:
            return False

        self.pid = found_pid
        self.h_proc = h
        self.base_addr = base_addr
        self.mod_size = mod_size
        return True

    def detach(self):
        if self.h_proc:
            self.k32.CloseHandle(self.h_proc)
        self.h_proc = None
        self.pid = None
        self.base_addr = None
        self.mod_size = None

    def read_bytes(self, addr: int, size: int):
        if not self.h_proc or not addr or addr < 0x10000:
            return None
        buf = ctypes.create_string_buffer(size)
        nread = ctypes.c_size_t(0)
        ok = self.k32.ReadProcessMemory(self.h_proc, ctypes.c_void_p(addr), buf, size, ctypes.byref(nread))
        if not ok or nread.value != size:
            return None
        return buf.raw

    def write_bytes(self, addr: int, data: bytes) -> bool:
        if not self.h_proc or not addr or addr < 0x10000:
            return False
        buf = ctypes.create_string_buffer(data, len(data))
        nwritten = ctypes.c_size_t(0)
        ok = self.k32.WriteProcessMemory(self.h_proc, ctypes.c_void_p(addr), buf, len(data), ctypes.byref(nwritten))
        return bool(ok and nwritten.value == len(data))

    def read_u8(self, addr: int):
        b = self.read_bytes(addr, 1)
        return struct.unpack("<B", b)[0] if b else None

    def write_u8(self, addr: int, val: int) -> bool:
        return self.write_bytes(addr, struct.pack("<B", val & 0xFF))

    def read_i32(self, addr: int):
        b = self.read_bytes(addr, 4)
        return struct.unpack("<i", b)[0] if b else None

    def write_i32(self, addr: int, val: int) -> bool:
        val = max(-2147483648, min(2147483647, int(val)))
        return self.write_bytes(addr, struct.pack("<i", val))

    def read_u32(self, addr: int):
        b = self.read_bytes(addr, 4)
        return struct.unpack("<I", b)[0] if b else None

    def write_u32(self, addr: int, val: int) -> bool:
        return self.write_bytes(addr, struct.pack("<I", int(val) & 0xFFFFFFFF))

    def read_i64(self, addr: int):
        b = self.read_bytes(addr, 8)
        return struct.unpack("<q", b)[0] if b else None

    def write_i64(self, addr: int, val: int) -> bool:
        return self.write_bytes(addr, struct.pack("<q", int(val)))

    def read_ptr(self, addr: int):
        b = self.read_bytes(addr, 8)
        return struct.unpack("<Q", b)[0] if b else None

    def read_std_string(self, addr: int) -> str:
        raw = self.read_bytes(addr, 32)
        if not raw:
            return "???"
        size = struct.unpack_from("<Q", raw, 0x10)[0]
        cap = struct.unpack_from("<Q", raw, 0x18)[0]
        if size == 0 or size > 32:
            return "???"
        if cap <= 15:
            return raw[:size].decode("ascii", errors="replace")
        str_ptr = struct.unpack_from("<Q", raw, 0x00)[0]
        sbytes = self.read_bytes(str_ptr, size)
        return sbytes.decode("ascii", errors="replace") if sbytes else "???"

    def resolve_player_country(self) -> dict:
        """Rozwiązuje pełny łańcuch wskaźników CGameState -> CCountry gracza."""
        if not self.h_proc or not self.base_addr:
            return {"ok": False, "reason": "Proces hoi4.exe nie jest uruchomiony"}

        gs_ptr = self.read_ptr(self.base_addr + self.cgamestate_rva)
        if not gs_ptr:
            return {"ok": False, "reason": "CGameState* == NULL (gra się ładuje)"}

        tag_id = self.read_i32(gs_ptr + OFF_GAMESTATE_PLAYER_TAG)
        if tag_id is None or tag_id <= 0:
            tag_id = self.read_i32(gs_ptr + OFF_GAMESTATE_PLAYER_TAG_ALT)
        if tag_id is None or tag_id <= 0:
            return {"ok": False, "reason": "Brak wybranego państwa (wejdź do kampanii SP)", "gs_ptr": gs_ptr}

        tag_to_idx = self.read_ptr(gs_ptr + OFF_GAMESTATE_TAG_TO_INDEX)
        if not tag_to_idx:
            return {"ok": False, "reason": "Brak tablicy Tag->Index"}

        c_idx = self.read_i32(tag_to_idx + tag_id * 4)
        if c_idx is None or c_idx < 0 or c_idx > 2000:
            return {"ok": False, "reason": f"Nieprawidłowy CountryIndex={c_idx}"}

        countries_arr = self.read_ptr(gs_ptr + OFF_GAMESTATE_COUNTRIES_ARR)
        if not countries_arr:
            return {"ok": False, "reason": "Brak tablicy CCountry*"}

        ccountry = self.read_ptr(countries_arr + c_idx * 8)
        if not ccountry:
            return {"ok": False, "reason": "CCountry* == NULL"}

        tag_names_arr = self.read_ptr(gs_ptr + OFF_GAMESTATE_TAG_NAMES_ARR)
        tag_str = self.read_std_string(tag_names_arr + tag_id * 32) if tag_names_arr else "???"

        # Pod-struktury w CCountry
        pol_ptr = self.read_ptr(ccountry + OFF_COUNTRY_POLITICAL_PTR)
        nukes_ptr = self.read_ptr(ccountry + OFF_COUNTRY_NUKES_PTR)
        fuel_ptr = self.read_ptr(ccountry + OFF_COUNTRY_FUEL_PTR)
        xp_ptr = self.read_ptr(ccountry + OFF_COUNTRY_XP_PTR)
        ironman_flags = self.read_u32(gs_ptr + OFF_GAMESTATE_IRONMAN_FLAGS)

        return {
            "ok": True,
            "gs_ptr": gs_ptr,
            "tag_id": tag_id,
            "country_idx": c_idx,
            "tag_str": tag_str,
            "ccountry": ccountry,
            "pol_ptr": pol_ptr,
            "nukes_ptr": nukes_ptr,
            "fuel_ptr": fuel_ptr,
            "xp_ptr": xp_ptr,
            "ironman_on": bool(ironman_flags & 1) if ironman_flags is not None else False,
        }

    # --- Odczyt i zapis poszczególnych zasobów państwa ---

    def read_all_stats(self, ctx: dict) -> dict:
        if not ctx.get("ok"):
            return {}
        cc = ctx["ccountry"]
        pol = ctx.get("pol_ptr")
        nukes = ctx.get("nukes_ptr")
        fuel = ctx.get("fuel_ptr")
        xp = ctx.get("xp_ptr")

        pp_raw = self.read_i64(pol + OFF_POLITICAL_POWER_VAL) if pol else None
        cp_raw = self.read_i64(cc + OFF_COUNTRY_COMMAND_POWER)
        stab_raw = self.read_i64(cc + OFF_COUNTRY_STABILITY)
        ws_raw = self.read_i64(cc + OFF_COUNTRY_WAR_SUPPORT)
        mp_raw = self.read_i32(cc + OFF_COUNTRY_MANPOWER)
        nukes_raw = self.read_i64(nukes + OFF_NUKES_COUNT_VAL) if nukes else None
        fuel_cur = self.read_i64(fuel + OFF_FUEL_CURRENT_VAL) if fuel else None
        fuel_max = self.read_i64(fuel + OFF_FUEL_MAX_VAL) if fuel else None
        xp_army = self.read_i64(xp + OFF_XP_ARMY_VAL) if xp else None
        xp_navy = self.read_i64(xp + OFF_XP_NAVY_VAL) if xp else None
        xp_air = self.read_i64(xp + OFF_XP_AIR_VAL) if xp else None

        return {
            "pp": (pp_raw / SCALE_FIXED_POINT) if pp_raw is not None else 0.0,
            "cp": (cp_raw / SCALE_FIXED_POINT) if cp_raw is not None else 0.0,
            "stability": (stab_raw / SCALE_PERCENT_FP) if stab_raw is not None else 0.0,
            "war_support": (ws_raw / SCALE_PERCENT_FP) if ws_raw is not None else 0.0,
            "manpower": mp_raw if mp_raw is not None else 0,
            "nukes": (nukes_raw // SCALE_NUKES) if nukes_raw is not None else 0,
            "fuel": (fuel_cur / SCALE_Q49_15) if fuel_cur is not None else 0.0,
            "fuel_max": (fuel_max / SCALE_Q49_15) if fuel_max is not None else 0.0,
            "xp_army": (xp_army / SCALE_Q49_15) if xp_army is not None else 0.0,
            "xp_navy": (xp_navy / SCALE_Q49_15) if xp_navy is not None else 0.0,
            "xp_air": (xp_air / SCALE_Q49_15) if xp_air is not None else 0.0,
        }

    def set_stat(self, ctx: dict, key: str, value: float) -> bool:
        if not ctx.get("ok"):
            return False
        cc = ctx["ccountry"]
        if key == "pp" and ctx.get("pol_ptr"):
            return self.write_i64(ctx["pol_ptr"] + OFF_POLITICAL_POWER_VAL, int(round(value * SCALE_FIXED_POINT)))
        elif key == "cp":
            return self.write_i64(cc + OFF_COUNTRY_COMMAND_POWER, int(round(value * SCALE_FIXED_POINT)))
        elif key == "stability":
            return self.write_i64(cc + OFF_COUNTRY_STABILITY, int(round(value * SCALE_PERCENT_FP)))
        elif key == "war_support":
            return self.write_i64(cc + OFF_COUNTRY_WAR_SUPPORT, int(round(value * SCALE_PERCENT_FP)))
        elif key == "manpower":
            return self.write_i32(cc + OFF_COUNTRY_MANPOWER, int(round(value)))
        elif key == "nukes" and ctx.get("nukes_ptr"):
            return self.write_i64(ctx["nukes_ptr"] + OFF_NUKES_COUNT_VAL, int(round(value)) * SCALE_NUKES)
        elif key == "fuel" and ctx.get("fuel_ptr"):
            if value < 0:
                # Specjalny tryb: wypełnij do 100% aktualnej pojemności maksymalnej
                max_f = self.read_i64(ctx["fuel_ptr"] + OFF_FUEL_MAX_VAL)
                if max_f and max_f > 0:
                    return self.write_i64(ctx["fuel_ptr"] + OFF_FUEL_CURRENT_VAL, max_f)
                value = 500_000.0
            return self.write_i64(ctx["fuel_ptr"] + OFF_FUEL_CURRENT_VAL, int(round(value * SCALE_Q49_15)))
        elif key == "xp_army" and ctx.get("xp_ptr"):
            return self.write_i64(ctx["xp_ptr"] + OFF_XP_ARMY_VAL, int(round(value * SCALE_Q49_15)))
        elif key == "xp_navy" and ctx.get("xp_ptr"):
            return self.write_i64(ctx["xp_ptr"] + OFF_XP_NAVY_VAL, int(round(value * SCALE_Q49_15)))
        elif key == "xp_air" and ctx.get("xp_ptr"):
            return self.write_i64(ctx["xp_ptr"] + OFF_XP_AIR_VAL, int(round(value * SCALE_Q49_15)))
        return False

    def read_toggle_state(self, rva_list: list) -> bool:
        if not self.h_proc or not self.base_addr or not rva_list:
            return False
        first_rva, val_on, _ = rva_list[0]
        cur = self.read_u8(self.base_addr + first_rva)
        return cur == val_on

    def write_toggle_state(self, rva_list: list, enable: bool) -> bool:
        if not self.h_proc or not self.base_addr:
            return False
        ok = True
        for rva, val_on, val_off in rva_list:
            target_byte = val_on if enable else val_off
            if not self.write_u8(self.base_addr + rva, target_byte):
                ok = False
        return ok

    def set_ironman(self, ctx: dict, enable_ironman: bool) -> bool:
        if not ctx.get("gs_ptr"):
            return False
        addr = ctx["gs_ptr"] + OFF_GAMESTATE_IRONMAN_FLAGS
        flags = self.read_u32(addr)
        if flags is None:
            return False
        new_flags = (flags | 1) if enable_ironman else (flags & ~1)
        return self.write_u32(addr, new_flags)


# ============================================================================
# 4. NOWOCZESNY INTERFEJS GRAFICZNY GUI (DARK THEME TKINTER)
# ============================================================================

BG_MAIN = "#0f1117"
BG_CARD = "#181b24"
BG_CARD_HOVER = "#1f2430"
BG_INPUT = "#10131a"
FG_PRIMARY = "#f0f4f8"
FG_SECONDARY = "#8b95a5"
ACCENT_BLUE = "#3b82f6"
ACCENT_GREEN = "#10b981"
ACCENT_RED = "#ef4444"
ACCENT_AMBER = "#f59e0b"
BORDER_COLOR = "#262b38"


class PillToggleSwitch(tk.Canvas):
    """Nowoczesny animowany przełącznik dwustanowy (pill switch)."""

    def __init__(self, parent, command=None, initial=False, **kwargs):
        super().__init__(
            parent, width=48, height=24, bg=BG_CARD, highlightthickness=0, cursor="hand2", **kwargs
        )
        self.state = initial
        self.command = command
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _draw(self):
        self.delete("all")
        bg_col = ACCENT_GREEN if self.state else "#2d3343"
        # Zaokrąglone tło (pill)
        self.create_oval(2, 2, 22, 22, fill=bg_col, outline=bg_col)
        self.create_oval(26, 2, 46, 22, fill=bg_col, outline=bg_col)
        self.create_rectangle(12, 2, 36, 22, fill=bg_col, outline=bg_col)
        # Kółko suwaka
        cx = 36 if self.state else 12
        self.create_oval(cx - 8, 4, cx + 8, 20, fill="#ffffff", outline="#ffffff")

    def _on_click(self, _event=None):
        self.state = not self.state
        self._draw()
        if self.command:
            self.command(self.state)

    def set_state(self, new_state: bool, notify: bool = False):
        if self.state != new_state:
            self.state = new_state
            self._draw()
            if notify and self.command:
                self.command(self.state)


class HOI4CommanderGUI:
    """Główne okno aplikacji HOI4 IronCommander & Tactical Inspector."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("HOI4 IronCommander — Tactical Memory Deck (Single-Player x64)")
        self.root.geometry("1040x760")
        self.root.minsize(960, 680)
        self.root.configure(bg=BG_MAIN)

        # Ustaw ikonkę okna jeśli istnieje w folderze
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hoi4_trainer.ico")
        if os.path.isfile(ico_path):
            try:
                self.root.iconbitmap(ico_path)
            except Exception:
                pass

        self.engine = HOI4MemoryEngine()
        self.ctx = {"ok": False, "reason": "Szukanie procesu hoi4.exe..."}
        self.locked_stats = {}  # {stat_key: (PillToggleSwitch, lock_value)}
        self.toggle_widgets = {}  # {toggle_id: (PillToggleSwitch, rva_list, title)}
        self.stat_value_labels = {}  # {stat_key: tk.Label}
        self.stat_entries = {}  # {stat_key: tk.Entry}
        self.last_hotkey_time = {}

        self._build_ui()
        self._schedule_loop()

    def _build_ui(self):
        # --- Pasek nagłówkowy (Header & Telemetry) ---
        header = tk.Frame(self.root, bg="#13161f", height=74, highlightbackground=BORDER_COLOR, highlightthickness=1)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        title_frame = tk.Frame(header, bg="#13161f")
        title_frame.pack(side="left", padx=20, pady=10)

        tk.Label(
            title_frame,
            text="HEARTS OF IRON IV",
            font=("Segoe UI", 15, "bold"),
            fg=FG_PRIMARY,
            bg="#13161f",
        ).pack(anchor="w")
        tk.Label(
            title_frame,
            text="IronCommander Suite  •  Clausewitz x64 Engine Inspector",
            font=("Segoe UI", 9),
            fg=ACCENT_BLUE,
            bg="#13161f",
        ).pack(anchor="w")

        # Status procesora / ASLR / Państwa po prawej
        telemetry = tk.Frame(header, bg="#13161f")
        telemetry.pack(side="right", padx=20, pady=10)

        self.lbl_proc_status = tk.Label(
            telemetry,
            text="● PROCES: SZUKANIE HOI4.EXE...",
            font=("Consolas", 10, "bold"),
            fg=ACCENT_AMBER,
            bg="#13161f",
        )
        self.lbl_proc_status.pack(anchor="e")

        self.lbl_country_status = tk.Label(
            telemetry,
            text="Państwo: ---  |  CGameState*: NULL  |  CCountry*: NULL",
            font=("Consolas", 9),
            fg=FG_SECONDARY,
            bg="#13161f",
        )
        self.lbl_country_status.pack(anchor="e", pady=(2, 0))

        # --- Pasek zakładek (Custom Tab Bar) ---
        tab_bar = tk.Frame(self.root, bg=BG_MAIN, pady=8, padx=20)
        tab_bar.pack(fill="x")

        self.active_tab = "stats"
        self.tab_buttons = {}
        self.tab_frames = {}

        tabs_spec = [
            ("stats", "⚡ Zasoby Państwa & Doświadczenie (Stats & XP)"),
            ("toggles", "🛠 Przełączniki Taktyczne: Budowa, Badania, Fokusy, Świat (14 Opcji)"),
            ("re_info", "🔍 Architektura Pamięci & Pointer Path (Edukacyjne RE)"),
        ]

        for tab_id, tab_title in tabs_spec:
            btn = tk.Button(
                tab_bar,
                text=tab_title,
                font=("Segoe UI", 10, "bold"),
                bg=ACCENT_BLUE if tab_id == "stats" else BG_CARD,
                fg=FG_PRIMARY,
                activebackground=ACCENT_BLUE,
                activeforeground=FG_PRIMARY,
                bd=0,
                padx=16,
                pady=7,
                cursor="hand2",
                command=lambda tid=tab_id: self._switch_tab(tid),
            )
            btn.pack(side="left", padx=(0, 8))
            self.tab_buttons[tab_id] = btn

        # Przycisk odblokowania Ironman na pasku zakładek po prawej
        self.btn_ironman = tk.Button(
            tab_bar,
            text="🔓 Odblokuj Ironman (SP)",
            font=("Segoe UI", 9, "bold"),
            bg="#262b38",
            fg=ACCENT_AMBER,
            activebackground="#32394a",
            activeforeground=FG_PRIMARY,
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
            command=self._toggle_ironman,
        )
        self.btn_ironman.pack(side="right")

        # Kontener na zawartość zakładek
        self.content_area = tk.Frame(self.root, bg=BG_MAIN)
        self.content_area.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        self.tab_frames["stats"] = tk.Frame(self.content_area, bg=BG_MAIN)
        self.tab_frames["toggles"] = tk.Frame(self.content_area, bg=BG_MAIN)
        self.tab_frames["re_info"] = tk.Frame(self.content_area, bg=BG_MAIN)

        self._build_stats_tab(self.tab_frames["stats"])
        self._build_toggles_tab(self.tab_frames["toggles"])
        self._build_re_info_tab(self.tab_frames["re_info"])

        self.tab_frames["stats"].pack(fill="both", expand=True)

        # --- Dolny pasek statusu (Footer) ---
        footer = tk.Frame(self.root, bg="#13161f", height=32, highlightbackground=BORDER_COLOR, highlightthickness=1)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        self.lbl_footer = tk.Label(
            footer,
            text="Gotowy. Skróty klawiszowe F1–F11 działają globalnie podczas gry w Hearts of Iron IV (Single-Player).",
            font=("Segoe UI", 9),
            fg=FG_SECONDARY,
            bg="#13161f",
        )
        self.lbl_footer.pack(side="left", padx=16)

    def _switch_tab(self, tab_id: str):
        for tid, frame in self.tab_frames.items():
            frame.pack_forget()
            self.tab_buttons[tid].configure(bg=ACCENT_BLUE if tid == tab_id else BG_CARD)
        self.tab_frames[tab_id].pack(fill="both", expand=True)
        self.active_tab = tab_id

    # ------------------------------------------------------------------------
    # ZAKŁADKA 1: ZASOBY PAŃSTWA I DOŚWIADCZENIE (STATS & XP)
    # ------------------------------------------------------------------------
    def _build_stats_tab(self, parent: tk.Frame):
        # Dwie kolumny: lewa = Zasoby Państwa, prawa = Doświadczenie Wojskowe + Szybkie Akcje
        left_col = tk.Frame(parent, bg=BG_MAIN)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 8))

        right_col = tk.Frame(parent, bg=BG_MAIN)
        right_col.pack(side="right", fill="both", expand=True, padx=(8, 0))

        tk.Label(
            left_col,
            text="ZASOBY PAŃSTWA (COUNTRY RESOURCES)",
            font=("Segoe UI", 9, "bold"),
            fg=FG_SECONDARY,
            bg=BG_MAIN,
        ).pack(anchor="w", pady=(0, 6))

        country_stats = [
            ("pp", "Siła Polityczna (Political Power)", "CFixedPoint (*100k) @ CPoliticalStatus+0xE0", "2000", 2000.0),
            ("cp", "Siła Dowodzenia (Command Power)", "CFixedPoint (*100k) @ CCountry+0x1F0", "200", 200.0),
            ("stability", "Stabilność (Stability %)", "CFixedPoint (*1k/%) @ CCountry+0x10D0", "100", 100.0),
            ("war_support", "Poparcie Wojenne (War Support %)", "CFixedPoint (*1k/%) @ CCountry+0x10D8", "100", 100.0),
            ("manpower", "Zasoby Ludzkie (Manpower)", "int32 (1:1) @ CCountry+0x340", "5000000", 5_000_000.0),
            ("fuel", "Paliwo (Current Fuel)", "Q49.15 (*32768) @ CFuelMgr+0x08", "500000", -1.0),
            ("nukes", "Bomby Atomowe (Nuclear Bombs)", "int64 (*10^8) @ CNuclearMgr+0x18", "100", 999.0),
        ]

        for key, title, subtitle, def_val, lock_val in country_stats:
            self._create_stat_row(left_col, key, title, subtitle, def_val, lock_val)

        tk.Label(
            right_col,
            text="DOŚWIADCZENIE WOJSKOWE (MILITARY EXPERIENCE)",
            font=("Segoe UI", 9, "bold"),
            fg=FG_SECONDARY,
            bg=BG_MAIN,
        ).pack(anchor="w", pady=(0, 6))

        xp_stats = [
            ("xp_army", "Doświadczenie Armii (Army XP)", "Q49.15 (*32768) @ CCountryXP+0x40", "500", 500.0),
            ("xp_navy", "Doświadczenie Marynarki (Navy XP)", "Q49.15 (*32768) @ CCountryXP+0x28", "500", 500.0),
            ("xp_air", "Doświadczenie Lotnictwa (Air XP)", "Q49.15 (*32768) @ CCountryXP+0x10", "500", 500.0),
        ]

        for key, title, subtitle, def_val, lock_val in xp_stats:
            self._create_stat_row(right_col, key, title, subtitle, def_val, lock_val)

        # Panel szybkiego pakietu "God Mode / Superpower Pack" pod XP
        quick_card = tk.Frame(right_col, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1, padx=14, pady=12)
        quick_card.pack(fill="x", pady=(12, 0))

        tk.Label(
            quick_card,
            text="🚀 SZYBKIE PAKIETY (ONE-CLICK PRESETS)",
            font=("Segoe UI", 10, "bold"),
            fg=FG_PRIMARY,
            bg=BG_CARD,
        ).pack(anchor="w")

        tk.Label(
            quick_card,
            text="Natychmiastowe napełnienie wszystkich zasobów i doświadczenia wybranego państwa:",
            font=("Segoe UI", 8),
            fg=FG_SECONDARY,
            bg=BG_CARD,
        ).pack(anchor="w", pady=(2, 8))

        btn_row = tk.Frame(quick_card, bg=BG_CARD)
        btn_row.pack(fill="x")

        tk.Button(
            btn_row,
            text="⚡ Max Wszystkie Zasoby & XP",
            font=("Segoe UI", 9, "bold"),
            bg=ACCENT_GREEN,
            fg="#09130e",
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
            command=self._apply_max_all_resources,
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            btn_row,
            text="🔒 Zablokuj (Unlimited) Wszystkie Zasoby",
            font=("Segoe UI", 9, "bold"),
            bg=ACCENT_BLUE,
            fg=FG_PRIMARY,
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
            command=self._lock_all_resources,
        ).pack(side="left")

    def _create_stat_row(self, parent, key, title, subtitle, default_str, lock_val):
        row = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1, padx=12, pady=7)
        row.pack(fill="x", pady=3)

        info_box = tk.Frame(row, bg=BG_CARD)
        info_box.pack(side="left", fill="x", expand=True)

        tk.Label(info_box, text=title, font=("Segoe UI", 9, "bold"), fg=FG_PRIMARY, bg=BG_CARD).pack(anchor="w")
        tk.Label(info_box, text=subtitle, font=("Consolas", 8), fg=FG_SECONDARY, bg=BG_CARD).pack(anchor="w")

        # Aktualna wartość w grze
        val_lbl = tk.Label(row, text="---", font=("Consolas", 10, "bold"), fg=ACCENT_GREEN, bg=BG_CARD, width=11, anchor="e")
        val_lbl.pack(side="left", padx=(4, 8))
        self.stat_value_labels[key] = val_lbl

        # Pole wpisania wartości
        entry = tk.Entry(
            row,
            width=9,
            font=("Consolas", 9),
            bg=BG_INPUT,
            fg=FG_PRIMARY,
            insertbackground=FG_PRIMARY,
            relief="flat",
            justify="right",
        )
        entry.insert(0, default_str)
        entry.pack(side="left", padx=4, ipady=3)
        self.stat_entries[key] = entry

        # Przycisk Ustaw
        tk.Button(
            row,
            text="Ustaw",
            font=("Segoe UI", 8, "bold"),
            bg=ACCENT_BLUE,
            fg=FG_PRIMARY,
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            command=lambda k=key: self._on_set_stat_clicked(k),
        ).pack(side="left", padx=4)

        # Przełącznik Lock / Unlimited
        tk.Label(row, text="Lock:", font=("Segoe UI", 8), fg=FG_SECONDARY, bg=BG_CARD).pack(side="left", padx=(4, 2))
        sw = PillToggleSwitch(row, initial=False)
        sw.pack(side="left")
        self.locked_stats[key] = (sw, lock_val)

    # ------------------------------------------------------------------------
    # ZAKŁADKA 2: PRZEŁĄCZNIKI TAKTYCZNE (14 GLOBALNYCH FLAG .DATA)
    # ------------------------------------------------------------------------
    def _build_toggles_tab(self, parent: tk.Frame):
        left_col = tk.Frame(parent, bg=BG_MAIN)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 8))

        right_col = tk.Frame(parent, bg=BG_MAIN)
        right_col.pack(side="right", fill="both", expand=True, padx=(8, 0))

        mid = (len(GAME_TOGGLES) + 1) // 2
        for idx, (tid, title, desc, rva_list, _vk, hk_str) in enumerate(GAME_TOGGLES):
            target_col = left_col if idx < mid else right_col
            card = tk.Frame(target_col, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1, padx=12, pady=8)
            card.pack(fill="x", pady=3)

            text_box = tk.Frame(card, bg=BG_CARD)
            text_box.pack(side="left", fill="x", expand=True)

            tk.Label(text_box, text=title, font=("Segoe UI", 9, "bold"), fg=FG_PRIMARY, bg=BG_CARD).pack(anchor="w")
            tk.Label(text_box, text=desc, font=("Consolas", 8), fg=FG_SECONDARY, bg=BG_CARD).pack(anchor="w")

            # Odznaka skrótu klawiszowego (F1 - F11)
            hk_badge = tk.Label(
                card,
                text=hk_str,
                font=("Consolas", 9, "bold"),
                fg=ACCENT_AMBER if hk_str != "—" else FG_SECONDARY,
                bg="#10131a",
                padx=8,
                pady=2,
            )
            hk_badge.pack(side="left", padx=10)

            sw = PillToggleSwitch(
                card,
                initial=False,
                command=lambda state, rvas=rva_list, name=title: self._on_toggle_switched(name, rvas, state),
            )
            sw.pack(side="right")
            self.toggle_widgets[tid] = (sw, rva_list, title)

    # ------------------------------------------------------------------------
    # ZAKŁADKA 3: ARCHITEKTURA PAMIĘCI & POINTER PATH (EDUKACYJNE RE)
    # ------------------------------------------------------------------------
    def _build_re_info_tab(self, parent: tk.Frame):
        card = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1, padx=18, pady=14)
        card.pack(fill="both", expand=True)

        tk.Label(
            card,
            text="DOKUMENTACJA REVERSE ENGINEERINGU (CLAUSEWITZ ENGINE x64)",
            font=("Segoe UI", 11, "bold"),
            fg=ACCENT_BLUE,
            bg=BG_CARD,
        ).pack(anchor="w", pady=(0, 8))

        self.re_text = tk.Text(
            card,
            bg="#10131a",
            fg=FG_PRIMARY,
            font=("Consolas", 9),
            relief="flat",
            padx=12,
            pady=12,
            wrap="word",
        )
        self.re_text.pack(fill="both", expand=True)

        info_doc = (
            "1. JAK DZIAŁA ROZWIĄZYWANIE PAŃSTWA GRACZA (POINTER PATH):\n"
            "   • Baza modułu hoi4.exe jest losowana przy każdym starcie przez ASLR (np. 0x7FF6xxxx0000).\n"
            "   • Pod statycznym RVA +0x33DD6B0 w sekcji .data znajduje się globalny wskaźnik CGameState*.\n"
            "   • Wewnątrz CGameState pod offsetem +0x520 (fallback +0x524) leży 32-bitowe ID państwa gracza (TagID).\n"
            "   • Tablica translacji *(uint64*)(CGameState + 0x340) + TagID * 4 zwraca CountryIndex.\n"
            "   • Tablica wskaźników *(uint64*)(CGameState + 0x310) + CountryIndex * 8 zwraca obiekt CCountry*.\n\n"
            "2. DWA FORMATY STAŁOPRZECINKOWE (FIXED-POINT) W HOI4:\n"
            "   • CFixedPoint (skala dziesiętna 100,000 = 0x186A0 na 1.0 jednostki):\n"
            "     - Political Power: *(int64*)(*(uint64*)(CCountry + 0xF90) + 0xE0)\n"
            "     - Command Power:   *(int64*)(CCountry + 0x1F0)\n"
            "     - Stability:       *(int64*)(CCountry + 0x10D0)  [100,000 = 100%, czyli 1,000 na 1%]\n"
            "     - War Support:     *(int64*)(CCountry + 0x10D8)  [100,000 = 100%, czyli 1,000 na 1%]\n"
            "   • CFixedPoint64 Q49.15 (skala binarna 32,768 = 1 << 15 na 1.0 jednostki, konwersja przez sub_142572790):\n"
            "     - Fuel (Paliwo):   *(int64*)(*(uint64*)(CCountry + 0x1580) + 0x08) [Max Capacity pod +0x10]\n"
            "     - Army XP:         *(int64*)(*(uint64*)(CCountry + 0x1588) + 0x40)\n"
            "     - Navy XP:         *(int64*)(*(uint64*)(CCountry + 0x1588) + 0x28)\n"
            "     - Air XP:          *(int64*)(*(uint64*)(CCountry + 0x1588) + 0x10)\n\n"
            "3. GLOBALNE FLAGI PRZEŁĄCZNIKÓW W SEKCJI .DATA (RVA 0x33DDA86 .. 0x33DDABD):\n"
            "   • Zamiast modyfikować kod wykonywalny (.text), co mogłoby destabilizować grę, przełączniki silnika\n"
            "     przestawiają jednobajtowe flagi (uint8) w sekcji .data, które silnik sam sprawdza w każdej turze:\n"
            "     - 0x33DDA98 = Instant Construction (ic)      - 0x33DDA9B = Instant Army Training (it)\n"
            "     - 0x33DDA99 = Instant Ship Refitting         - 0x33DDA86 = Instant Research on Click\n"
            "     - 0x33DDA9C/A0/A1 = Fast Focus & NoChecks    - 0x33DDAA2/A3 = Fast Decisions & NoChecks\n"
            "     - 0x33DDA8B = Teleport / Instant Move        - 0x33DDA87/89 = AllowDiplo + Yesman\n"
            "     - 0x33DDAB4/B5 = Instant WarGoal + NoCB      - 0x312AC80 = No Supply Penalties (0 = brak kar)\n"
        )
        self.re_text.insert("1.0", info_doc)

    # ------------------------------------------------------------------------
    # AKCJE UŻYTKOWNIKA I PĘTLA ODŚWIEŻANIA / ZAMRAŻANIA (FREEZE LOOP)
    # ------------------------------------------------------------------------
    def _on_set_stat_clicked(self, key: str):
        if not self.ctx.get("ok"):
            messagebox.showwarning("Brak aktywnej kampanii", "Uruchom Hearts of Iron IV i wejdź do kampanii Single-Player!")
            return
        raw_txt = self.stat_entries[key].get().strip().replace(",", ".")
        try:
            val = float(raw_txt)
        except ValueError:
            messagebox.showerror("Błąd formatu", f"Nieprawidłowa liczba: {raw_txt}")
            return

        if self.engine.set_stat(self.ctx, key, val):
            self.lbl_footer.configure(text=f"[+] Zapisano nową wartość ({val}) dla '{key}' w państwie {self.ctx.get('tag_str')}.", fg=ACCENT_GREEN)
        else:
            self.lbl_footer.configure(text=f"[!] Nie udało się zapisać wartości dla '{key}'.", fg=ACCENT_RED)

    def _on_toggle_switched(self, name: str, rva_list: list, state: bool):
        if not self.engine.h_proc:
            self.lbl_footer.configure(text=f"[!] Uruchom hoi4.exe, aby przełączyć: {name}", fg=ACCENT_AMBER)
            return
        if self.engine.write_toggle_state(rva_list, state):
            st_txt = "WŁĄCZONO (ON)" if state else "WYŁĄCZONO (OFF)"
            self.lbl_footer.configure(text=f"[+] {name} -> {st_txt}", fg=ACCENT_GREEN if state else FG_SECONDARY)

    def _toggle_ironman(self):
        if not self.ctx.get("gs_ptr"):
            messagebox.showwarning("Brak gry", "Uruchom hoi4.exe i wczytaj zapis gry.")
            return
        new_ironman = not self.ctx.get("ironman_on", False)
        if self.engine.set_ironman(self.ctx, new_ironman):
            st = "WŁĄCZONY" if new_ironman else "WYŁĄCZONY (Konsola i modyfikacje odblokowane)"
            self.lbl_footer.configure(text=f"[+] Tryb Ironman: {st}", fg=ACCENT_GREEN)

    def _apply_max_all_resources(self):
        if not self.ctx.get("ok"):
            messagebox.showwarning("Brak kampanii", "Wejdź do kampanii Single-Player w HOI4!")
            return
        presets = [
            ("pp", 2000.0), ("cp", 200.0), ("stability", 100.0), ("war_support", 100.0),
            ("manpower", 10_000_000.0), ("fuel", -1.0), ("nukes", 999.0),
            ("xp_army", 500.0), ("xp_navy", 500.0), ("xp_air", 500.0),
        ]
        for k, v in presets:
            self.engine.set_stat(self.ctx, k, v)
        self.lbl_footer.configure(
            text=f"[+] Ustawiono maksymalne zasoby i doświadczenie dla państwa {self.ctx.get('tag_str')}!",
            fg=ACCENT_GREEN,
        )

    def _lock_all_resources(self):
        any_unlocked = any(not sw.state for sw, _ in self.locked_stats.values())
        for sw, _ in self.locked_stats.values():
            sw.set_state(any_unlocked)
        st = "ZABLOKOWANO (Unlimited ON)" if any_unlocked else "ODBLOKOWANO (Unlimited OFF)"
        self.lbl_footer.configure(text=f"[+] Wszystkie zasoby i XP: {st}", fg=ACCENT_GREEN)

    def _poll_hotkeys(self):
        now = time.time()
        for tid, title, _desc, rva_list, vk, hk_str in GAME_TOGGLES:
            if vk and self.engine.is_key_pressed(vk):
                if now - self.last_hotkey_time.get(vk, 0) > 0.35:
                    self.last_hotkey_time[vk] = now
                    sw, _, _ = self.toggle_widgets[tid]
                    sw.set_state(not sw.state, notify=True)

    def _schedule_loop(self):
        try:
            self._poll_hotkeys()
            attached = self.engine.attach()
            if not attached:
                self.lbl_proc_status.configure(text="● PROCES: OCZEKIWANIE NA HOI4.EXE...", fg=ACCENT_AMBER)
                self.lbl_country_status.configure(text="Uruchom grę Hearts of Iron IV (64-bit), trener podepnie się automatycznie.")
                self.ctx = {"ok": False}
            else:
                self.lbl_proc_status.configure(
                    text=f"● POŁĄCZONO: hoi4.exe (PID: {self.engine.pid} | Baza ASLR: 0x{self.engine.base_addr:X})",
                    fg=ACCENT_GREEN,
                )
                self.ctx = self.engine.resolve_player_country()
                if self.ctx.get("ok"):
                    iron_str = "TAK (Zablokowany)" if self.ctx.get("ironman_on") else "NIE (Odblokowany)"
                    self.lbl_country_status.configure(
                        text=(
                            f"Państwo: {self.ctx['tag_str']} (TagID={self.ctx['tag_id']})  |  "
                            f"CGameState*: 0x{self.ctx['gs_ptr']:X}  |  CCountry*: 0x{self.ctx['ccountry']:X}  |  Ironman: {iron_str}"
                        ),
                        fg=FG_PRIMARY,
                    )

                    # Zamrażanie (Unlimited Freeze) zaznaczonych statystyk
                    for key, (sw, lock_val) in self.locked_stats.items():
                        if sw.state:
                            self.engine.set_stat(self.ctx, key, lock_val)

                    # Odczyt aktualnych wartości do GUI
                    stats = self.engine.read_all_stats(self.ctx)
                    fmt_map = {
                        "pp": f"{stats.get('pp', 0):,.1f}",
                        "cp": f"{stats.get('cp', 0):,.1f}",
                        "stability": f"{stats.get('stability', 0):.1f}%",
                        "war_support": f"{stats.get('war_support', 0):.1f}%",
                        "manpower": f"{stats.get('manpower', 0):,d}",
                        "fuel": f"{stats.get('fuel', 0):,.0f}",
                        "nukes": f"{stats.get('nukes', 0):,d}",
                        "xp_army": f"{stats.get('xp_army', 0):,.1f}",
                        "xp_navy": f"{stats.get('xp_navy', 0):,.1f}",
                        "xp_air": f"{stats.get('xp_air', 0):,.1f}",
                    }
                    for k, txt in fmt_map.items():
                        if k in self.stat_value_labels:
                            self.stat_value_labels[k].configure(text=txt)
                else:
                    self.lbl_country_status.configure(text=f"Status kampanii: {self.ctx.get('reason')}", fg=ACCENT_AMBER)

                # Synchronizuj stan przełączników z pamięcią gry
                for tid, (sw, rva_list, _title) in self.toggle_widgets.items():
                    mem_state = self.engine.read_toggle_state(rva_list)
                    sw.set_state(mem_state, notify=False)
        except Exception:
            pass

        self.root.after(250, self._schedule_loop)


def main():
    if "--verify-static" in sys.argv:
        ok = verify_static_binary(DEFAULT_HOI4_PATH)
        sys.exit(0 if ok else 1)

    root = tk.Tk()
    _app = HOI4CommanderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
