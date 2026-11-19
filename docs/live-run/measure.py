"""Run the real linkx CLI in this process and report wall time, CPU time and peak memory.

Usage:  uv run python docs/live-run/measure.py <linkx arguments...>
"""

import ctypes
import sys
import time
from ctypes import wintypes

from linkx.cli import main


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_working_set_mb() -> float:
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE  # a pseudo-handle, -1
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError()
    return counters.PeakWorkingSetSize / 1e6


wall, cpu = time.perf_counter(), time.process_time()
code = main(sys.argv[1:])
print(
    f"MEASURED: exit {code}, wall {time.perf_counter() - wall:.1f}s, "
    f"cpu {time.process_time() - cpu:.1f}s, peak memory {peak_working_set_mb():.0f} MB",
    file=sys.stderr,
)
sys.exit(code)
