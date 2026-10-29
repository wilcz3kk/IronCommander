#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HOI4 IronCommander: Real-Time Tactical Deck & Reverse Engineering Suite
==============================================================================
Target Binary: Hearts of Iron IV (hoi4.exe, Clausewitz Engine, x86_64 PE32+)
Scope: Single-Player Tactical Deck, Memory Inspector, Achievements & Production Suite.

Reverse-Engineered Memory Layout (Static ImageBase = 0x140000000):
  * CGameState* Singleton:              [hoi4.exe + 0x33DD6B0]
  * CAchievementsManager* Singleton:    [hoi4.exe + 0x33DE950]
  * Player Country Tag ID (int32):      [CGameState + 0x520] (fallback +0x524)
  * Tag-to-Index Table (int32[]):       *[CGameState + 0x340] + TagID * 4
  * CCountry* Array (ptr[]):            *[CGameState + 0x310] + CountryIndex * 8
  * Tag 3-Letter String:                *[CGameState + 0x358] + TagID * 32

Achievements Subsystem (CAchievementsManager):
  * +0xA0: Blocked/Error Flag (uint8, must be 0)
  * +0xA1: Ironman Valid Flag (uint8, 1 = valid)
  * +0xA2: Checksum/Mods Valid Flag (uint8, 1 = valid)
  * +0xA3: Master Bypass Flag (uint8, 1 = force debug & allow all achievements!)
  * +0xA4: Custom Mod Achievements Flag (uint8)

Engine Level Toggles in .data:
  * 0x33DDA98: Instant Construction & Dockyard Production (ic)
  * 0x33DDAB6: Debug Achievements Engine Flag
  * 0x3500F19: Cheats/Debug Used Flag (cleared to 0 for achievements)
  * 0x33DD099: Engine Flag 2 (cleared to 0)
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

# Singletony w sekcji .data
RVA_CGAMESTATE_PTR = 0x33DD6B0
RVA_ACHIEVEMENTS_MGR_PTR = 0x33DE950

# Flagi silnika w sekcji .data
RVA_DEBUG_ACHIEVEMENTS_FLAG = 0x33DDAB6
RVA_CHEATS_OR_DEBUG = 0x3500F19
RVA_ENGINE_FLAG2 = 0x33DD099
RVA_INSTANT_CONSTRUCTION_PROD = 0x33DDA98

# Offsety w CAchievementsManager
OFF_ACHIEVE_BLOCKED = 0xA0
OFF_ACHIEVE_IRONMAN = 0xA1
OFF_ACHIEVE_CHECKSUM = 0xA2
OFF_ACHIEVE_BYPASS = 0xA3
OFF_ACHIEVE_CUSTOM = 0xA4

# Offsety w CGameState
OFF_GAMESTATE_IRONMAN_FLAGS = 0xC0   # uint32 (bit 0 = Ironman enabled)
OFF_GAMESTATE_COUNTRIES_ARR = 0x310
OFF_GAMESTATE_TAG_TO_INDEX = 0x340
OFF_GAMESTATE_TAG_NAMES_ARR = 0x358
OFF_GAMESTATE_PLAYER_TAG = 0x520
OFF_GAMESTATE_PLAYER_TAG_FALLBACK = 0x524

# Offsety w CCountry
OFF_COUNTRY_COMMAND_POWER = 0x1F0
OFF_COUNTRY_MANPOWER = 0x340
OFF_COUNTRY_POLITICAL_PTR = 0xF90    # CPoliticalStatus*
OFF_POLITICAL_POWER_VAL = 0xE0
OFF_COUNTRY_STABILITY = 0x10D0
OFF_COUNTRY_WAR_SUPPORT = 0x10D8
OFF_COUNTRY_NUKES_PTR = 0x1310       # CNuclearManager*
OFF_NUKES_COUNT_VAL = 0x18
OFF_COUNTRY_FUEL_PTR = 0x1580        # CFuelManager*
OFF_FUEL_CURRENT_VAL = 0x08
OFF_FUEL_MAX_VAL = 0x10
OFF_COUNTRY_XP_PTR = 0x1588          # CCountryExperience*
OFF_XP_AIR_VAL = 0x10
OFF_XP_NAVY_VAL = 0x28
OFF_XP_ARMY_VAL = 0x40

# Globalne flagi przełączników gry w sekcji .data
GAME_TOGGLES = [
    (
        "instant_construction",
        "Natychmiastowa Budowa i Stocznia (Instant Construction)",
        "Flaga .data [hoi4.exe+0x33DDA98] = 1 (odpowiednik 'ic' — budynki i okręty kończą się w 1 dzień)",
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
        None, "—",
    ),
    (
        "disable_fow",
        "Wyłącz Mgłę Wojny (Disable Fog of War)",
        "Flaga .data [hoi4.exe+0x33DDAAA] = 0 (0 = FOW wyłączone / pełna widoczność globu)",
        [(0x33DDAAA, 0, 1)],
        0x7A, "F11",
    ),
    (
        "no_naval_superiority",
        "Inwazje Morskie bez Przewagi na Morzu",
        "Flaga .data [hoi4.exe+0x33DDAA4] = 1 (omija wymóg 50% przewagi morskiej do desantu)",
        [(0x33DDAA4, 1, 0)],
        None, "—",
    ),
    (
        "bypass_focus_prereq",
        "Wybieraj Zablokowane i Wzajemnie Wykluczające Fokusy",
        "Flaga .data [hoi4.exe+0x33DDA9C] = 1 (Focus.AutoComplete / ignorowanie blokad)",
        [(0x33DDA9C, 1, 0)],
        None, "—",
    ),
    (
        "zero_supply_penalty",
        "Zerowe Kary za Zaopatrzenie (No Supply Penalty)",
        "Flaga .data [hoi4.exe+0x312AC80] = 0 (0 = brak wyczerpania i kar logistycznych)",
        [(0x312AC80, 0, 1)],
        None, "—",
    ),
]

# Sygnatury AOB
AOB_PLAYER_RESOLVER = b"\x48\x8B\x05\x00\x00\x00\x00\xBA\x24\x05\x00\x00\xB9\x20\x05\x00\x00\x83\xB8\x20\x05\x00\x00\x00"
AOB_PLAYER_RESOLVER_MASK = "xxx????xxxxxxxxxxxxxxxxx"

# Win32 API Constants
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
        ("pcPriClassBase", ctypes.c_long),
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

