#!/usr/bin/env python3
"""
HOI4 Political Power RE Companion & Single-Player Trainer
=========================================================
Projekt edukacyjny Reverse Engineeringu (Hearts of Iron IV, 64-bit Clausewitz Engine).
Wyłącznie do kampanii single-player.

Mechanizm działania:
1. Odnajduje proces `hoi4.exe` i pobiera dynamiczny adres bazowy modułu (omijając ASLR).
2. Skanuje sekcję wykonywalną `.text` w pamięci procesu za pomocą sygnatur AOB (Array of Bytes)
   i wylicza z instrukcji `RIP-relative` (LEA / MOV) aktualne statyczne RVA singletona `CGameState`.
3. Rozwiązuje wielopoziomowy łańcuch wskaźników (Pointer Path):
   - CGameState*         = *(uint64_t*)(hoi4.exe + RVA_CGameState)
   - PlayerCountryID     = *(int32_t*)(CGameState + 0x520)  [lub +0x524]
   - CountryIndex        = *(int32_t*)(*(uint64_t*)(CGameState + 0x340) + PlayerCountryID * 4)
   - CCountry*           = *(uint64_t*)(*(uint64_t*)(CGameState + 0x310) + CountryIndex * 8)
   - CPoliticalStatus*   = *(uint64_t*)(CCountry + 0xF90)
   - PoliticalPowerAddr  = CPoliticalStatus + 0xE0
4. Odczytuje i zapisuje 64-bitową wartość stałoprzecinkową (CFixedPoint64, skalar 100 000 = 0x186A0).
"""

import ctypes
import ctypes.wintypes as wt
import struct
import sys
import os
import argparse

# Stałe Win32 API
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

FIXED_POINT_SCALE = 100_000  # 0x186A0 w silniku Clausewitz (1.0 PP = 100 000 jednostek)

# Domyślne wartości wyprowadzone statycznie z hoi4.exe (SHA256: b6650c85...6bd6d888)
DEFAULT_IMAGE_BASE = 0x140000000
DEFAULT_RVA_CGAMESTATE = 0x33DD6B0
DEFAULT_RVA_ADD_PP_WRITE = 0xBFA732
DEFAULT_RVA_CHANGE_PP_WRITE = 0xBFA84A

# Sygnatury AOB (z obsługą wildcardów '??')
# 1. CPoliticalStatus::AddPoliticalPower (zapis + clamp do POLITICAL_POWER_LOWER_CAP / UPPER_CAP)
AOB_ADD_PP = (
    "48 8B 97 E0 00 00 00 "     # mov rdx, [rdi + 0xE0]
    "48 03 D3 "                 # add rdx, rbx
    "48 8B 5C 24 40 "           # mov rbx, [rsp + 0x40]
    "48 89 97 E0 00 00 00 "     # mov [rdi + 0xE0], rdx  <-- INSTRUKCJA ZAPISUJĄCA (+0x0F od początku wzorca)
    "48 8B 0D ?? ?? ?? ?? "     # mov rcx, [rip + disp32] (POLITICAL_POWER_LOWER_CAP)
    "48 8B 05 ?? ?? ?? ??"      # mov rax, [rip + disp32] (POLITICAL_POWER_UPPER_CAP)
)
AOB_ADD_PP_WRITE_OFFSET = 0x0F  # Offset instrukcji 48 89 97 E0 00 00 00 wewnątrz wzorca

# 2. CPoliticalStatus::ChangePoliticalPower (druga funkcja zapisująca z mnożnikiem dziennym)
AOB_CHANGE_PP = (
    "48 8B 87 E0 00 00 00 "     # mov rax, [rdi + 0xE0]
    "48 8D 0C 18 "              # lea rcx, [rax + rbx]
    "48 8B 5C 24 40 "           # mov rbx, [rsp + 0x40]
    "48 85 C0 "                 # test rax, rax
    "78 09 "                    # js +9
    "48 85 C9 "                 # test rcx, rcx
    "79 0E "                    # jns +14
    "33 C9 "                    # xor ecx, ecx
    "EB 19 "                    # jmp +25
    "48 3B C8 "                 # cmp rcx, rax
    "7D 05 "                    # jge +5
    "48 8B C8 "                 # mov rcx, rax
    "EB 0F "                    # jmp +15
    "48 3B 0D ?? ?? ?? ?? "     # cmp rcx, [rip + disp32]
    "48 0F 4F 0D ?? ?? ?? ?? "  # cmovg rcx, [rip + disp32]
    "48 89 8F E0 00 00 00"      # mov [rdi + 0xE0], rcx  <-- INSTRUKCJA ZAPISUJĄCA (+0x37 od początku wzorca)
)
AOB_CHANGE_PP_WRITE_OFFSET = 0x37

