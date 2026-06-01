from __future__ import annotations
import sys
import re
from collections import namedtuple
import ctypes
import ctypes.wintypes
from loguru import logger
from typing import List, Tuple, Optional

__all__ = [
    "get_current_processes",
    "get_memory_maps",
    "get_executable_path",
    "get_file_version",
    "get_executable_bits",
    "read_memory_string",
    "search_memory",
    "open_process",
    "close_handle",
]

class PROCESS_ENTRY_32(ctypes.Structure):
    """Process entry data structure."""

    # noinspection PyTypeChecker
    _fields_ = [
        ("dwSize", ctypes.wintypes.DWORD),
        ("cntUsage", ctypes.wintypes.DWORD),
        ("th32ProcessID", ctypes.wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.wintypes.ULONG)),
        ("th32ModuleID", ctypes.wintypes.DWORD),
        ("cntThreads", ctypes.wintypes.DWORD),
        ("th32ParentProcessID", ctypes.wintypes.DWORD),
        ("pcPriClassBase", ctypes.wintypes.LONG),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szExeFile", ctypes.c_char * ctypes.wintypes.MAX_PATH)
    ]

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    """Memory basic information data structure."""

    _fields_ = [
        ("BaseAddress", ctypes.wintypes.LPVOID),
        ("AllocationBase", ctypes.wintypes.LPVOID),
        ("AllocationProtect", ctypes.wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.wintypes.DWORD),
        ("Protect", ctypes.wintypes.DWORD),
        ("Type", ctypes.wintypes.DWORD)
    ]

MemoryMapping = namedtuple(
    typename="MemoryMapping",
    field_names=["BaseAddress", "RegionSize", "State", "Protect", "Type", "FileName"],
)

class VS_FIXED_FILEINFO(ctypes.Structure):
    """File version information data structure."""

    _fields_ = [
        ('dwSignature', ctypes.wintypes.DWORD),
        ('dwStrucVersion', ctypes.wintypes.DWORD),
        ('dwFileVersionMS', ctypes.wintypes.DWORD),
        ('dwFileVersionLS', ctypes.wintypes.DWORD),
        ('dwProductVersionMS', ctypes.wintypes.DWORD),
        ('dwProductVersionLS', ctypes.wintypes.DWORD),
        ('dwFileFlagsMask', ctypes.wintypes.DWORD),
        ('dwFileFlags', ctypes.wintypes.DWORD),
        ('dwFileOS', ctypes.wintypes.DWORD),
        ('dwFileType', ctypes.wintypes.DWORD),
        ('dwFileSubtype', ctypes.wintypes.DWORD),
        ('dwFileDateMS', ctypes.wintypes.DWORD),
        ('dwFileDateLS', ctypes.wintypes.DWORD),
    ]

kernel32 = ctypes.WinDLL(name="kernel32", use_last_error=True)

# create the current processes snapshot
create_snapshot = kernel32.CreateToolhelp32Snapshot
create_snapshot.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
create_snapshot.restype = ctypes.wintypes.HANDLE

# acquire the first process snapshot
first_process = kernel32.Process32First
first_process.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(PROCESS_ENTRY_32)]
first_process.restype = ctypes.wintypes.BOOL

# acquire the next process snapshot
next_process = kernel32.Process32Next
next_process.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(PROCESS_ENTRY_32)]
next_process.restype = ctypes.wintypes.BOOL

# close handle
close_handle = kernel32.CloseHandle
close_handle.argtypes = [ctypes.wintypes.HANDLE]
close_handle.restype = ctypes.wintypes.BOOL

# read a process memory information
read_memory = kernel32.ReadProcessMemory

# open a running process by its id
open_process = kernel32.OpenProcess
open_process.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
open_process.restype = ctypes.wintypes.HANDLE

# query the memory address information of a process's virtual address space
query_addr = kernel32.VirtualQueryEx
query_addr.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.LPCVOID, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
query_addr.restype = ctypes.c_size_t

psapi = ctypes.WinDLL(name="psapi", use_last_error=True)

