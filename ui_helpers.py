"""Layout-independent clipboard shortcuts and hover tooltips for the UI."""
from __future__ import annotations

import textwrap
import tkinter as tk
from typing import Any, Callable, Literal

# Windows virtual-key codes stay the same across keyboard layouts.
_VK_A = 65
_VK_C = 67
_VK_V = 86
_VK_X = 88
_VK_INSERT = 45

_CTRL = 0x4
_SHIFT = 0x1

ClipboardAction = Literal["paste", "copy", "cut", "select_all"]


def clipboard_action_from_event(event: Any) -> ClipboardAction | None:
    """Map a key event to a clipboard action using physical keycodes.

    On non-English layouts (Hebrew, Russian, …) Control+V often reports a
    local keysym instead of "v". The Windows keycode for V stays 86, so we
    prefer keycode when Control is held.
    """
    state = int(getattr(event, "state", 0) or 0)
    keycode = int(getattr(event, "keycode", 0) or 0)
    keysym = str(getattr(event, "keysym", "") or "").lower()

    if state & _SHIFT and (keycode == _VK_INSERT or keysym == "insert"):
        return "paste"

    if not (state & _CTRL):
        return None

    if keycode == _VK_V or keysym in ("v", "V"):
        return "paste"
    if keycode == _VK_C or keysym in ("c", "C"):
        return "copy"
    if keycode == _VK_X or keysym in ("x", "X"):
        return "cut"
    if keycode == _VK_A or keysym in ("a", "A"):
        return "select_all"
    return None


def wrap_tooltip(text: str, width: int = 48) -> str:
    """Wrap tooltip text for readable hover popups."""
    return textwrap.fill(text.strip(), width=width)


class ToolTip:
    """Small delayed hover label for buttons, entries, and text boxes."""

    def __init__(self, widget: tk.Misc, text: str, delay_ms: int = 450) -> None:
        self.widget = widget
        self.text = wrap_tooltip(text)
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _event=None) -> None:
        self._cancel()
        self._hide()

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 16
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tip.attributes("-topmost", True)
        label = tk.Label(
            tip,
            text=self.text,
            justify="left",
            background="#1e1e1e",
            foreground="#f0f0f0",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
        )
        label.pack()
        self._tip = tip

    def _hide(self) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def attach_tooltip(widget: tk.Misc, text: str) -> ToolTip:
    return ToolTip(widget, text)


def _textbox_widget(widget: Any) -> Any:
    """Return the underlying tk Text for a CTkTextbox when present."""
    return getattr(widget, "_textbox", widget)


def bind_layout_independent_clipboard(
    widget: Any,
    *,
    on_before_paste: Callable[[], None] | None = None,
) -> None:
    """Bind Ctrl/Cmd clipboard shortcuts that work on any keyboard layout."""

    target = _textbox_widget(widget)

    def _handle(event):
        action = clipboard_action_from_event(event)
        if action is None:
            return None
        try:
            if action == "paste":
                if on_before_paste is not None:
                    on_before_paste()
                try:
                    text = widget.clipboard_get()
                except tk.TclError:
                    return "break"
                try:
                    target.delete("sel.first", "sel.last")
                except tk.TclError:
                    pass
                target.insert("insert", text)
                return "break"
            if action == "copy":
                try:
                    selected = target.get("sel.first", "sel.last")
                except tk.TclError:
                    return "break"
                widget.clipboard_clear()
                widget.clipboard_append(selected)
                return "break"
            if action == "cut":
                try:
                    selected = target.get("sel.first", "sel.last")
                except tk.TclError:
                    return "break"
                widget.clipboard_clear()
                widget.clipboard_append(selected)
                target.delete("sel.first", "sel.last")
                return "break"
            if action == "select_all":
                target.tag_add("sel", "1.0", "end-1c")
                target.mark_set("insert", "1.0")
                target.see("insert")
                return "break"
        except Exception:
            return "break"
        return "break"

    # KeyPress catches layout-local keysyms; keycode still identifies the physical key.
    for sequence in ("<Control-KeyPress>", "<Control-Key>", "<Shift-Insert>"):
        widget.bind(sequence, _handle, add="+")
        target.bind(sequence, _handle, add="+")
