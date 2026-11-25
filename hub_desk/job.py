"""Windows Job Object: everything a pane starts ends with the pane.

A CLI starts MCP servers, and those start more. When a process in the middle exits first, its children are no
longer reachable by walking parent ids (`taskkill /T` misses them). A job holds every descendant regardless.
KILL_ON_JOB_CLOSE also covers the server dying: Windows closes the handle and ends the job's processes.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
HANDLE = ctypes.c_void_p
for name, res, args in (
    ("CreateJobObjectW", HANDLE, (ctypes.c_void_p, wintypes.LPCWSTR)),
    ("SetInformationJobObject", wintypes.BOOL, (HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)),
    ("AssignProcessToJobObject", wintypes.BOOL, (HANDLE, HANDLE)),
    ("TerminateJobObject", wintypes.BOOL, (HANDLE, wintypes.UINT)),
    ("QueryInformationJobObject", wintypes.BOOL, (HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)),
    ("OpenProcess", HANDLE, (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)),
    ("WaitForSingleObject", wintypes.DWORD, (HANDLE, wintypes.DWORD)),
    ("CloseHandle", wintypes.BOOL, (HANDLE,)),
):
    getattr(k32, name).restype, getattr(k32, name).argtypes = res, args

KILL_ON_JOB_CLOSE, EXTENDED_LIMIT_INFORMATION = 0x2000, 9
PROCESS_TERMINATE, PROCESS_SET_QUOTA, SYNCHRONIZE = 0x0001, 0x0100, 0x00100000


class _Basic(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _Extended(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


class Job:
    """Holds one process and all it starts from now on. `ok` is False when Windows refused; callers fall back."""

    def __init__(self, pid: int):
        self.handle, self.ok = k32.CreateJobObjectW(None, None), False
        if not self.handle:
            return
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
        proc = k32.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, pid)
        if proc:
            self.ok = bool(k32.SetInformationJobObject(self.handle, EXTENDED_LIMIT_INFORMATION,
                                                       ctypes.byref(info), ctypes.sizeof(info))
                           and k32.AssignProcessToJobObject(self.handle, proc))
            k32.CloseHandle(proc)

    def active(self) -> int:
        """Processes alive in the job right now."""
        info = _Accounting()
        if not self.handle or not k32.QueryInformationJobObject(self.handle, 1, ctypes.byref(info),
                                                                ctypes.sizeof(info), None):
            return 0
        return info.ActiveProcesses

    def kill(self) -> tuple[int, int]:
        """End everything in the job. Returns (processes that were alive, processes still alive after)."""
        if not self.handle:
            return 0, 0
        import time
        had = self.active()
        k32.TerminateJobObject(self.handle, 1)
        for _ in range(50):  # termination is asynchronous: give it up to 5 s before reporting what is left
            if not self.active():
                break
            time.sleep(0.1)
        left = self.active()
        k32.CloseHandle(self.handle)
        self.handle = None
        return had, left


def pid_alive(pid: int) -> bool:
    h = k32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        k32.CloseHandle(h)