# 3. Wyłuskiwanie wskaźnika globalnego CGameState oraz aktywnego państwa gracza (+0x520 / +0x524)
AOB_PLAYER_RESOLVER = (
    "48 8B 05 ?? ?? ?? ?? "     # mov rax, [rip + disp32_CGameState]
    "83 B8 20 05 00 00 00 "     # cmp dword ptr [rax + 0x520], 0
    "B9 24 05 00 00 "           # mov ecx, 0x524
    "BA 20 05 00 00 "           # mov edx, 0x520
    "0F 4F CA "                 # cmovg ecx, edx
    "48 03 C8"                  # add rcx, rax
)


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
        ("modBaseAddr", ctypes.POINTER(wt.BYTE)),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", wt.WCHAR * 256),
        ("szExePath", wt.WCHAR * 260),
    ]


def parse_aob(pattern_str: str):
    """Konwertuje tekstowy wzorzec AOB (np. '48 8B ?? 00') na listę bajtów/None."""
    tokens = pattern_str.strip().split()
    pattern = []
    for tok in tokens:
        if tok in ("?", "??"):
            pattern.append(None)
        else:
            pattern.append(int(tok, 16))
    return pattern


def scan_aob(buffer: bytes, pattern_str: str) -> list[int]:
    """Skanuje bufor bajtów pod kątem wzorca AOB z wildcardami (zoptymalizowane wyszukiwanie)."""
    pat = parse_aob(pattern_str)
    n = len(pat)
    if n == 0 or len(buffer) < n:
        return []

    # Znajdź najdłuższy stały prefiks/podciąg bez wildcardów jako kotwicę (anchor)
    best_start, best_bytes = 0, b""
    cur_start, cur_bytes = 0, bytearray()
    for i, b in enumerate(pat):
        if b is not None:
            if not cur_bytes:
                cur_start = i
            cur_bytes.append(b)
            if len(cur_bytes) > len(best_bytes):
                best_start, best_bytes = cur_start, bytes(cur_bytes)
        else:
            cur_bytes = bytearray()

    matches = []
    search_pos = 0
    limit = len(buffer) - n
    while search_pos <= limit:
        idx = buffer.find(best_bytes, search_pos + best_start)
        if idx == -1:
            break
        cand = idx - best_start
        if cand > limit:
            break
        # Weryfikuj pełny wzorzec z wildcardami
        ok = True
        for j, pb in enumerate(pat):
            if pb is not None and buffer[cand + j] != pb:
                ok = False
                break
        if ok:
            matches.append(cand)
        search_pos = cand + 1
    return matches


