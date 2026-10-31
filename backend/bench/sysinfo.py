"""Cross-platform RAM/CPU-time probing, stdlib only (ctypes + /proc).

No third-party dependency (e.g. psutil) is used here on purpose: Windows
exposes everything needed via GlobalMemoryStatusEx/GetProcessMemoryInfo/
GetProcessTimes (all reachable through ctypes, which is stdlib), and Linux
exposes the same information as plain text files under /proc. Anything
this module can't determine safely returns None rather than guessing, so
callers must handle "unknown", not assume zero or a default.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional


IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"


def get_system_ram_mb() -> Optional[dict]:
    """Returns {'total_mb', 'available_mb', 'percent_used'} or None if this
    OS isn't supported yet (report unknown, never fabricate a number)."""
    if IS_WINDOWS:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        mb = 1024 * 1024
        return {
            "total_mb": stat.ullTotalPhys / mb,
            "available_mb": stat.ullAvailPhys / mb,
            "percent_used": stat.dwMemoryLoad,
        }

    if IS_LINUX:
        try:
            with open("/proc/meminfo") as f:
                vals = {}
                for line in f:
                    key, _, rest = line.partition(":")
                    if key in ("MemTotal", "MemAvailable"):
                        vals[key] = int(rest.strip().split()[0]) / 1024  # kB -> MB
            total = vals.get("MemTotal")
            avail = vals.get("MemAvailable")
            if total is None or avail is None:
                return None
            return {
                "total_mb": total,
                "available_mb": avail,
                "percent_used": round(100 * (1 - avail / total), 1) if total else None,
            }
        except OSError:
            return None

    return None  # macOS etc: not implemented, report unknown rather than guess


def get_process_memory_mb(pid: Optional[int] = None) -> Optional[dict]:
    """Returns {'working_set_mb', 'peak_working_set_mb'} for this process
    (or `pid`), or None if unsupported on this OS."""
    if IS_WINDOWS:
        import ctypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        # HANDLE is pointer-sized (64-bit on x64) -- without explicit
        # restype/argtypes, ctypes defaults to a 32-bit int for the return
        # value and arguments, which truncates GetCurrentProcess()'s
        # pseudo-handle (-1 as a 64-bit value) and makes the later call
        # silently fail. Must declare these explicitly.
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), ctypes.c_ulong
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int

        if pid is None:
            handle = kernel32.GetCurrentProcess()
        else:
            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_VM_READ = 0x0010
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            if not handle:
                return None
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if pid is not None:
            kernel32.CloseHandle(handle)
        if not ok:
            return None
        mb = 1024 * 1024
        return {
            "working_set_mb": counters.WorkingSetSize / mb,
            "peak_working_set_mb": counters.PeakWorkingSetSize / mb,
        }

    if IS_LINUX:
        target = "self" if pid is None else str(pid)
        try:
            with open(f"/proc/{target}/status") as f:
                vals = {}
                for line in f:
                    key, _, rest = line.partition(":")
                    if key in ("VmRSS", "VmHWM"):
                        vals[key] = int(rest.strip().split()[0]) / 1024  # kB -> MB
            if "VmRSS" not in vals:
                return None
            return {
                "working_set_mb": vals["VmRSS"],
                "peak_working_set_mb": vals.get("VmHWM", vals["VmRSS"]),
            }
        except OSError:
            return None

    return None


@dataclass
class CpuTimeSample:
    wall: float
    cpu_seconds: Optional[float]


def sample_cpu_time() -> CpuTimeSample:
    """Wall clock + this process's total (user+kernel) CPU seconds so far.
    Two samples bracketing a region let you compute a utilization percent:
    (cpu_seconds_delta / wall_delta / logical_cores) * 100."""
    wall = time.time()
    cpu_seconds: Optional[float] = None
    if IS_WINDOWS:
        import ctypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

        creation, exit_, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
        ]
        kernel32.GetProcessTimes.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        ok = kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_), ctypes.byref(kernel), ctypes.byref(user)
        )
        if ok:
            def _to_100ns(ft):
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

            cpu_seconds = (_to_100ns(kernel) + _to_100ns(user)) / 1e7  # 100ns units -> s
    else:
        try:
            import resource

            ru = resource.getrusage(resource.RUSAGE_SELF)
            cpu_seconds = ru.ru_utime + ru.ru_stime
        except ImportError:
            cpu_seconds = None
    return CpuTimeSample(wall=wall, cpu_seconds=cpu_seconds)


def cpu_utilization_percent(start: CpuTimeSample, end: CpuTimeSample, logical_cores: int) -> Optional[float]:
    if start.cpu_seconds is None or end.cpu_seconds is None:
        return None
    wall_delta = end.wall - start.wall
    if wall_delta <= 0:
        return None
    cpu_delta = end.cpu_seconds - start.cpu_seconds
    return round(100 * cpu_delta / wall_delta / max(logical_cores, 1), 1)


def run_os_tool(args: list[str], timeout: float = 15.0) -> Optional[str]:
    """Runs an OS-shipped tool (powershell, dxdiag, ...) -- never a pip
    package -- and returns stdout, or None on any failure/timeout. Used
    only for read-only system inspection."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        return None


def powershell_json(command: str, timeout: float = 15.0) -> Optional[object]:
    """Runs a PowerShell command that ends in `ConvertTo-Json` and parses
    the result. Returns None on failure rather than raising."""
    if not IS_WINDOWS:
        return None
    import json

    out = run_os_tool(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command], timeout=timeout
    )
    if not out or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None