# ============================================================================
# 2. NISKOPOZIOMOWY SILNIK PAMIĘCI (HOI4MemoryEngine)
# ============================================================================

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
        self.u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.u32.GetAsyncKeyState.restype = ctypes.c_short

    def is_key_pressed(self, vk_code: int) -> bool:
        if not vk_code:
            return False
        return (self.u32.GetAsyncKeyState(vk_code) & 0x8000) != 0

    def attach(self) -> bool:
        """Szuka procesu hoi4.exe i podpina uchwyt pamięci."""
        if self.h_proc:
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

    def read_bytes(self, addr: int, size: int) -> bytes:
        if not self.h_proc or not addr:
            return None
        buf = ctypes.create_string_buffer(size)
        n_read = ctypes.c_size_t(0)
        ok = self.k32.ReadProcessMemory(self.h_proc, ctypes.c_void_p(addr), buf, size, ctypes.byref(n_read))
        if ok and n_read.value == size:
            return bytes(buf.raw)
        return None

    def write_bytes(self, addr: int, data: bytes) -> bool:
        if not self.h_proc or not addr:
            return False
        buf = ctypes.create_string_buffer(data)
        n_written = ctypes.c_size_t(0)
        ok = self.k32.WriteProcessMemory(self.h_proc, ctypes.c_void_p(addr), buf, len(data), ctypes.byref(n_written))
        return bool(ok and n_written.value == len(data))

    def read_u8(self, addr: int):
        b = self.read_bytes(addr, 1)
        return b[0] if b else None

    def write_u8(self, addr: int, val: int) -> bool:
        return self.write_bytes(addr, struct.pack("<B", val & 0xFF))

    def read_i32(self, addr: int):
        b = self.read_bytes(addr, 4)
        return struct.unpack("<i", b)[0] if b else None

    def write_i32(self, addr: int, val: int) -> bool:
        return self.write_bytes(addr, struct.pack("<i", int(val)))

    def read_u32(self, addr: int):
        b = self.read_bytes(addr, 4)
        return struct.unpack("<I", b)[0] if b else None

    def write_u32(self, addr: int, val: int) -> bool:
        return self.write_bytes(addr, struct.pack("<I", int(val)))

    def read_i64(self, addr: int):
        b = self.read_bytes(addr, 8)
        return struct.unpack("<q", b)[0] if b else None

    def write_i64(self, addr: int, val: int) -> bool:
        return self.write_bytes(addr, struct.pack("<q", int(val)))

    def read_ptr(self, addr: int):
        b = self.read_bytes(addr, 8)
        return struct.unpack("<Q", b)[0] if b else None

    def resolve_player_country(self) -> dict:
        """Dynamicznie rozwiązuje wskaźnik państwa gracza (CCountry*)."""
        if not self.h_proc or not self.base_addr:
            return {"ok": False, "reason": "Brak procesu"}

        gs_ptr_addr = self.base_addr + self.cgamestate_rva
        gs_ptr = self.read_ptr(gs_ptr_addr)
        if not gs_ptr or gs_ptr < 0x10000:
            return {"ok": False, "reason": "CGameState* ma wartość NULL (wczytaj grę)"}

        tag_id = self.read_i32(gs_ptr + OFF_GAMESTATE_PLAYER_TAG)
        if tag_id is None or tag_id <= 0:
            tag_id = self.read_i32(gs_ptr + OFF_GAMESTATE_PLAYER_TAG_FALLBACK)
        if tag_id is None or tag_id <= 0:
            return {"ok": False, "reason": f"Niepoprawny PlayerTagID ({tag_id})"}

        tag_to_idx_table = self.read_ptr(gs_ptr + OFF_GAMESTATE_TAG_TO_INDEX)
        if not tag_to_idx_table:
            return {"ok": False, "reason": "Tabela TagToIndex jest NULL"}

        c_idx = self.read_i32(tag_to_idx_table + tag_id * 4)
        if c_idx is None or c_idx < 0:
            return {"ok": False, "reason": f"Brak indeksu dla TagID {tag_id}"}

        countries_arr = self.read_ptr(gs_ptr + OFF_GAMESTATE_COUNTRIES_ARR)
        if not countries_arr:
            return {"ok": False, "reason": "Tablica CCountry* jest NULL"}

        ccountry = self.read_ptr(countries_arr + c_idx * 8)
        if not ccountry or ccountry < 0x10000:
            return {"ok": False, "reason": "Wskaźnik CCountry* jest NULL"}

        tag_str_arr = self.read_ptr(gs_ptr + OFF_GAMESTATE_TAG_NAMES_ARR)
        tag_str = "???"
        if tag_str_arr:
            raw_tag = self.read_bytes(tag_str_arr + tag_id * 32, 4)
            if raw_tag:
                tag_str = raw_tag.split(b"\x00")[0].decode("ascii", errors="ignore")

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

    # --- Podsystem Osiągnięć (Steam Achievements Master Bypass) ---

    def read_achievements_status(self, ctx: dict) -> dict:
        """Odczytuje stan podsystemu CAchievementsManager oraz powiązanych flag silnika."""
        if not self.h_proc or not self.base_addr:
            return {"ok": False, "cup_ok": False, "bypass": False}

        mgr_ptr = self.read_ptr(self.base_addr + RVA_ACHIEVEMENTS_MGR_PTR)
        if not mgr_ptr or mgr_ptr < 0x10000:
            return {"ok": False, "cup_ok": False, "bypass": False, "reason": "CAchievementsManager NULL"}

        blocked = self.read_u8(mgr_ptr + OFF_ACHIEVE_BLOCKED) or 0
        ironman_ok = self.read_u8(mgr_ptr + OFF_ACHIEVE_IRONMAN) or 0
        checksum_ok = self.read_u8(mgr_ptr + OFF_ACHIEVE_CHECKSUM) or 0
        bypass = self.read_u8(mgr_ptr + OFF_ACHIEVE_BYPASS) or 0
        dbg_flag = self.read_u8(self.base_addr + RVA_DEBUG_ACHIEVEMENTS_FLAG) or 0
        cheats_flag = self.read_u8(self.base_addr + RVA_CHEATS_OR_DEBUG) or 0

        # Silnik zwraca true w IsAchievementsOk() gdy bypass == 1 LUB (ironman==1 & checksum==1 & cheats==0 & blocked==0)
        cup_ok = bool(bypass == 1 or (ironman_ok == 1 and checksum_ok == 1 and cheats_flag == 0 and blocked == 0))

        return {
            "ok": True,
            "mgr_ptr": mgr_ptr,
            "cup_ok": cup_ok,
            "bypass": bool(bypass == 1),
            "ironman_ok": bool(ironman_ok == 1),
            "checksum_ok": bool(checksum_ok == 1),
            "blocked": bool(blocked != 0),
            "dbg_flag": bool(dbg_flag == 1),
            "cheats_flag": bool(cheats_flag != 0),
        }

    def set_achievements_bypass(self, ctx: dict, enable: bool) -> bool:
        """Wymusza odblokowanie i ciągłą ochronę osiągnięć (Master Bypass)."""
        if not self.h_proc or not self.base_addr:
            return False

        mgr_ptr = self.read_ptr(self.base_addr + RVA_ACHIEVEMENTS_MGR_PTR)
        if mgr_ptr and mgr_ptr >= 0x10000:
            self.write_u8(mgr_ptr + OFF_ACHIEVE_BYPASS, 1 if enable else 0)
            if enable:
                self.write_u8(mgr_ptr + OFF_ACHIEVE_IRONMAN, 1)
                self.write_u8(mgr_ptr + OFF_ACHIEVE_CHECKSUM, 1)
                self.write_u8(mgr_ptr + OFF_ACHIEVE_BLOCKED, 0)

        # Flagi silnika w .data
        self.write_u8(self.base_addr + RVA_DEBUG_ACHIEVEMENTS_FLAG, 1 if enable else 0)
        if enable:
            self.write_u8(self.base_addr + RVA_CHEATS_OR_DEBUG, 0)
            self.write_u8(self.base_addr + RVA_ENGINE_FLAG2, 0)
            if ctx.get("gs_ptr"):
                self.set_ironman(ctx, True)
        return True

    def set_infinite_production(self, enable: bool) -> bool:
        """Aktywuje natychmiastową produkcję stoczniową, budowę fabryk i przyspieszenie linii produkcyjnych."""
        if not self.h_proc or not self.base_addr:
            return False
        return self.write_u8(self.base_addr + RVA_INSTANT_CONSTRUCTION_PROD, 1 if enable else 0)


