"""Verifica lo splash dell'exe senza screenshot (sessione senza desktop).

Controlla che nei primi secondi esista la nostra finestra splash
(560x320, senza titlebar) appartenente al processo IACallMinute, che poi
appaia la finestra principale (titolo "IACallMinute x.y.z") e che alla
fine lo splash sparisca.
"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

import psutil

SPLASH_W, SPLASH_H = 560, 320

user32 = ctypes.windll.user32
CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def iacm_pids() -> set:
    out = set()
    for q in psutil.process_iter(["name"]):
        if q.info["name"] and "IACallMinute" in q.info["name"]:
            out.add(q.pid)
    return out


def is_splash(w: int, h: int) -> bool:
    # stessa proporzione 560x320, comunque piu' stretta della GUI
    return bool(h) and abs(w / h - SPLASH_W / SPLASH_H) < 0.03 and w <= 1400


def windows_for(pids: set) -> list:
    found = []

    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((buf.value, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(CB(cb), 0)
    return found


def main() -> int:
    exe = r"C:\Progetti\IACallMinute\dist\IACallMinute.exe"
    p = subprocess.Popen([exe])
    t0 = time.time()
    splash_seen = None
    gui_seen = None
    splash_gone_after = None
    print("t\tpid\tfinestre (titolo wxh)")
    while time.time() - t0 < 45:
        pids = iacm_pids()
        wins = windows_for(pids) if pids else []
        dt = time.time() - t0
        for title, w, h in wins:
            if is_splash(w, h) and splash_seen is None:
                splash_seen = dt
                print(f"SPLASH {dt:.1f}s '{title}' {w}x{h}")
            if title.startswith("IACallMinute 1") and gui_seen is None:
                gui_seen = dt
                print(f"GUI    {dt:.1f}s '{title}' {w}x{h}")
        if splash_seen is not None and splash_gone_after is None:
            still = any(is_splash(w, h) for _, w, h in wins)
            if not still:
                splash_gone_after = dt
        if gui_seen and splash_gone_after:
            break
        if dt < 6:
            line = "; ".join(f"'{t}' {w}x{h}" for t, w, h in wins)
            print(f"{dt:.1f}\t{sorted(pids)}\t{line}")
        time.sleep(0.4)

    ok = True
    print("---")
    if splash_seen is None:
        print("FAIL splash mai visto (finestra 560x320)")
        ok = False
    else:
        print(f"OK splash presente da {splash_seen:.1f}s")
    if splash_seen is not None and splash_seen > 4:
        print(f"FAIL splash tardi ({splash_seen:.1f}s)")
        ok = False
    if gui_seen is None:
        print("FAIL finestra principale mai vista")
        ok = False
    else:
        print(f"OK finestra principale da {gui_seen:.1f}s")
    if splash_seen is not None and gui_seen is not None and splash_seen >= gui_seen:
        print("FAIL lo splash resta sopra la GUI")
        ok = False
    if splash_gone_after is None:
        print("FAIL lo splash non sparisce")
        ok = False
    else:
        print(f"OK splash chiuso a {splash_gone_after:.1f}s")

    for q in psutil.process_iter(["name"]):
        if q.info["name"] and "IACallMinute" in q.info["name"]:
            q.terminate()
    time.sleep(3)
    left = [q.pid for q in psutil.process_iter(["name"])
            if q.info["name"] and "IACallMinute" in q.info["name"]]
    if left:
        print(f"FAIL processi residui {left}")
        for q in psutil.process_iter(["name"]):
            if q.info["name"] and "IACallMinute" in q.info["name"]:
                q.kill()
        ok = False
    else:
        print("OK nessun processo residuo")

    print("PASS test_exe_splash" if ok else "FAIL test_exe_splash")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
