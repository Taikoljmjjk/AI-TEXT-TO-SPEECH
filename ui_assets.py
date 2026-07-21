from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Resolve bundled UI assets in source and PyInstaller builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def apply_app_icon(window: tk.Tk) -> None:
    """Apply the TAILEMMO icon and retain its Tk image reference."""
    icon_path = resource_path("assets", "tailemmo_icon.png")
    if not icon_path.is_file():
        return
    try:
        image = tk.PhotoImage(file=str(icon_path))
        window.iconphoto(True, image)
        window._tailemmo_icon = image  # type: ignore[attr-defined]
    except tk.TclError:
        # Missing icon data must never prevent the application from opening.
        return
