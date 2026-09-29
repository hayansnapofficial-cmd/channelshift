"""Read an optional Windows-user DPAPI credential outside projects and exports."""
from __future__ import annotations

import os
from pathlib import Path


def read_typesafe_key():
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        return ""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    path = Path(os.environ["LOCALAPPDATA"]) / "ChannelShift" / "credentials" / "typesafe.dpapi"
    try:
        with path.open("rb") as stream:
            encrypted = stream.read(8193)
        if not 1 <= len(encrypted) <= 8192:
            return ""
        buffer = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
        source, output = Blob(len(encrypted), buffer), Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        crypt.CryptUnprotectData.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            return ""
        try:
            if not 1 <= output.size <= 512:
                return ""
            return ctypes.string_at(output.data, output.size).decode("ascii")
        finally:
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(output.data)
    except (OSError, ValueError, UnicodeError):
        return ""
