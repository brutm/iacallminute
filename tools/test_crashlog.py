"""Verifica che i crash finiscano SEMPRE su file.

Covers due scenari dell'exe windowed:
1. sys.stderr is None -> traceback reindirizzato su logs/stderr.log
2. eccezione in un thread -> logs/crash.log (threading.excepthook)
3. eccezione non gestita nel main -> logs/crash.log (sys.excepthook)
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import config  # noqa: E402

CRASH = config.LOG_DIR / "crash.log"
STDERR = config.LOG_DIR / "stderr.log"


def _run(code: str) -> None:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    assert r.returncode == 0, f"exit {r.returncode}: {r.stderr}"
    time.sleep(0.3)


def _tail(path, n=40):
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8",
                                    errors="replace").splitlines()[-n:])


def main() -> int:
    ok = 0
    for p in (CRASH, STDERR):
        if p.exists():
            p.unlink()
    marker = f"TC{int(time.time())}"

    # 1. stderr None -> redirect su file
    _run(f"""
import sys
sys.path.insert(0, {ROOT!r})
sys.stderr = None
import main as m
m._redirect_stdio()
print("MARKER_OUT {marker}", file=sys.stderr)
""")
    tail = _tail(STDERR)
    assert f"MARKER_OUT {marker}" in tail, "stderr non reindirizzato"
    ok += 1
    print("1 stderr redirect su stderr.log ok")

    # 2. eccezione in un thread -> crash.log
    _run(f"""
import sys, threading, time
sys.path.insert(0, {ROOT!r})
import main as m
m._redirect_stdio()
m._install_excepthook()
def boom():
    raise RuntimeError('TC_THREAD {marker}')
t = threading.Thread(target=boom); t.start(); t.join()
time.sleep(0.2)
""")
    tail = _tail(CRASH)
    assert "THREAD EXCEPTION" in tail, "threading.excepthook non scritto"
    assert f"TC_THREAD {marker}" in tail, "traceback thread mancante"
    ok += 1
    print("2 crash.log da threading.excepthook ok")

    # 3. eccezione non gestita nel main -> crash.log
    r = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {ROOT!r});\n"
         f"import main as m; m._redirect_stdio(); m._install_excepthook();\n"
         f"raise RuntimeError('TC_MAIN {marker}')"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60)
    assert r.returncode != 0, "il main avrebbe dovuto uscire con errore"
    tail = _tail(CRASH)
    assert "EXCEPTION" in tail, "sys.excepthook non scritto"
    assert f"TC_MAIN {marker}" in tail, "traceback main mancante"
    ok += 1
    print("3 crash.log da sys.excepthook ok")

    print(f"PASS {ok}/3 test_crashlog")
    return 0


if __name__ == "__main__":
    sys.exit(main())