class Win32ProcessMemory:
    """Klasa pomocnicza do odczytu/zapisu pamięci procesu hoi4.exe przez Win32 API."""

    def __init__(self, process_name: str = "hoi4.exe"):
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.process_name = process_name
        self.pid = None
        self.h_process = None
        self.module_base = None
        self.module_size = None

    def attach(self) -> bool:
        self.pid = self._find_pid(self.process_name)
        if not self.pid:
            return False
        self.module_base, self.module_size = self._find_module(self.pid, self.process_name)
        if not self.module_base:
            return False
        access = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION
        self.h_process = self.kernel32.OpenProcess(access, False, self.pid)
        return bool(self.h_process)

    def close(self):
        if self.h_process:
            self.kernel32.CloseHandle(self.h_process)
            self.h_process = None

    def _find_pid(self, exe_name: str):
        snap = self.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == wt.HANDLE(-1).value:
            return None
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not self.kernel32.Process32FirstW(snap, ctypes.byref(entry)):
                return None
            while True:
                if entry.szExeFile.lower() == exe_name.lower():
                    return entry.th32ProcessID
                if not self.kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        finally:
            self.kernel32.CloseHandle(snap)
        return None

    def _find_module(self, pid: int, mod_name: str):
        snap = self.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
        if snap == wt.HANDLE(-1).value:
            return None, None
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
            if not self.kernel32.Module32FirstW(snap, ctypes.byref(entry)):
                return None, None
            while True:
                if entry.szModule.lower() == mod_name.lower():
                    base_addr = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
                    return base_addr, entry.modBaseSize
                if not self.kernel32.Module32NextW(snap, ctypes.byref(entry)):
                    break
        finally:
            self.kernel32.CloseHandle(snap)
        return None, None

    def read_bytes(self, address: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t(0)
        ok = self.kernel32.ReadProcessMemory(
            self.h_process,
            ctypes.c_void_p(address),
            buf,
            size,
            ctypes.byref(bytes_read),
        )
        if not ok or bytes_read.value != size:
            raise OSError(f"ReadProcessMemory nie powiodło się pod adresem 0x{address:X}")
        return buf.raw

    def write_bytes(self, address: int, data: bytes) -> bool:
        bytes_written = ctypes.c_size_t(0)
        ok = self.kernel32.WriteProcessMemory(
            self.h_process,
            ctypes.c_void_p(address),
            data,
            len(data),
            ctypes.byref(bytes_written),
        )
        return bool(ok and bytes_written.value == len(data))

    def read_u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read_bytes(address, 8))[0]

    def read_i64(self, address: int) -> int:
        return struct.unpack("<q", self.read_bytes(address, 8))[0]

    def read_i32(self, address: int) -> int:
        return struct.unpack("<i", self.read_bytes(address, 4))[0]

    def write_i64(self, address: int, value: int) -> bool:
        return self.write_bytes(address, struct.pack("<q", value))


def read_Clausewitz_string(mem: Win32ProcessMemory, str_struct_addr: int) -> str:
    """Odczytuje strukturę std::string (MSVC x64 Small String Optimization) spod podanego adresu."""
    raw = mem.read_bytes(str_struct_addr, 32)
    size, capacity = struct.unpack_from("<QQ", raw, 16)
    if size == 0 or size > 64:
        return ""
    if capacity <= 15:
        return raw[:size].decode("ascii", errors="replace")
    str_ptr = struct.unpack_from("<Q", raw, 0)[0]
    if not str_ptr:
        return ""
    return mem.read_bytes(str_ptr, size).decode("ascii", errors="replace")


def resolve_player_political_power(mem: Win32ProcessMemory, rva_cgamestate: int = DEFAULT_RVA_CGAMESTATE):
    """
    Rozwiązuje pełny wielopoziomowy wskaźnik do Political Power gracza i zwraca słownik diagnostyczny.
    """
    info = {
        "module_base": mem.module_base,
        "rva_cgamestate": rva_cgamestate,
        "static_va_cgamestate": DEFAULT_IMAGE_BASE + rva_cgamestate,
        "runtime_ptr_cgamestate": mem.module_base + rva_cgamestate,
    }

    # Krok 1: Odczyt globalnego wskaźnika CGameState* z sekcji .data
    c_gamestate = mem.read_u64(info["runtime_ptr_cgamestate"])
    info["c_gamestate"] = c_gamestate
    if not c_gamestate:
        raise ValueError("Singleton CGameState* ma wartość NULL (gra jest w menu głównym lub jeszcze się ładuje).")

    # Krok 2: Odczyt tagu państwa gracza (PlayerCountryID: +0x520 lub +0x524)
    tag_primary = mem.read_i32(c_gamestate + 0x520)
    tag_fallback = mem.read_i32(c_gamestate + 0x524)
    player_tag_id = tag_primary if tag_primary > 0 else tag_fallback
    info["player_tag_id"] = player_tag_id
    info["player_tag_offset"] = 0x520 if tag_primary > 0 else 0x524
    if player_tag_id <= 0:
        raise ValueError("Nie wybrano państwa gracza (PlayerCountryID == 0). Wczytaj kampanię single-player.")

    # Krok 3: Odszukanie indeksu w tablicy przekierowań Tag -> Index (lub bezpośredni indeks)
    tag_map_ptr = mem.read_u64(c_gamestate + 0x340)
    info["tag_map_ptr"] = tag_map_ptr
    if tag_map_ptr:
        country_index = mem.read_i32(tag_map_ptr + player_tag_id * 4)
    else:
        country_index = player_tag_id
    info["country_index"] = country_index

    # Krok 4: Odczyt tablicy wskaźników CCountry* pod CGameState + 0x310
    countries_array_ptr = mem.read_u64(c_gamestate + 0x310)
    info["countries_array_ptr"] = countries_array_ptr
    if not countries_array_ptr:
        raise ValueError("Tablica państw (CGameState + 0x310) jest pusta (NULL).")

    c_country = mem.read_u64(countries_array_ptr + country_index * 8)
    info["c_country"] = c_country
    if not c_country:
        raise ValueError(f"Obiekt CCountry* dla indeksu {country_index} ma wartość NULL.")

    # Odczyt nazwy 3-literowego tagu państwa (np. GER, POL, USA) z CGameState + 0x358
    try:
        tags_names_array = mem.read_u64(c_gamestate + 0x358)
        if tags_names_array:
            info["country_tag_str"] = read_Clausewitz_string(mem, tags_names_array + player_tag_id * 32)
        else:
            info["country_tag_str"] = "???"
    except Exception:
        info["country_tag_str"] = "???"

    # Krok 5: Odczyt wskaźnika CPoliticalStatus* pod CCountry + 0xF90
    c_politics = mem.read_u64(c_country + 0xF90)
    info["c_politics"] = c_politics
    if not c_politics:
        raise ValueError("Wskaźnik CPoliticalStatus* (CCountry + 0xF90) ma wartość NULL.")

    # Krok 6: Adres pola Political Power (CPoliticalStatus + 0xE0)
    pp_address = c_politics + 0xE0
    raw_pp = mem.read_i64(pp_address)
    info["pp_address"] = pp_address
    info["raw_pp"] = raw_pp
    info["pp_value"] = raw_pp / FIXED_POINT_SCALE
    return info


