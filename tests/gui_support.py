"""Helpers shared by test_gui.py and gui_scenarios.py: running the real Tk
event loop until a condition holds, and small file writers."""
from __future__ import annotations

import time
import tkinter as tk


def put(path: str, data) -> None:
    """Write bytes or text to a file and close it."""
    if isinstance(data, bytes):
        with open(path, "wb") as f:
            f.write(data)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)


def alive(app) -> bool:
    try:
        return bool(app.root.winfo_exists())
    except tk.TclError:
        return False


def pump(app, cond, timeout: float = 6.0) -> bool:
    """Run the real Tk event loop until ``cond()`` is true or time runs out."""
    root = app.root
    t0 = time.time()
    state = {"ok": False}

    def poll():
        try:
            if cond():
                state["ok"] = True
                root.quit()
                return
        except tk.TclError:
            state["ok"] = True
            root.quit()
            return
        if time.time() - t0 > timeout:
            root.quit()
            return
        root.after(5, poll)

    root.after(0, poll)
    root.mainloop()
    if not state["ok"]:
        try:
            state["ok"] = bool(cond())            # the loop also ends by itself when the window is destroyed
        except tk.TclError:
            state["ok"] = True
    return state["ok"]


def sleep_pump(app, seconds: float) -> None:
    t_end = time.time() + seconds
    pump(app, lambda: time.time() >= t_end, timeout=seconds + 2)