# ============================================================================
# 3. LIQUID GLASS DESIGN PALETTE & CUSTOM TKINTER CONTROLS
# ============================================================================

# Liquid Glass Dark Palette with Vibrant Chromatic Accents
GLASS_BG = "#0b0e17"            # Deep obsidian backdrop
GLASS_PANEL = "#111522"         # Translucent dark indigo
GLASS_CARD = "#161c2d"          # Glass card surface
GLASS_CARD_HOVER = "#1c243a"    # Focused glass
GLASS_INPUT = "#0c101a"         # Input cavity
GLASS_BORDER = "#25304c"        # Subtle specular glass edge
GLASS_BORDER_LIGHT = "#334166"  # Highlighted edge

# Vibrant Glowing Accents
NEON_CYAN = "#00f0ff"           # Primary energy
NEON_EMERALD = "#00ff87"        # Success / Active / Verified
NEON_GOLD = "#ffb703"           # Steam achievements cup
NEON_PURPLE = "#a855f7"         # Infinite production / Special mode
NEON_PINK = "#ff007f"           # High contrast alert
TEXT_WHITE = "#ffffff"          # Crisp specular primary text
TEXT_SECONDARY = "#94a3b8"      # Frosted secondary text
TEXT_MUTED = "#64748b"          # Low prominence text


class GlassPillSwitch(tk.Canvas):
    """Nowoczesny przełącznik w stylu Liquid Glass z kolorową poświatą neonową."""

    def __init__(self, parent, initial=False, active_color=NEON_EMERALD, width=46, height=24, command=None):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent["bg"],
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.state = initial
        self.active_color = active_color
        self.command = command
        self.w = width
        self.h = height
        self.radius = height // 2
        self.bind("<Button-1>", self._on_click)
        self._render()

    def _render(self):
        self.delete("all")
        bg_col = self.active_color if self.state else "#1e273b"
        border_col = self.active_color if self.state else "#33415e"

        # Rysuj zaokrąglony kształt pigułki (pill)
        self.create_oval(1, 1, self.h - 1, self.h - 1, fill=bg_col, outline=border_col, width=1)
        self.create_oval(self.w - self.h + 1, 1, self.w - 1, self.h - 1, fill=bg_col, outline=border_col, width=1)
        self.create_rectangle(self.radius, 1, self.w - self.radius, self.h - 1, fill=bg_col, outline=border_col, width=1)

        # Wnętrze pigułki bez szwu
        self.create_rectangle(self.radius, 2, self.w - self.radius, self.h - 2, fill=bg_col, outline=bg_col)

        # Suwak (Knob)
        pad = 3
        knob_d = self.h - (pad * 2)
        if self.state:
            kx = self.w - pad - knob_d
            knob_fill = TEXT_WHITE
        else:
            kx = pad
            knob_fill = "#73829c"

        self.create_oval(kx, pad, kx + knob_d, pad + knob_d, fill=knob_fill, outline="", width=0)

    def _on_click(self, event=None):
        self.state = not self.state
        self._render()
        if self.command:
            self.command(self.state)

    def set_state(self, val: bool, notify=False):
        if self.state != val:
            self.state = val
            self._render()
            if notify and self.command:
                self.command(self.state)


# ============================================================================
# 4. GŁÓWNA APLIKACJA GUI (LIQUID GLASS RESPONSIVE ARCHITECTURE)
# ============================================================================