# checks whether the specified address is within a memory-mapped file in the address space
# of the specified process. If so, the function returns the name of the memory-mapped file.
get_mapped_filename = psapi.GetMappedFileNameW
get_mapped_filename.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_void_p, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD]
get_mapped_filename.restype = ctypes.wintypes.DWORD

get_module_filename = psapi.GetModuleFileNameExA
get_module_filename.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.HANDLE, ctypes.c_char_p, ctypes.wintypes.DWORD]
get_module_filename.restype = ctypes.wintypes.DWORD

version = ctypes.WinDLL(name="version", use_last_error=True)

# acquire the size of file version information
get_file_version_info_size = version.GetFileVersionInfoSizeW
get_file_version_info_size.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.POINTER(ctypes.wintypes.DWORD)]
get_file_version_info_size.restype = ctypes.wintypes.DWORD

# retrieves version information for the specified file
get_file_info = version.GetFileVersionInfoW
get_file_info.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.c_void_p]
get_file_info.restype = ctypes.wintypes.BOOL

# retrieves specified version information from the specified version-information resource
query_version = version.VerQueryValueW
query_version.argtypes = [ctypes.c_void_p, ctypes.wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.wintypes.UINT)]
query_version.restype = ctypes.wintypes.BOOL

def get_current_processes() -> List[Tuple[int, List[str] | str]]:
    """Returns snapshot information (including pid, and executable filename) of all currently
    running processes.

    :return: List of processes snapshot information.
    """
    snapshot = create_snapshot(0x00000002, 0)
    if snapshot == ctypes.wintypes.HANDLE(-1).value:
        logger.error("Failed to create a process snapshot.")
        return []

    pe32 = PROCESS_ENTRY_32()
    pe32.dwSize = ctypes.sizeof(PROCESS_ENTRY_32)

    if not first_process(snapshot, ctypes.byref(pe32, 0)):
        logger.error("Filed to get the first process snapshot.")
        close_handle(snapshot)
        return []

    processes = []
    while True:
        processes.append((
            pe32.th32ProcessID,  # process id
            pe32.szExeFile.decode("utf-8", errors="ignore"),  # executable filename
        ))
        if not next_process(snapshot, ctypes.byref(pe32)):
            close_handle(snapshot)
            return processes

def read_memory_string(
    process,
    address: int,
    size: int = 64,
    indirect: bool = False,
    addr_len: int = 8,
    encoding: Optional[str] = "utf-8",
) -> Optional[str | bytes]:
    """Read a string from the memory via the given address and size.

    :param process: The process for which to read.
    :param address: The starting address to read from memory.
    :param size: The total byte size to read (default: 64).
    :param indirect: Whether the data is indirect address (default: False).
    :param addr_len: The length of the address when indirect is True (default: 8).
    :param encoding: Whether to decode the data by the given encoding (default: utf-8).
    :return: A string or bytes read from the memory if successful, otherwise None.
    """
    size_0 = addr_len if indirect else size
    buffer = ctypes.create_string_buffer(size_0)
    if read_memory(process, ctypes.c_void_p(address), buffer, size_0, 0) == 0:
        return None

    if indirect:
        address = int.from_bytes(buffer, byteorder="little")
        return read_memory_string(process, address, size, indirect=False, encoding=encoding)

    buffer = bytes(buffer)
    if encoding is not None:
        if b"\x00" in buffer:
            buffer = buffer.split(b"\x00")[0]
        string = buffer.decode(encoding=encoding, errors="ignore")
        return string.strip() if string.strip() != "" else None
    return buffer