def run_offline_verification(exe_path: str):
    """Weryfikuje sygnatury AOB i RVA bezpośrednio na pliku dyskowym hoi4.exe."""
    print("=" * 72)
    print(" [TRYB OFFLINE] Weryfikacja statyczna binarki hoi4.exe oraz wzorców AOB")
    print("=" * 72)
    if not os.path.isfile(exe_path):
        print(f"[!] Nie znaleziono pliku: {exe_path}")
        return False

    with open(exe_path, "rb") as f:
        data = f.read()

    # Parsowanie nagłówka PE
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    num_sec = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe_off + 20)[0]
    image_base = struct.unpack_from("<Q", data, pe_off + 24 + 24)[0]

    sec_table = pe_off + 24 + opt_size
    text_rva, text_vsize, text_raw_ptr, text_raw_size = 0, 0, 0, 0
    for i in range(num_sec):
        s_off = sec_table + i * 40
        name = data[s_off : s_off + 8].rstrip(b"\x00").decode("ascii", errors="replace")
        vsize, rva, rsize, rptr = struct.unpack_from("<IIII", data, s_off + 8)
        if name == ".text":
            text_rva, text_vsize, text_raw_ptr, text_raw_size = rva, vsize, rptr, rsize
            break

    text_bytes = data[text_raw_ptr : text_raw_ptr + min(text_vsize, text_raw_size)]
    print(f"[+] Plik:           {exe_path} ({len(data):,} bajtów)")
    print(f"[+] PE ImageBase:   0x{image_base:X}")
    print(f"[+] Sekcja .text:   RVA=0x{text_rva:X}, Rozmiar=0x{text_vsize:X}")

    # Test 1: AOB_ADD_PP
    hits_add = scan_aob(text_bytes, AOB_ADD_PP)
    print(f"\n[1] Skan AOB: CPoliticalStatus::AddPoliticalPower")
    print(f"    Wzorzec:  {AOB_ADD_PP}")
    print(f"    Trafień:  {len(hits_add)}")
    for h in hits_add:
        fn_rva = text_rva + h
        write_rva = fn_rva + AOB_ADD_PP_WRITE_OFFSET
        print(f"    -> Blok RVA:              0x{fn_rva:X} (Static VA: 0x{image_base + fn_rva:X})")
        print(f"    -> Instrukcja zapisująca: RVA=0x{write_rva:X} (Static VA: 0x{image_base + write_rva:X})")
        print(f"       mov [rdi+0xE0], rdx    (Bajtowo: {text_bytes[h+AOB_ADD_PP_WRITE_OFFSET:h+AOB_ADD_PP_WRITE_OFFSET+7].hex(' ').upper()})")

    # Test 2: AOB_CHANGE_PP
    hits_chg = scan_aob(text_bytes, AOB_CHANGE_PP)
    print(f"\n[2] Skan AOB: CPoliticalStatus::ChangePoliticalPower")
    print(f"    Wzorzec:  {AOB_CHANGE_PP}")
    print(f"    Trafień:  {len(hits_chg)}")
    for h in hits_chg:
        fn_rva = text_rva + h
        write_rva = fn_rva + AOB_CHANGE_PP_WRITE_OFFSET
        print(f"    -> Blok RVA:              0x{fn_rva:X} (Static VA: 0x{image_base + fn_rva:X})")
        print(f"    -> Instrukcja zapisująca: RVA=0x{write_rva:X} (Static VA: 0x{image_base + write_rva:X})")
        print(f"       mov [rdi+0xE0], rcx    (Bajtowo: {text_bytes[h+AOB_CHANGE_PP_WRITE_OFFSET:h+AOB_CHANGE_PP_WRITE_OFFSET+7].hex(' ').upper()})")

    # Test 3: AOB_PLAYER_RESOLVER (wyznaczenie RVA singletona CGameState z instrukcji RIP-relative)
    hits_res = scan_aob(text_bytes, AOB_PLAYER_RESOLVER)
    print(f"\n[3] Skan AOB: Wyłuskanie globalnego wskaźnika CGameState* (RIP-relative)")
    print(f"    Wzorzec:  {AOB_PLAYER_RESOLVER}")
    print(f"    Trafień:  {len(hits_res)}")
    if hits_res:
        h0 = hits_res[0]
        instr_rva = text_rva + h0
        disp32 = struct.unpack_from("<i", text_bytes, h0 + 3)[0]
        cgamestate_rva = instr_rva + 7 + disp32
        print(f"    -> Pierwsze trafienie RVA: 0x{instr_rva:X} (mov rax, [rip + 0x{disp32:X}])")
        print(f"    -> Wyliczone RVA CGameState*: 0x{cgamestate_rva:X} (Static VA: 0x{image_base + cgamestate_rva:X})")

    print("\n" + "=" * 72)
    print(" Podsumowanie Pointer Path (Cheat Engine / Trainer):")
    print('   Baza:    "hoi4.exe" + 0x33DD6B0  (wskaźnik na CGameState)')
    print("   Krok 1:  Odczytaj PlayerTagID = *(int32*)(CGameState + 0x520)")
    print("   Krok 2:  Odczytaj CountryIdx  = *(int32*)(*(uint64*)(CGameState + 0x340) + PlayerTagID * 4)")
    print("   Krok 3:  Odczytaj CCountry*   = *(uint64*)(*(uint64*)(CGameState + 0x310) + CountryIdx * 8)")
    print("   Krok 4:  Odczytaj CPolitics*  = *(uint64*)(CCountry + 0xF90)")
    print("   Krok 5:  Political Power      = *(int64*)(CPolitics + 0xE0)  [Skala: 1 PP = 100 000]")
    print("   (Gdy w CCountry[0] siedzi państwo gracza lub używamy bezpośredniego slotu:")
    print('    łańcuch CE dla slotu 0 to: ["hoi4.exe"+0x33DD6B0] -> +0x310 -> +0x0 -> +0xF90 -> +0xE0)')
    print("=" * 72)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="HOI4 Political Power RE Companion & Single-Player Trainer (Edukacyjny)"
    )
    parser.add_argument(
        "--exe",
        default=r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV\hoi4.exe",
        help="Ścieżka do pliku hoi4.exe do analizy statycznej AOB",
    )
    parser.add_argument(
        "--verify-static",
        action="store_true",
        help="Tylko zweryfikuj sygnatury AOB i RVA na pliku hoi4.exe (bez gry w tle)",
    )
    parser.add_argument(
        "--get",
        action="store_true",
        help="Odczytaj aktualną wartość Political Power z uruchomionej gry hoi4.exe",
    )
    parser.add_argument(
        "--set",
        type=float,
        metavar="PP_VALUE",
        help="Ustaw nową wartość Political Power (np. --set 500.0)",
    )
    parser.add_argument(
        "--add",
        type=float,
        metavar="PP_DELTA",
        help="Dodaj określoną liczbę Political Power (np. --add 150.0)",
    )
    args = parser.parse_args()

    if args.verify_static:
        run_offline_verification(args.exe)
        return

    # Sprawdzamy, czy proces hoi4.exe jest uruchomiony
    mem = Win32ProcessMemory("hoi4.exe")
    if not mem.attach():
        print("[i] Proces hoi4.exe nie jest obecnie uruchomiony w tle.")
        print("[i] Uruchamiam weryfikację statyczną AOB na pliku wykonywalnym...\n")
        run_offline_verification(args.exe)
        print("\nAby użyć trybu Live Trainer (Single-Player):")
        print("  1. Uruchom Hearts of Iron IV i wczytaj dowolną kampanię jednoosobową.")
        print("  2. Uruchom ten skrypt ponownie:")
        print("       python hoi4_pp_trainer.py --get")
        print("       python hoi4_pp_trainer.py --set 1000")
        print("       python hoi4_pp_trainer.py --add 250")
        return

    try:
        print("=" * 72)
        print(f" [TRYB LIVE] Połączono z hoi4.exe (PID: {mem.pid})")
        print(f" Dynamiczna baza modułu (ASLR): 0x{mem.module_base:X}")
        print("=" * 72)

        # Dynamiczny skan AOB w pamięci procesu, aby potwierdzić RVA CGameState (odporność na patche)
        rva_cgamestate = DEFAULT_RVA_CGAMESTATE
        try:
            # Skanujemy pierwsze 40 MB modułu (.text)
            text_dump = mem.read_bytes(mem.module_base + 0x1000, 0x2783848)
            hits = scan_aob(text_dump, AOB_PLAYER_RESOLVER)
            if hits:
                h0 = hits[0]
                instr_rva = 0x1000 + h0
                disp32 = struct.unpack_from("<i", text_dump, h0 + 3)[0]
                rva_cgamestate = instr_rva + 7 + disp32
                print(f"[+] AOB Resolver znalazł CGameState RVA: 0x{rva_cgamestate:X}")
        except Exception as e:
            print(f"[!] Ostrzeżenie AOB (używam domyślnego RVA 0x{rva_cgamestate:X}): {e}")

        info = resolve_player_political_power(mem, rva_cgamestate)
        print(f"[+] CGameState*         : 0x{info['c_gamestate']:X} (z hoi4.exe+0x{info['rva_cgamestate']:X})")
        print(f"[+] Państwo gracza      : Tag ID = {info['player_tag_id']} ({info['country_tag_str']}), Indeks = {info['country_index']}")
        print(f"[+] CCountry*           : 0x{info['c_country']:X}")
        print(f"[+] CPoliticalStatus*   : 0x{info['c_politics']:X} (CCountry + 0xF90)")
        print(f"[+] Adres PoliticalPower: 0x{info['pp_address']:X} (CPoliticalStatus + 0xE0)")
        print(f"[+] Aktualna wartość PP : {info['pp_value']:.2f} (surowe CFixedPoint64: {info['raw_pp']})")

        if args.set is not None:
            new_raw = int(round(args.set * FIXED_POINT_SCALE))
            if mem.write_i64(info["pp_address"], new_raw):
                print(f"[✓] Ustawiono Political Power na: {args.set:.2f} (zapisano {new_raw} pod 0x{info['pp_address']:X})")
            else:
                print("[!] Błąd zapisu pamięci (WriteProcessMemory)!")

        elif args.add is not None:
            new_val = info["pp_value"] + args.add
            new_raw = int(round(new_val * FIXED_POINT_SCALE))
            if mem.write_i64(info["pp_address"], new_raw):
                print(f"[✓] Dodano {args.add:+.2f} PP -> Nowa wartość: {new_val:.2f} (zapisano {new_raw})")
            else:
                print("[!] Błąd zapisu pamięci (WriteProcessMemory)!")

        elif not args.get:
            # Tryb interaktywny, jeśli uruchomiono bez flag
            print("\nPodaj nową wartość Political Power (lub naciśnij Enter, aby wyjść):")
            user_in = input("Nowe PP > ").strip()
            if user_in:
                target_pp = float(user_in.replace(",", "."))
                new_raw = int(round(target_pp * FIXED_POINT_SCALE))
                if mem.write_i64(info["pp_address"], new_raw):
                    print(f"[✓] Zaktualizowano Political Power na {target_pp:.2f}!")
                else:
                    print("[!] Nie udało się zapisać nowej wartości.")

    except Exception as exc:
        print(f"\n[!] Błąd: {exc}")
    finally:
        mem.close()


if __name__ == "__main__":
    main()