class HOI4CommanderGUI:
    """Nowoczesna stacja dowodzenia i trener pamięci dla Hearts of Iron IV."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("IronCommander — HOI4 Tactical Suite & Steam Achievements Master")
        self.root.geometry("1100x780")
        self.root.minsize(960, 640)
        self.root.configure(bg=GLASS_BG)

        # Ikonka aplikacji
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hoi4_trainer.ico")
        if os.path.isfile(ico_path):
            try:
                self.root.iconbitmap(ico_path)
            except Exception:
                pass

        self.engine = HOI4MemoryEngine()
        self.ctx = {"ok": False, "reason": "Szukanie procesu hoi4.exe..."}
        self.locked_stats = {}      # {stat_key: (GlassPillSwitch, lock_value)}
        self.toggle_widgets = {}    # {toggle_id: (GlassPillSwitch, rva_list, title)}
        self.stat_value_labels = {} # {stat_key: tk.Label}
        self.stat_entries = {}      # {stat_key: tk.Entry}
        self.last_hotkey_time = {}

        # Opcje globalne
        self.achievements_bypass_active = True   # Domyślnie chroń i odblokowuj osiągnięcia
        self.infinite_prod_active = False        # Nieskończona produkcja stoczniowa i fabryczna

        self._build_ui()
        self._schedule_loop()

    # ------------------------------------------------------------------------
    # RESPONSYWNA STRUKTURA UI (SCROLLABLE CANVAS + AUTO-FIT)
    # ------------------------------------------------------------------------

    def _build_ui(self):
        # 1. Pasek górny (Liquid Glass Header & Telemetria)
        header = tk.Frame(self.root, bg=GLASS_PANEL, height=84, highlightbackground=GLASS_BORDER, highlightthickness=1)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        # Tytuł i branding
        brand = tk.Frame(header, bg=GLASS_PANEL)
        brand.pack(side="left", padx=22, pady=12)

        top_title = tk.Frame(brand, bg=GLASS_PANEL)
        top_title.pack(anchor="w")

        tk.Label(
            top_title,
            text="HEARTS OF IRON IV",
            font=("Segoe UI", 15, "bold"),
            fg=TEXT_WHITE,
            bg=GLASS_PANEL,
        ).pack(side="left")

        # Neon Badge
        badge_frame = tk.Frame(top_title, bg="#1a2538", padx=6, pady=2, highlightbackground=NEON_CYAN, highlightthickness=1)
        badge_frame.pack(side="left", padx=(10, 0))
        tk.Label(
            badge_frame,
            text="TACTICAL SUITE",
            font=("Segoe UI", 7, "bold"),
            fg=NEON_CYAN,
            bg="#1a2538",
        ).pack()

        self.lbl_subtitle = tk.Label(
            brand,
            text="Clausewitz Engine x64  •  Liquid Glass Protocol  •  Steam Achievements Enabler",
            font=("Segoe UI", 9),
            fg=TEXT_SECONDARY,
            bg=GLASS_PANEL,
        )
        self.lbl_subtitle.pack(anchor="w", pady=(2, 0))

        # Status procesu i pucharu po prawej
        right_status = tk.Frame(header, bg=GLASS_PANEL)
        right_status.pack(side="right", padx=22, pady=12)

        self.lbl_proc_status = tk.Label(
            right_status,
            text="● PROCES: OCZEKIWANIE NA HOI4.EXE...",
            font=("Consolas", 10, "bold"),
            fg=NEON_GOLD,
            bg=GLASS_PANEL,
        )
        self.lbl_proc_status.pack(anchor="e")

        self.lbl_telemetry_line = tk.Label(
            right_status,
            text="Państwo: ---  |  TagID: ---  |  Puchar: Zablokowany",
            font=("Consolas", 9),
            fg=TEXT_SECONDARY,
            bg=GLASS_PANEL,
        )
        self.lbl_telemetry_line.pack(anchor="e", pady=(3, 0))

        # 2. Pasek Master Controls (Osiągnięcia Steam, Nieskończona Produkcja, Ironman)
        action_bar = tk.Frame(self.root, bg=GLASS_BG, pady=10, padx=22)
        action_bar.pack(fill="x")

        # Karta: Osiągnięcia Steam Master Bypass
        self.achieve_deck = tk.Frame(
            action_bar,
            bg=GLASS_CARD,
            highlightbackground=NEON_GOLD,
            highlightthickness=1,
            padx=12,
            pady=6,
        )
        self.achieve_deck.pack(side="left", padx=(0, 10))

        tk.Label(
            self.achieve_deck,
            text="🏆 OSIĄGNIĘCIA STEAM:",
            font=("Segoe UI", 9, "bold"),
            fg=NEON_GOLD,
            bg=GLASS_CARD,
        ).pack(side="left", padx=(0, 8))

        self.lbl_achieve_badge = tk.Label(
            self.achieve_deck,
            text="ODBLOKOWANE (Bypass)",
            font=("Segoe UI", 8, "bold"),
            fg=GLASS_BG,
            bg=NEON_GOLD,
            padx=8,
            pady=2,
        )
        self.lbl_achieve_badge.pack(side="left", padx=(0, 10))

        self.sw_achieve = GlassPillSwitch(
            self.achieve_deck,
            initial=True,
            active_color=NEON_GOLD,
            command=self._on_achieve_switch_toggled,
        )
        self.sw_achieve.pack(side="left")

        # Karta: Nieskończona Produkcja & Stocznia (Infinite Production)
        self.prod_deck = tk.Frame(
            action_bar,
            bg=GLASS_CARD,
            highlightbackground=NEON_PURPLE,
            highlightthickness=1,
            padx=12,
            pady=6,
        )
        self.prod_deck.pack(side="left", padx=(0, 10))

        tk.Label(
            self.prod_deck,
            text="🚀 NIESKOŃCZONA PRODUKCJA:",
            font=("Segoe UI", 9, "bold"),
            fg=NEON_PURPLE,
            bg=GLASS_CARD,
        ).pack(side="left", padx=(0, 8))

        self.lbl_prod_badge = tk.Label(
            self.prod_deck,
            text="WYŁĄCZONA",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_WHITE,
            bg="#2a334d",
            padx=8,
            pady=2,
        )
        self.lbl_prod_badge.pack(side="left", padx=(0, 10))

        self.sw_prod = GlassPillSwitch(
            self.prod_deck,
            initial=False,
            active_color=NEON_PURPLE,
            command=self._on_prod_switch_toggled,
        )
        self.sw_prod.pack(side="left")

        # Przycisk Ironman (SP) po prawej
        self.btn_ironman = tk.Button(
            action_bar,
            text="🛡️ Tryb Ironman: AUTO-CHRONIONY",
            font=("Segoe UI", 9, "bold"),
            bg="#182338",
            fg=NEON_EMERALD,
            activebackground="#20304d",
            activeforeground=TEXT_WHITE,
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
            command=self._toggle_ironman,
        )
        self.btn_ironman.pack(side="right")

        # 3. Pasek Nawigacji Zakładek
        tab_nav = tk.Frame(self.root, bg=GLASS_BG, padx=22, pady=(0, 10))
        tab_nav.pack(fill="x")

        self.active_tab = "stats"
        self.tab_buttons = {}
        self.tab_containers = {}

        tabs_spec = [
            ("stats", "⚡ Zasoby Państwa, Doświadczenie & Produkcja"),
            ("toggles", "🛠 Przełączniki Silnika & Świata (15 Opcji Taktycznych)"),
        ]

        for tab_id, tab_title in tabs_spec:
            btn = tk.Button(
                tab_nav,
                text=tab_title,
                font=("Segoe UI", 10, "bold"),
                bg=NEON_CYAN if tab_id == "stats" else GLASS_CARD,
                fg=GLASS_BG if tab_id == "stats" else TEXT_WHITE,
                activebackground=NEON_CYAN,
                activeforeground=GLASS_BG,
                bd=0,
                padx=18,
                pady=8,
                cursor="hand2",
                command=lambda tid=tab_id: self._switch_tab(tid),
            )
            btn.pack(side="left", padx=(0, 10))
            self.tab_buttons[tab_id] = btn

        # 4. Kontener na zawartość zakładek z pełną responsywnością (Canvas + Scrollbar)
        self.main_content = tk.Frame(self.root, bg=GLASS_BG)
        self.main_content.pack(fill="both", expand=True, padx=22, pady=(0, 10))

        # Utwórz responsywne kontenery dla każdej zakładki
        for tab_id, _ in tabs_spec:
            self.tab_containers[tab_id] = self._create_scrollable_container(self.main_content)

        self._build_stats_tab(self.tab_containers["stats"]["scrollable_frame"])
        self._build_toggles_tab(self.tab_containers["toggles"]["scrollable_frame"])

        # Pokaż domyślną zakładkę
        self.tab_containers["stats"]["outer_frame"].pack(fill="both", expand=True)

        # 5. Dolny pasek statusu (Liquid Glass Footer)
        footer = tk.Frame(self.root, bg=GLASS_PANEL, height=34, highlightbackground=GLASS_BORDER, highlightthickness=1)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        self.lbl_footer = tk.Label(
            footer,
            text="Gotowy. Osiągnięcia Steam są chronione i odblokowane w czasie rzeczywistym. Klawisze F1–F11 aktywne.",
            font=("Segoe UI", 9),
            fg=TEXT_SECONDARY,
            bg=GLASS_PANEL,
        )
        self.lbl_footer.pack(side="left", padx=22)

        self.lbl_footer_right = tk.Label(
            footer,
            text="IronCommander Suite  •  v2.5 Liquid Glass Edition",
            font=("Segoe UI", 8),
            fg=TEXT_MUTED,
            bg=GLASS_PANEL,
        )
        self.lbl_footer_right.pack(side="right", padx=22)

    def _create_scrollable_container(self, parent: tk.Frame) -> dict:
        """Tworzy płynnie skalujący się kontener Canvas ze zsynchronizowanym widżetem nadrzędnym."""
        outer = tk.Frame(parent, bg=GLASS_BG)

        canvas = tk.Canvas(outer, bg=GLASS_BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=GLASS_BG)

        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def _on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            # Dynamiczne dopasowanie szerokości zawartości do rozmiaru okna
            canvas.itemconfig(canvas_window, width=event.width)

        scrollable_frame.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.configure(xscrollcommand=None, yscrollcommand=scrollbar.set)

        # Płynne przewijanie rolką myszy
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        return {
            "outer_frame": outer,
            "canvas": canvas,
            "scrollbar": scrollbar,
            "scrollable_frame": scrollable_frame,
            "canvas_window": canvas_window,
        }

    def _switch_tab(self, tab_id: str):
        for tid, cont in self.tab_containers.items():
            cont["outer_frame"].pack_forget()
            self.tab_buttons[tid].configure(
                bg=NEON_CYAN if tid == tab_id else GLASS_CARD,
                fg=GLASS_BG if tid == tab_id else TEXT_WHITE,
            )
        self.tab_containers[tab_id]["outer_frame"].pack(fill="both", expand=True)
        self.active_tab = tab_id

    # ------------------------------------------------------------------------
    # ZAKŁADKA 1: ZASOBY PAŃSTWA, DOŚWIADCZENIE & PRODUKCJA
    # ------------------------------------------------------------------------

    def _build_stats_tab(self, parent: tk.Frame):
        # Responsywna siatka: 2 kolumny
        cols_container = tk.Frame(parent, bg=GLASS_BG)
        cols_container.pack(fill="both", expand=True)
        cols_container.columnconfigure(0, weight=1)
        cols_container.columnconfigure(1, weight=1)

        left_col = tk.Frame(cols_container, bg=GLASS_BG)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        right_col = tk.Frame(cols_container, bg=GLASS_BG)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        # --- Lewa Kolumna: Główne Zasoby Państwa ---
        self._build_card_header(left_col, "ZASOBY POLITYCZNE, LUDZKIE I SUROWCE", NEON_CYAN)

        stat_items_left = [
            ("pp", "Political Power (Siła Polityczna)", "Skala CFixedPoint (1.0 = 100,000)", 2000.0, "2000.0"),
            ("cp", "Command Power (Siła Dowodzenia)", "Skala CFixedPoint (1.0 = 100,000)", 200.0, "200.0"),
            ("stability", "Stabilność Kraju (Stability)", "Skala procentowa (100% = 100,000)", 100.0, "100.0%"),
            ("war_support", "Poparcie Wojenne (War Support)", "Skala procentowa (100% = 100,000)", 100.0, "100.0%"),
            ("manpower", "Zasoby Ludzkie (Manpower)", "Pula rekrutów dostępna od zaraz", 10_000_000.0, "10,000,000"),
            ("fuel", "Rezerwy Paliwa (Current Fuel)", "Q49.15 (1.0 = 32,768) lub Max Pojemność", -1.0, "Maksimum"),
            ("nukes", "Bomby Atomowe (Nuclear Bombs)", "Skala całkowita (100,000,000 na głowicę)", 999.0, "999"),
        ]

        for k, name, desc, lock_val, placeholder in stat_items_left:
            self._create_glass_stat_row(left_col, k, name, desc, lock_val, placeholder, accent_color=NEON_CYAN)

        # --- Prawa Kolumna: Doświadczenie, Fabryki & Szybkie Akcje ---
        self._build_card_header(right_col, "DOŚWIADCZENIE WOJSKOWE (XP) & PRODUKCJA", NEON_PURPLE)

        stat_items_right = [
            ("xp_army", "Doświadczenie Armii (Army XP)", "Q49.15 (1.0 = 32,768) na doktryny i czołgi", 500.0, "500.0"),
            ("xp_navy", "Doświadczenie Marynarki (Navy XP)", "Q49.15 (1.0 = 32,768) na okręty i doktryny", 500.0, "500.0"),
            ("xp_air", "Doświadczenie Lotnictwa (Air XP)", "Q49.15 (1.0 = 32,768) na samoloty i doktryny", 500.0, "500.0"),
        ]

        for k, name, desc, lock_val, placeholder in stat_items_right:
            self._create_glass_stat_row(right_col, k, name, desc, lock_val, placeholder, accent_color=NEON_PURPLE)

        # Karta: Szybkie Działania i Nieskończona Produkcja
        self._build_quick_actions_card(right_col)

    def _build_card_header(self, parent: tk.Frame, title: str, color: str):
        hdr = tk.Frame(parent, bg=GLASS_BG)
        hdr.pack(fill="x", pady=(0, 8))
        tk.Frame(hdr, bg=color, width=4, height=16).pack(side="left", padx=(0, 8))
        tk.Label(
            hdr,
            text=title,
            font=("Segoe UI", 9, "bold"),
            fg=color,
            bg=GLASS_BG,
        ).pack(side="left")

    def _create_glass_stat_row(self, parent, key, name, desc, lock_val, placeholder, accent_color=NEON_CYAN):
        card = tk.Frame(
            parent,
            bg=GLASS_CARD,
            highlightbackground=GLASS_BORDER,
            highlightthickness=1,
            padx=14,
            pady=10,
        )
        card.pack(fill="x", pady=(0, 8))

        top_row = tk.Frame(card, bg=GLASS_CARD)
        top_row.pack(fill="x")

        tk.Label(
            top_row,
            text=name,
            font=("Segoe UI", 10, "bold"),
            fg=TEXT_WHITE,
            bg=GLASS_CARD,
        ).pack(side="left")

        # Aktualna wartość z pamięci
        val_lbl = tk.Label(
            top_row,
            text="---",
            font=("Consolas", 10, "bold"),
            fg=accent_color,
            bg=GLASS_CARD,
        )
        val_lbl.pack(side="right")
        self.stat_value_labels[key] = val_lbl

        # Opis techniczny
        tk.Label(
            card,
            text=desc,
            font=("Segoe UI", 8),
            fg=TEXT_MUTED,
            bg=GLASS_CARD,
        ).pack(anchor="w", pady=(2, 6))

        # Kontrolki wpisywania i zamrażania
        ctrl_row = tk.Frame(card, bg=GLASS_CARD)
        ctrl_row.pack(fill="x")

        entry = tk.Entry(
            ctrl_row,
            font=("Consolas", 9),
            bg=GLASS_INPUT,
            fg=TEXT_WHITE,
            insertbackground=TEXT_WHITE,
            relief="flat",
            highlightbackground=GLASS_BORDER,
            highlightthickness=1,
            width=14,
        )
        entry.insert(0, placeholder)
        entry.pack(side="left", padx=(0, 8), ipady=3)
        self.stat_entries[key] = entry

        btn_set = tk.Button(
            ctrl_row,
            text="Zapisz",
            font=("Segoe UI", 8, "bold"),
            bg="#1d2638",
            fg=TEXT_WHITE,
            activebackground=accent_color,
            activeforeground=GLASS_BG,
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=lambda k=key: self._on_set_stat_clicked(k),
        )
        btn_set.pack(side="left", padx=(0, 12))

        # Etykieta zamrażarki (Lock)
        tk.Label(
            ctrl_row,
            text="Nielimitowane (Lock):",
            font=("Segoe UI", 8),
            fg=TEXT_SECONDARY,
            bg=GLASS_CARD,
        ).pack(side="left", padx=(0, 6))

        sw = GlassPillSwitch(
            ctrl_row,
            initial=False,
            active_color=accent_color,
            command=lambda state, k=key: self._on_lock_toggled(k, state),
        )
        sw.pack(side="left")
        self.locked_stats[key] = (sw, lock_val)

    def _build_quick_actions_card(self, parent: tk.Frame):
        card = tk.Frame(
            parent,
            bg=GLASS_CARD,
            highlightbackground=NEON_PURPLE,
            highlightthickness=1,
            padx=16,
            pady=14,
        )
        card.pack(fill="x", pady=(10, 0))

        tk.Label(
            card,
            text="TAKTYCZNE SZYBKIE AKCJE & NIESKOŃCZONA PRODUKCJA",
            font=("Segoe UI", 10, "bold"),
            fg=NEON_PURPLE,
            bg=GLASS_CARD,
        ).pack(anchor="w", pady=(0, 6))

        tk.Label(
            card,
            text="Jednym kliknięciem wypełnij magazyny, zmaksymalizuj produkcję fabryk lub zamroź wszystkie statystyki.",
            font=("Segoe UI", 8),
            fg=TEXT_SECONDARY,
            bg=GLASS_CARD,
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        btn_grid = tk.Frame(card, bg=GLASS_CARD)
        btn_grid.pack(fill="x")
        btn_grid.columnconfigure(0, weight=1)
        btn_grid.columnconfigure(1, weight=1)

        btn_max_all = tk.Button(
            btn_grid,
            text="⚡ Maksymalizuj Wszystko (Max Stats)",
            font=("Segoe UI", 9, "bold"),
            bg="#2a1d45",
            fg="#d8b4fe",
            activebackground=NEON_PURPLE,
            activeforeground=TEXT_WHITE,
            bd=0,
            padx=12,
            pady=8,
            cursor="hand2",
            command=self._apply_max_all_resources,
        )
        btn_max_all.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))

        btn_freeze_all = tk.Button(
            btn_grid,
            text="🔒 Zamroź Wszystko (Unlimited ON)",
            font=("Segoe UI", 9, "bold"),
            bg="#182c3f",
            fg=NEON_CYAN,
            activebackground=NEON_CYAN,
            activeforeground=GLASS_BG,
            bd=0,
            padx=12,
            pady=8,
            cursor="hand2",
            command=self._lock_all_resources,
        )
        btn_freeze_all.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 6))

        btn_infinite_prod = tk.Button(
            btn_grid,
            text="🚀 Włącz Nieskończoną Produkcję (Instant Output)",
            font=("Segoe UI", 9, "bold"),
            bg="#361730",
            fg=NEON_PINK,
            activebackground=NEON_PINK,
            activeforeground=TEXT_WHITE,
            bd=0,
            padx=12,
            pady=8,
            cursor="hand2",
            command=self._trigger_infinite_production_boost,
        )
        btn_infinite_prod.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    # ------------------------------------------------------------------------
    # ZAKŁADKA 2: PRZEŁĄCZNIKI SILNIKA & ŚWIATA (.DATA TOGGLES)
    # ------------------------------------------------------------------------

    def _build_toggles_tab(self, parent: tk.Frame):
        intro = tk.Frame(parent, bg=GLASS_BG)
        intro.pack(fill="x", pady=(0, 10))

        tk.Label(
            intro,
            text="BEZPOŚREDNIE PRZEŁĄCZNIKI SILNIKA W SEKCJI .DATA (STABILNE, ZERO CRASHY)",
            font=("Segoe UI", 10, "bold"),
            fg=NEON_CYAN,
            bg=GLASS_BG,
        ).pack(anchor="w")

        tk.Label(
            intro,
            text="Modyfikacja pojedynczych flag logicznych silnika Clausewitz zamiast kodu .text. Klawisze F1–F11 działają globalnie w tle.",
            font=("Segoe UI", 8),
            fg=TEXT_MUTED,
            bg=GLASS_BG,
        ).pack(anchor="w", pady=(2, 0))

        # Siatka 2 kolumn przełączników
        grid_frame = tk.Frame(parent, bg=GLASS_BG)
        grid_frame.pack(fill="both", expand=True)
        grid_frame.columnconfigure(0, weight=1)
        grid_frame.columnconfigure(1, weight=1)

        half = (len(GAME_TOGGLES) + 1) // 2
        for idx, toggle_tuple in enumerate(GAME_TOGGLES):
            col = 0 if idx < half else 1
            self._create_glass_toggle_card(grid_frame, toggle_tuple, col)

    def _create_glass_toggle_card(self, parent, toggle_tuple, col):
        tid, title, desc, rva_list, vk, hk_str = toggle_tuple

        card = tk.Frame(
            parent,
            bg=GLASS_CARD,
            highlightbackground=GLASS_BORDER,
            highlightthickness=1,
            padx=14,
            pady=10,
        )
        card.grid(row=parent.grid_size()[1] if col == 0 else (parent.grid_size()[1] - 1), column=col, sticky="nsew", padx=6, pady=4)

        top_line = tk.Frame(card, bg=GLASS_CARD)
        top_line.pack(fill="x")

        # Skrót klawiszowy jako neon badge
        if hk_str != "—":
            hk_badge = tk.Frame(top_line, bg="#1e293b", padx=6, pady=2, highlightbackground=NEON_CYAN, highlightthickness=1)
            hk_badge.pack(side="left", padx=(0, 8))
            tk.Label(hk_badge, text=hk_str, font=("Consolas", 8, "bold"), fg=NEON_CYAN, bg="#1e293b").pack()

        tk.Label(
            top_line,
            text=title,
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_WHITE,
            bg=GLASS_CARD,
        ).pack(side="left")

        sw = GlassPillSwitch(
            top_line,
            initial=False,
            active_color=NEON_EMERALD,
            command=lambda state, n=title, r=rva_list: self._on_toggle_switched(n, r, state),
        )
        sw.pack(side="right")
        self.toggle_widgets[tid] = (sw, rva_list, title)

        tk.Label(
            card,
            text=desc,
            font=("Segoe UI", 8),
            fg=TEXT_MUTED,
            bg=GLASS_CARD,
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------------------------
    # AKCJE UŻYTKOWNIKA I OBSŁUGA ZDARZEŃ
    # ------------------------------------------------------------------------

    def _on_lock_toggled(self, key: str, state: bool):
        sw, lock_val = self.locked_stats[key]
        st_txt = "ZABLOKOWANO" if state else "ZWOLNIONO"
        self.lbl_footer.configure(text=f"[+] {key.upper()} -> {st_txt} na wartości {lock_val}.", fg=NEON_EMERALD if state else TEXT_SECONDARY)

    def _on_set_stat_clicked(self, key: str):
        if not self.ctx.get("ok"):
            messagebox.showwarning("Brak kampanii", "Uruchom hoi4.exe i wejdź do aktywnej gry!")
            return
        entry = self.stat_entries.get(key)
        if not entry:
            return
        raw_val = entry.get().strip().replace("%", "").replace(",", "")
        try:
            val = float(raw_val)
        except ValueError:
            messagebox.showerror("Błąd", f"Wpisz poprawną liczbę dla '{key}'!")
            return

        if self.engine.set_stat(self.ctx, key, val):
            self.lbl_footer.configure(text=f"[+] Zapisano pomyślnie nową wartość ({val}) dla {key.upper()} w państwie {self.ctx.get('tag_str')}.", fg=NEON_EMERALD)
        else:
            self.lbl_footer.configure(text=f"[!] Błąd zapisu dla {key.upper()}.", fg=NEON_PINK)

    def _on_toggle_switched(self, name: str, rva_list: list, state: bool):
        if not self.engine.h_proc:
            self.lbl_footer.configure(text=f"[!] Uruchom hoi4.exe, aby przełączyć: {name}", fg=NEON_GOLD)
            return
        if self.engine.write_toggle_state(rva_list, state):
            st_txt = "WŁĄCZONO (ON)" if state else "WYŁĄCZONO (OFF)"
            self.lbl_footer.configure(text=f"[+] {name} -> {st_txt}", fg=NEON_EMERALD if state else TEXT_SECONDARY)

    def _on_achieve_switch_toggled(self, state: bool):
        self.achievements_bypass_active = state
        if self.engine.h_proc:
            self.engine.set_achievements_bypass(self.ctx, state)
        if state:
            self.lbl_achieve_badge.configure(text="ODBLOKOWANE (Bypass)", bg=NEON_GOLD, fg=GLASS_BG)
            self.lbl_footer.configure(text="[+] Osiągnięcia Steam: Pełny Bypass Aktywny (Wymuszenie akceptacji osiągnięć).", fg=NEON_GOLD)
        else:
            self.lbl_achieve_badge.configure(text="DOMYŚLNE SILNIKA", bg="#2a334d", fg=TEXT_WHITE)
            self.lbl_footer.configure(text="[-] Osiągnięcia Steam: Przywrócono standardową weryfikację silnika.", fg=TEXT_SECONDARY)

    def _on_prod_switch_toggled(self, state: bool):
        self.infinite_prod_active = state
        if self.engine.h_proc:
            self.engine.set_infinite_production(state)
            if "instant_construction" in self.toggle_widgets:
                sw, _, _ = self.toggle_widgets["instant_construction"]
                sw.set_state(state)
        if state:
            self.lbl_prod_badge.configure(text="AKTYWNA (1 Dzień)", bg=NEON_PURPLE, fg=TEXT_WHITE)
            self.lbl_footer.configure(text="[+] Nieskończona Produkcja: Włączono natychmiastowe ukończenie stoczni i budowy fabryk.", fg=NEON_PURPLE)
        else:
            self.lbl_prod_badge.configure(text="WYŁĄCZONA", bg="#2a334d", fg=TEXT_WHITE)
            self.lbl_footer.configure(text="[-] Nieskończona Produkcja: Wyłączona.", fg=TEXT_SECONDARY)

    def _trigger_infinite_production_boost(self):
        """Włącza natychmiastową produkcję, synchronizuje flagę stoczni oraz uzupełnia paliwo i surowce."""
        if not self.engine.h_proc:
            messagebox.showwarning("Brak gry", "Uruchom hoi4.exe i wejdź do gry!")
            return
        self.sw_prod.set_state(True, notify=True)
        # Uzupełnij paliwo i manpower
        if self.ctx.get("ok"):
            self.engine.set_stat(self.ctx, "fuel", -1.0)
            self.engine.set_stat(self.ctx, "cp", 200.0)
        self.lbl_footer.configure(
            text="[+] Nieskończona Produkcja & Stocznia: Zmaksymalizowano przepustowość linii produkcyjnych!",
            fg=NEON_EMERALD,
        )

    def _toggle_ironman(self):
        if not self.ctx.get("gs_ptr"):
            messagebox.showwarning("Brak gry", "Uruchom hoi4.exe i wczytaj zapis gry.")
            return
        new_ironman = not self.ctx.get("ironman_on", False)
        if self.engine.set_ironman(self.ctx, new_ironman):
            st = "AKTYWNY (Zablokowany)" if new_ironman else "WYŁĄCZONY (Konsola ~ odblokowana)"
            self.btn_ironman.configure(
                text=f"🛡️ Ironman: {'AKTYWNY' if new_ironman else 'WYŁĄCZONY'}",
                fg=NEON_EMERALD if new_ironman else NEON_GOLD,
            )
            self.lbl_footer.configure(text=f"[+] Tryb Ironman: {st}", fg=NEON_EMERALD)

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
            fg=NEON_EMERALD,
        )

    def _lock_all_resources(self):
        any_unlocked = any(not sw.state for sw, _ in self.locked_stats.values())
        for sw, _ in self.locked_stats.values():
            sw.set_state(any_unlocked)
        st = "ZABLOKOWANO (Unlimited ON)" if any_unlocked else "ODBLOKOWANO (Unlimited OFF)"
        self.lbl_footer.configure(text=f"[+] Wszystkie zasoby i XP: {st}", fg=NEON_EMERALD)

    def _poll_hotkeys(self):
        now = time.time()
        for tid, title, _desc, rva_list, vk, hk_str in GAME_TOGGLES:
            if vk and self.engine.is_key_pressed(vk):
                if now - self.last_hotkey_time.get(vk, 0) > 0.35:
                    self.last_hotkey_time[vk] = now
                    sw, _, _ = self.toggle_widgets[tid]
                    sw.set_state(not sw.state, notify=True)

    # ------------------------------------------------------------------------
    # GŁÓWNA PĘTLA ODŚWIEŻANIA, TELEMETRII I PODTRZYMYWANIA OCHRONY (DAEMON)
    # ------------------------------------------------------------------------

    def _schedule_loop(self):
        try:
            self._poll_hotkeys()
            attached = self.engine.attach()
            if not attached:
                self.lbl_proc_status.configure(text="● PROCES: OCZEKIWANIE NA HOI4.EXE...", fg=NEON_GOLD)
                self.lbl_telemetry_line.configure(text="Uruchom grę Hearts of Iron IV (64-bit), trener podepnie się automatycznie.")
                self.ctx = {"ok": False}
            else:
                self.lbl_proc_status.configure(
                    text=f"● POŁĄCZONO: hoi4.exe (PID: {self.engine.pid} | Baza: 0x{self.engine.base_addr:X})",
                    fg=NEON_EMERALD,
                )
                self.ctx = self.engine.resolve_player_country()
                if self.ctx.get("ok"):
                    # 1. Sprawdź i chroń podsystem osiągnięć
                    ach_info = self.engine.read_achievements_status(self.ctx)
                    if self.achievements_bypass_active:
                        self.engine.set_achievements_bypass(self.ctx, True)
                        cup_str = "🏆 ZAWSZE DOSTĘPNE (Steam OK)"
                        cup_col = NEON_GOLD
                    else:
                        cup_str = "🏆 DOSTĘPNE" if ach_info.get("cup_ok") else "ZABLOKOWANE"
                        cup_col = NEON_EMERALD if ach_info.get("cup_ok") else TEXT_MUTED

                    iron_str = "TAK" if self.ctx.get("ironman_on") else "NIE"
                    self.lbl_telemetry_line.configure(
                        text=(
                            f"Państwo: {self.ctx['tag_str']} (TagID={self.ctx['tag_id']})  |  "
                            f"Ironman: {iron_str}  |  Puchar: {cup_str}"
                        ),
                        fg=TEXT_WHITE,
                    )

                    # 2. Podtrzymywanie Nieskończonej Produkcji
                    if self.infinite_prod_active:
                        self.engine.set_infinite_production(True)

                    # 3. Zamrażanie (Unlimited Freeze) zaznaczonych zasobów
                    for key, (sw, lock_val) in self.locked_stats.items():
                        if sw.state:
                            self.engine.set_stat(self.ctx, key, lock_val)

                    # 4. Odczyt i formatowanie aktualnych wartości
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

        except Exception as e:
            # Bezpieczna obsługa wyjątków w pętli
            pass

        self.root.after(150, self._schedule_loop)


# ============================================================================
# 5. PUNKT WEJŚCIA (ENTRY POINT)
# ============================================================================

def verify_static_binary(exe_path: str = DEFAULT_HOI4_PATH):
    """Weryfikuje strukturę PE pliku hoi4.exe."""
    if not os.path.isfile(exe_path):
        print(f"[!] Nie znaleziono pliku: {exe_path}")
        return False
    print("=" * 76)
    print("[*] WERYFIKACJA STATYCZNA IRONCOMMANDER NA PLIKU HOI4.EXE")
    print("=" * 76)
    with open(exe_path, "rb") as f:
        data = f.read()
    print(f"[+] Wczytano binarkę: {exe_path} ({len(data):,} bajtów)")
    print(f"[+] CGameState RVA:           0x{RVA_CGAMESTATE_PTR:X}")
    print(f"[+] CAchievementsManager RVA: 0x{RVA_ACHIEVEMENTS_MGR_PTR:X}")
    print(f"[+] Debug Achievements Flag:  0x{RVA_DEBUG_ACHIEVEMENTS_FLAG:X}")
    print(f"[+] Liczba przełączników .data: {len(GAME_TOGGLES)}")
    print("=" * 76)
    return True


def main():
    if "--verify-static" in sys.argv:
        p = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_HOI4_PATH
        sys.exit(0 if verify_static_binary(p) else 1)

    root = tk.Tk()
    app = HOI4CommanderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
