"""Verifica SafeDestroy/detach_tk_vars: niente Variable.__del__ in thread."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402

from app.ui.tkutil import SafeDestroy, detach_tk_vars  # noqa: E402


def main() -> int:
    ok = 0
    root = tk.Tk()
    root.withdraw()

    class W(SafeDestroy, tk.Toplevel):
        pass

    w = W(root)
    v = tk.StringVar(master=w, value="x")
    w.v = v
    b = tk.BooleanVar(master=w, value=True)
    w.b = b
    assert v._tk is not None, "var non inizializzata"
    ok += 1
    print("1 var create ok")

    w.destroy()
    assert v._tk is None, "detach non eseguito (StringVar)"
    assert b._tk is None, "detach non eseguito (BooleanVar)"
    ok += 1
    print("2 detach alla destroy ok")

    # __del__ con _tk=None deve essere un no-op (cfr. tkinter.Variable)
    v.__del__()
    b.__del__()
    ok += 1
    print("3 __del__ no-op ok")

    # detach esplicito su un widget ancora vivo
    root2 = tk.Toplevel(root)
    v2 = tk.StringVar(master=root2, value="y")
    root2.v2 = v2
    detach_tk_vars(root2)
    assert v2._tk is None, "detach esplicito non fatto"
    v2.__del__()
    root2.destroy()
    ok += 1
    print("4 detach esplicito ok")

    # MRO: SafeDestroy deve finire su Tk/Toplevel.destroy
    assert issubclass(W, SafeDestroy)
    import inspect
    assert "detach_tk_vars" in inspect.getsource(SafeDestroy.destroy)
    ok += 1
    print("5 MRO ok")

    root.destroy()
    print(f"PASS {ok}/5 test_tkutil")
    return 0


if __name__ == "__main__":
    sys.exit(main())