def get_memory_maps(process) -> List[MemoryMapping]:
    """Return the memory mapping information of the given process.

    :param process: A running process.
    :return: List of process memory mappings.
    """
    memory_maps = []
    mbi = MEMORY_BASIC_INFORMATION()
    base_address, max_address = 0, 0x7FFFFFFFFFFFFFFF
    while base_address < max_address:
        if query_addr(process, base_address, ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break

        filename = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
        if get_mapped_filename(process, base_address, filename, ctypes.wintypes.MAX_PATH) > 0:
            filename = filename.value

        memory_maps.append(MemoryMapping(**{
            "BaseAddress": mbi.BaseAddress,
            "RegionSize": mbi.RegionSize,
            "State": mbi.State,
            "Protect": mbi.Protect,
            "Type": mbi.Type,
            "FileName": filename
        }))
        base_address += mbi.RegionSize

    return memory_maps

def get_executable_path(process) -> Optional[str]:
    """Returns the executable path of the given running process by its id.

    :param process: A running process.
    :return: the executable path string if successful, None otherwise.
    """
    path = ctypes.create_string_buffer(ctypes.wintypes.MAX_PATH)
    if get_module_filename(process, None, path, ctypes.wintypes.MAX_PATH) > 0:
        return path.value.decode("utf-8", errors="ignore")
    return None

def get_file_version(path: str) -> Optional[str]:
    """Returns the version information of the given executable file.

    :param path: A path to the executable file.
    :return: the version information string if successful, None otherwise.
    """
    if (size := get_file_version_info_size(path, None)) == 0:
        return None

    version = ctypes.create_string_buffer(size)
    if not get_file_info(path, 0, size, version):
        return None

    buffer = ctypes.c_void_p()
    if not query_version(version, r"\\", ctypes.byref(buffer), ctypes.byref(ctypes.wintypes.UINT())):
        return None

    ffi = ctypes.cast(buffer, ctypes.POINTER(VS_FIXED_FILEINFO)).contents
    if ffi.dwSignature != 0xFEEF04BD:
        return None

    return ".".join([str(e) for e in (
        (ffi.dwFileVersionMS >> 16) & 0xffff,
        ffi.dwFileVersionMS & 0xffff,
        (ffi.dwFileVersionLS >> 16) & 0xffff,
        ffi.dwFileVersionLS & 0xffff,
    )])

def get_executable_bits(path: str) -> int:
    """Retrieves the bits of the given executable file.

    :param path: A path to the executable file.
    :return: the bits number of the executable file.
    """
    try:
        with open(path, "rb") as fp:
            if fp.read(2) != b"MZ":
                raise ValueError(f"{path} is not a valid executable file")

            # seek to the offset of the PE signature
            fp.seek(60)
            pe_offset_bytes = fp.read(4)
            pe_offset = int.from_bytes(pe_offset_bytes, byteorder="little")

            # seek to the machine field in the PE header
            fp.seek(pe_offset + 4)
            machine_bytes = fp.read(2)
            machine_bytes = int.from_bytes(machine_bytes, byteorder="little")

            if machine_bytes == 0x14c:
                return 32
            elif machine_bytes == 0x8664:
                return 64
            else:
                raise ValueError(f"Unknown architecture: {hex(machine_bytes)}")
    except IOError as e:
        raise e

def search_memory(
    process,
    pattern: bytes,
    begin_addr: int = 0,
    end_addr: int = 0x7FFFFFFFFFFFFFFF,
    max_search: int = 100
) -> List[int]:
    """Retrieve the matching string in the specified memory range.

    :param process: A process for searching.
    :param pattern: A pattern to match against the memory data.
    :param begin_addr: The starting address of the memory range.
    :param end_addr: The ending address of the memory range.
    :param max_search: The maximum number of matches to return (default: 100).
    :return: A List of matching starting address.
    """
    out = []
    mbi = MEMORY_BASIC_INFORMATION()
    pattern = re.compile(pattern)

    addr = begin_addr
    max_addr = end_addr if sys.maxsize > 2 ** 32 else 0x7fff0000
    while addr < max_addr:
        if query_addr(process, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break

        allowed_protections = [0x10, 0x20, 0x40, 0x04, 0x02]
        if mbi.State != 0x1000 or mbi.Protect not in allowed_protections:
            addr += mbi.RegionSize
            continue

        base_addr = ctypes.c_ulonglong(mbi.BaseAddress)
        size = ctypes.c_size_t(mbi.RegionSize)
        buffer = ctypes.create_string_buffer(mbi.RegionSize)
        bytes_read = ctypes.c_size_t()
        if read_memory(process, base_addr, buffer, size, ctypes.byref(bytes_read)) == 0:
            addr += mbi.RegionSize
            continue

        find = [addr + e.start() for e in pattern.finditer(buffer, re.DOTALL)]
        if find: out.extend(find)
        if len(out) >= max_search: break
        addr += mbi.RegionSize
    return out
