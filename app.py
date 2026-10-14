"""Barcode Image Downloader — interactive preview & selection UI."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk  # noqa: F401  (kept so PyInstaller bundles PIL's tk bridge)

from downloader import (
    BarcodeResult,
    PreviewInfo,
    download_first_image,
    download_image_bytes,
    download_previews,
    is_safe_url,
    sanitize_barcode,
    sanitize_filename_part,
    save_as_jpg,
    search_image_urls,
)
from ui_helpers import attach_tooltip, bind_layout_independent_clipboard
from updater import (
    APP_VERSION,
    UpdateInfo,
    check_update_status,
    check_for_update,
    cleanup_previous_update,
    download_update,
    install_update,
    is_frozen,
    relaunch,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_PLACEHOLDER = "1234567890123\n9876543210987\n..."
_MAX_SLOTS = 20
_THUMB_SIZE = 120
_GRID_COLS = 4
# Barcodes processed at once by Download all. Kept modest so the image
# search sites do not start throttling us.
_AUTO_WORKERS = 4


def _fetch_candidates(barcode: str, count: int) -> tuple[list[str], list[PreviewInfo]]:
    """Search + download previews for one barcode (runs on a worker thread)."""
    urls = search_image_urls(barcode, max_results=count)
    return urls, download_previews(urls, max_size=_THUMB_SIZE)


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Barcode Image Downloader v{APP_VERSION}")
        self.minsize(750, 400)

        self._output_dir: str = ""
        self._running = False
        self._cancelled = False
        self._auto_first = False
        self._barcodes: list[str] = []
        self._current_idx: int = 0
        self._image_urls: list[str] = []
        self._previews: list[PreviewInfo] = []
        # Bumped on every batch start/cancel; late worker results from an
        # older batch compare against it and are dropped.
        self._batch_id = 0
        self._prefetch: dict[int, Future] = {}
        self._auto_done = 0
        self._auto_futures: list[Future] = []
        self._executor = ThreadPoolExecutor(max_workers=_AUTO_WORKERS, thread_name_prefix="bid-app")
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()

        self._check_vars: list[ctk.BooleanVar] = [ctk.BooleanVar(value=False) for _ in range(_MAX_SLOTS)]
        self._naming_var = ctk.StringVar(value="barcode")
        self._tooltips: list = []

        self._build_ui()
        self._fit_window()
        cleanup_previous_update()
        self._drain_ui_queue()
        self.after(800, self._start_update_check)

    # ------------------------------------------------------------------ #
    #  Scrollable wrapper                                                  #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        # Canvas + scrollbar for full-window scrolling
        self._canvas = tk.Canvas(self, highlightthickness=0, bg="#2b2b2b")
        self._scrollbar = ctk.CTkScrollbar(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        # Inner frame that holds all content
        self._inner = ctk.CTkFrame(self._canvas, fg_color="transparent")
        self._canvas_window = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")

        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        # Mouse wheel scrolling
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self._build_content(self._inner)

    def _on_inner_configure(self, _event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event=None) -> None:
        self._canvas.itemconfig(self._canvas_window, width=event.width)

    def _on_mousewheel(self, event) -> None:
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ------------------------------------------------------------------ #
    #  Content                                                             #
    # ------------------------------------------------------------------ #

    def _build_content(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)

        # 1. Header: barcode label left, Check for updates right
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(12, 0), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header, text="Paste barcodes (one per line):",
        ).grid(row=0, column=0, sticky="w")

        self._update_btn = ctk.CTkButton(
            header, text="Check for updates", width=150,
            command=self._on_check_updates,
        )
        self._update_btn.grid(row=0, column=1, sticky="e")
        self._tip(
            self._update_btn,
            "Check GitHub for a newer version. If one is available, you can update now or later.",
        )

        self._barcode_text = ctk.CTkTextbox(parent, height=100)
        self._barcode_text.grid(row=1, column=0, padx=20, pady=(4, 0), sticky="ew")
        self._barcode_text.insert("1.0", _PLACEHOLDER)
        self._barcode_placeholder_active = True
        self._barcode_text.bind("<FocusIn>", self._on_barcode_focus_in)
        self._barcode_text.bind("<FocusOut>", self._on_barcode_focus_out)
        # Ctrl+V/C/X/A by physical keycode so paste works on Hebrew and other layouts
        bind_layout_independent_clipboard(
            self._barcode_text,
            on_before_paste=self._clear_barcode_placeholder,
        )
        self._tip(
            self._barcode_text,
            "Paste barcodes here, one per line. Copy/paste works in any keyboard language.",
        )

        # 2. Output folder row
        folder_frame = ctk.CTkFrame(parent, fg_color="transparent")
        folder_frame.grid(row=2, column=0, padx=20, pady=(8, 0), sticky="ew")
        folder_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(folder_frame, text="Output folder:").grid(
            row=0, column=0, padx=(0, 8), sticky="w")

        self._folder_entry = ctk.CTkEntry(
            folder_frame, placeholder_text="No folder selected", state="readonly")
        self._folder_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self._tip(
            self._folder_entry,
            "Folder where saved JPEG images will be written. Use Browse to choose one.",
        )

        self._browse_btn = ctk.CTkButton(
            folder_frame, text="Browse", width=80,
            command=self._browse_folder,
        )
        self._browse_btn.grid(row=0, column=2)
        self._tip(self._browse_btn, "Choose the folder where downloaded images will be saved.")

        # 3. Qty images row
        qty_frame = ctk.CTkFrame(parent, fg_color="transparent")
        qty_frame.grid(row=3, column=0, padx=20, pady=(8, 0), sticky="ew")

        ctk.CTkLabel(qty_frame, text="Qty images:").grid(
            row=0, column=0, padx=(0, 8), sticky="w")

        self._qty_var = ctk.StringVar(value="4")
        self._qty_entry = ctk.CTkEntry(qty_frame, width=60, textvariable=self._qty_var)
        self._qty_entry.grid(row=0, column=1, sticky="w")
        self._tip(
            self._qty_entry,
            "How many preview images to fetch per barcode when using Download Images (1–20).",
        )

        ctk.CTkLabel(qty_frame, text="(number of preview images per barcode)",
                     text_color="gray").grid(row=0, column=2, padx=(10, 0), sticky="w")

        # 4. Naming choice row
        naming_frame = ctk.CTkFrame(parent, fg_color="transparent")
        naming_frame.grid(row=4, column=0, padx=20, pady=(8, 0), sticky="ew")

        ctk.CTkLabel(naming_frame, text="File naming:").grid(
            row=0, column=0, padx=(0, 12), sticky="w")

        self._naming_barcode_rb = ctk.CTkRadioButton(
            naming_frame, text="Barcode", variable=self._naming_var,
            value="barcode", command=self._on_naming_changed,
        )
        self._naming_barcode_rb.grid(row=0, column=1, padx=(0, 16), sticky="w")
        self._tip(
            self._naming_barcode_rb,
            "Save files as the full barcode, e.g. 4005555042822.jpg.",
        )

        self._naming_initials_rb = ctk.CTkRadioButton(
            naming_frame, text="Initials", variable=self._naming_var,
            value="initials", command=self._on_naming_changed,
        )
        self._naming_initials_rb.grid(row=0, column=2, padx=(0, 8), sticky="w")
        self._tip(
            self._naming_initials_rb,
            "Save files as initials plus digits 7–11 of the barcode, e.g. FNK-50428.jpg.",
        )

        self._initials_entry = ctk.CTkEntry(
            naming_frame, width=80, placeholder_text="e.g. FNK")
        self._initials_entry.grid(row=0, column=3, padx=(0, 8), sticky="w")
        self._tip(
            self._initials_entry,
            "Enter your initials (or short code) used as the filename prefix.",
        )

        self._initials_hint = ctk.CTkLabel(
            naming_frame,
            text="→ chars 7–11 of barcode  (e.g. FNK + 889698806961 → FNK-80696)",
            text_color="gray",
        )
        self._initials_hint.grid(row=0, column=4, sticky="w")

        # Start with initials widgets hidden
        self._initials_entry.grid_remove()
        self._initials_hint.grid_remove()

        # 5. Download Images + Download all + Cancel All row
        action_frame = ctk.CTkFrame(parent, fg_color="transparent")
        action_frame.grid(row=5, column=0, pady=(8, 0), padx=20, sticky="ew")
        action_frame.grid_columnconfigure(0, weight=1)

        self._download_btn = ctk.CTkButton(
            action_frame, text="Download Images",
            command=self._start_download,
        )
        self._download_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._tip(
            self._download_btn,
            "Search for each barcode and show preview images so you can choose which ones to save.",
        )

        self._download_all_btn = ctk.CTkButton(
            action_frame, text="Download all", width=130,
            fg_color="#1a6334", hover_color="#14502a",
            command=self._start_download_all,
        )
        self._download_all_btn.grid(row=0, column=1, padx=(0, 8))
        self._tip(
            self._download_all_btn,
            "For each barcode, automatically save the first image found — no preview or selection.",
        )

        self._cancel_btn = ctk.CTkButton(
            action_frame, text="Cancel All", width=130,
            fg_color="#a33232", hover_color="#822828",
            command=self._on_cancel_all, state="disabled",
        )
        self._cancel_btn.grid(row=0, column=2)
        self._tip(
            self._cancel_btn,
            "Stop the current batch. Images already saved stay on disk.",
        )

        # 6. Progress bar + counter
        prog_frame = ctk.CTkFrame(parent, fg_color="transparent")
        prog_frame.grid(row=6, column=0, padx=20, pady=(6, 0), sticky="ew")
        prog_frame.grid_columnconfigure(0, weight=1)

        self._progress = ctk.CTkProgressBar(prog_frame)
        self._progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self._progress.set(0)

        self._progress_label = ctk.CTkLabel(prog_frame, text="")
        self._progress_label.grid(row=0, column=1)

        # 7. Preview area — left: images grid, right: action buttons
        self._preview_frame = ctk.CTkFrame(parent)
        self._preview_frame.grid(row=7, column=0, padx=20, pady=(8, 0), sticky="ew")
        self._preview_frame.grid_columnconfigure(0, weight=1)
        self._preview_frame.grid_columnconfigure(1, weight=0)

        # Left side: barcode label + status + image slots
        self._left_panel = ctk.CTkFrame(self._preview_frame, fg_color="transparent")
        self._left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._left_panel.grid_columnconfigure(0, weight=1)

        self._barcode_label = ctk.CTkLabel(
            self._left_panel, text="", font=ctk.CTkFont(size=16, weight="bold"))
        self._barcode_label.grid(row=0, column=0, pady=(8, 4))

        self._status_label = ctk.CTkLabel(
            self._left_panel, text="Paste barcodes and click Download Images",
            text_color="gray")
        self._status_label.grid(row=1, column=0, pady=(0, 4))

        self._slots_frame = ctk.CTkFrame(self._left_panel, fg_color="transparent")
        self._slots_frame.grid(row=2, column=0, sticky="ew")
        self._slots_frame.grid_columnconfigure(tuple(range(_GRID_COLS)), weight=1)

        self._slot_widgets: list[ctk.CTkFrame] = []
        self._img_labels: list[ctk.CTkLabel] = []
        self._checkboxes: list[ctk.CTkCheckBox] = []
        self._slot_save_btns: list[ctk.CTkButton] = []

        # Right side: action buttons (vertical stack)
        self._btn_panel = ctk.CTkFrame(self._preview_frame, fg_color="transparent")
        self._btn_panel.grid(row=0, column=1, sticky="ns", padx=(0, 10), pady=10)
        self._btn_panel.grid_remove()  # hidden until previews

        self._btn_save_all = ctk.CTkButton(
            self._btn_panel, text="Save All", width=130,
            fg_color="#1a6334", hover_color="#14502a",
            command=self._on_save_all, state="disabled")
        self._btn_save_all.pack(pady=(0, 8))
        self._tip(self._btn_save_all, "Save every preview image shown for this barcode.")

        self._btn_save = ctk.CTkButton(
            self._btn_panel, text="Save Selected", width=130,
            fg_color="#2d8a4e", hover_color="#236b3c",
            command=self._on_save_selected, state="disabled")
        self._btn_save.pack(pady=(0, 8))
        self._tip(self._btn_save, "Save only the preview images you checked.")

        self._btn_skip = ctk.CTkButton(
            self._btn_panel, text="Skip", width=130,
            fg_color="#6b6b6b", hover_color="#555555",
            command=self._on_skip, state="disabled")
        self._btn_skip.pack()
        self._tip(self._btn_skip, "Skip this barcode and move to the next one without saving.")

        # 8. Log row — log on left, Open Folder on right
        log_frame = ctk.CTkFrame(parent, fg_color="transparent")
        log_frame.grid(row=8, column=0, padx=20, pady=(8, 0), sticky="ew")
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_columnconfigure(1, weight=0)

        log_left = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        log_left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_left, text="Log:").grid(row=0, column=0, sticky="w")
        self._log_box = ctk.CTkTextbox(log_left, height=100, state="disabled")
        self._log_box.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        self._open_btn = ctk.CTkButton(
            log_frame, text="Open\nFolder", width=130,
            command=self._open_folder, state="disabled",
        )
        self._open_btn.grid(row=0, column=1, sticky="s", pady=(0, 0))
        self._tip(self._open_btn, "Open the output folder in File Explorer.")
        self._tip(self._log_box, "Status log: saved, skipped, and error messages for the current batch.")

        # Bottom spacer
        ctk.CTkLabel(parent, text="").grid(row=9, column=0, pady=(0, 12))

    def _tip(self, widget, text: str) -> None:
        self._tooltips.append(attach_tooltip(widget, text))

    # ------------------------------------------------------------------ #
    #  Placeholder behaviour                                              #
    # ------------------------------------------------------------------ #

    def _clear_barcode_placeholder(self) -> None:
        if self._barcode_placeholder_active:
            self._barcode_text.delete("1.0", "end")
            self._barcode_placeholder_active = False

    def _on_barcode_focus_in(self, _event=None) -> None:
        self._clear_barcode_placeholder()

    def _on_barcode_focus_out(self, _event=None) -> None:
        if not self._barcode_text.get("1.0", "end").strip():
            self._barcode_text.insert("1.0", _PLACEHOLDER)
            self._barcode_placeholder_active = True

    # ------------------------------------------------------------------ #
    #  Naming choice                                                       #
    # ------------------------------------------------------------------ #

    def _on_naming_changed(self) -> None:
        if self._naming_var.get() == "initials":
            self._initials_entry.grid()
            self._initials_hint.grid()
        else:
            self._initials_entry.grid_remove()
            self._initials_hint.grid_remove()

    def _get_filename_base(self, barcode: str) -> str:
        """Return the filename stem (no extension) for a given barcode."""
        if self._naming_var.get() == "initials":
            # Sanitized so typed text like "..\" cannot leave the output folder
            initials = sanitize_filename_part(self._initials_entry.get()) or "IMG"
            # Extract characters at positions 7–11 (0-based index 6:11)
            digits = barcode[6:11] if len(barcode) > 6 else barcode
            # Strip leading zeros (e.g. 09926 → 9926)
            digits = digits.lstrip("0") or digits  # keep original if all zeros
            return sanitize_filename_part(f"{initials}-{digits}") or "IMG"
        return sanitize_barcode(barcode)

    def _output_path(self, barcode: str) -> str:
        """Full .jpg path for a barcode. Reads Tk widgets — main thread only."""
        return os.path.join(self._output_dir, f"{self._get_filename_base(barcode)}.jpg")

    # ------------------------------------------------------------------ #
    #  Folder browsing                                                     #
    # ------------------------------------------------------------------ #

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self._output_dir = folder
            self._folder_entry.configure(state="normal")
            self._folder_entry.delete(0, "end")
            self._folder_entry.insert(0, folder)
            self._folder_entry.configure(state="readonly")

    # ------------------------------------------------------------------ #
    #  Qty images helper                                                   #
    # ------------------------------------------------------------------ #

    def _get_qty(self) -> int:
        try:
            val = int(self._qty_var.get().strip())
            return max(1, min(val, _MAX_SLOTS))
        except ValueError:
            return 4

    # ------------------------------------------------------------------ #
    #  Main flow                                                           #
    # ------------------------------------------------------------------ #

    def _start_download(self) -> None:
        self._begin_batch(auto_first=False)

    def _start_download_all(self) -> None:
        self._begin_batch(auto_first=True)

    def _begin_batch(self, auto_first: bool) -> None:
        if self._running:
            return

        raw_text = "" if self._barcode_placeholder_active else self._barcode_text.get("1.0", "end").strip()
        barcodes = [b.strip() for b in raw_text.splitlines() if b.strip()]

        if not barcodes:
            messagebox.showerror("Validation Error", "Please enter at least one barcode.")
            return
        if not self._output_dir:
            messagebox.showerror("Validation Error", "Please select an output folder.")
            return

        self._running = True
        self._cancelled = False
        self._batch_id += 1
        self._prefetch = {}
        self._auto_first = auto_first
        self._num_images = self._get_qty()
        self._barcodes = barcodes
        self._current_idx = 0
        self._download_btn.configure(state="disabled")
        self._download_all_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._barcode_text.configure(state="disabled")
        self._qty_entry.configure(state="disabled")
        self._initials_entry.configure(state="disabled")
        self._progress.set(0)
        self._progress_label.configure(text=f"0 / {len(barcodes)}")
        self._clear_log()

        if auto_first:
            self._start_auto_batch()
        else:
            self._process_next_barcode()

    # ------------------------------------------------------------------ #
    #  Download all — several barcodes in parallel                         #
    # ------------------------------------------------------------------ #

    def _start_auto_batch(self) -> None:
        """Plan every barcode on the UI thread, then download in parallel."""
        batch_id = self._batch_id
        self._auto_done = 0
        self._auto_futures = []
        self._clear_previews()
        self._set_buttons_enabled(False)

        claimed: set[str] = set()
        immediate: list[BarcodeResult] = []
        jobs: list[tuple[str, str]] = []
        for barcode in self._barcodes:
            if not sanitize_barcode(barcode):
                immediate.append(BarcodeResult(barcode, "error", "empty after sanitization"))
                continue
            out_path = self._output_path(barcode)
            key = os.path.normcase(out_path)
            if key in claimed:
                # Initials naming can map two barcodes to one filename.
                immediate.append(BarcodeResult(
                    barcode, "skipped",
                    f"same filename as an earlier barcode ({os.path.basename(out_path)})"))
                continue
            claimed.add(key)
            jobs.append((barcode, out_path))

        if jobs:
            self._barcode_label.configure(text="Download all")
            self._status_label.configure(
                text=f"Downloading — {min(_AUTO_WORKERS, len(jobs))} barcodes at a time…",
                text_color="yellow")
        for barcode, out_path in jobs:
            fut = self._executor.submit(self._auto_worker, batch_id, barcode, out_path)
            fut.add_done_callback(
                lambda f, b=barcode: self._post(self._on_auto_future_done, batch_id, b, f))
            self._auto_futures.append(fut)
        # Reported after submitting, so the batch can only finish here when
        # nothing was queued.
        for result in immediate:
            self._on_auto_result(batch_id, result)

    def _auto_worker(self, batch_id: int, barcode: str, out_path: str) -> BarcodeResult | None:
        if batch_id != self._batch_id or self._cancelled:
            return None
        return download_first_image(barcode, out_path)

    def _on_auto_future_done(self, batch_id: int, barcode: str, fut: Future) -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        result = BarcodeResult(barcode, "error", str(exc)) if exc else fut.result()
        if result is not None:
            self._on_auto_result(batch_id, result)

    def _on_auto_result(self, batch_id: int, result: BarcodeResult) -> None:
        if batch_id != self._batch_id or self._cancelled or not self._running:
            return
        if result.status == "saved":
            self._log(f"[SAVED] {result.barcode} — {result.message}")
            self._open_btn.configure(state="normal")
        elif result.status == "skipped":
            self._log(f"[SKIP] {result.barcode} — {result.message}")
        elif result.status == "no_image":
            self._log(f"[WARN] {result.barcode} — {result.message}")
        else:
            self._log(f"[ERROR] {result.barcode} — {result.message}")

        self._auto_done += 1
        total = len(self._barcodes)
        self._progress.set(self._auto_done / total)
        self._progress_label.configure(text=f"{self._auto_done} / {total}")
        if self._auto_done >= total:
            self._on_batch_done()

    # ------------------------------------------------------------------ #
    #  Download Images — one barcode at a time with preview                #
    # ------------------------------------------------------------------ #

    def _post(self, fn, *args) -> None:
        """Run fn(*args) on the Tk thread. Safe to call from any thread.

        Worker threads never touch Tk directly (Tk is not thread-safe and
        `after()` from a thread fails when the main loop is busy); they queue
        the call and the UI thread drains the queue.
        """
        self._ui_queue.put((fn, args))

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                fn, args = self._ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception as exc:  # keep the pump alive whatever happens
                    self._log(f"[ERROR] internal: {exc}")
        except queue.Empty:
            pass
        self.after(30, self._drain_ui_queue)

    def _is_pending(self, barcode: str) -> bool:
        """True if this barcode still needs a preview (not invalid, not saved yet)."""
        return bool(sanitize_barcode(barcode)) and not os.path.exists(self._output_path(barcode))

    def _fetch_for(self, idx: int) -> Future:
        fut = self._prefetch.pop(idx, None)
        if fut is None:
            fut = self._executor.submit(_fetch_candidates, self._barcodes[idx], self._num_images)
        return fut

    def _prefetch_next(self, after_idx: int) -> None:
        """Start fetching the next barcode's previews while the user decides."""
        for idx in range(after_idx + 1, len(self._barcodes)):
            if self._is_pending(self._barcodes[idx]):
                if idx not in self._prefetch:
                    self._prefetch[idx] = self._executor.submit(
                        _fetch_candidates, self._barcodes[idx], self._num_images)
                return

    def _process_next_barcode(self) -> None:
        if self._cancelled:
            return
        if self._current_idx >= len(self._barcodes):
            self._on_batch_done()
            return

        idx = self._current_idx
        barcode = self._barcodes[idx]

        if not sanitize_barcode(barcode):
            self._log(f"[ERROR] {barcode} — empty after sanitization")
            self._advance()
            return

        if os.path.exists(self._output_path(barcode)):
            self._log(f"[SKIP] {barcode} — file already exists")
            self._advance()
            return

        self._barcode_label.configure(text=f"Barcode: {barcode}")
        self._status_label.configure(text="Searching for images…", text_color="yellow")
        self._clear_previews()
        self._set_buttons_enabled(False)

        batch_id = self._batch_id
        fut = self._fetch_for(idx)
        fut.add_done_callback(
            lambda f: self._post(self._on_fetch_done, batch_id, idx, barcode, f))

    def _on_fetch_done(self, batch_id: int, idx: int, barcode: str, fut: Future) -> None:
        if batch_id != self._batch_id or self._cancelled or idx != self._current_idx:
            return
        exc = fut.exception()
        if exc is not None:
            self._on_search_error(barcode, str(exc))
            return
        urls, previews = fut.result()
        self._show_previews(barcode, urls, previews)
        if urls:
            self._prefetch_next(idx)

    def _on_search_error(self, barcode: str, error: str) -> None:
        if self._cancelled:
            return
        self._log(f"[ERROR] {barcode} — {error}")
        self._status_label.configure(text="Error searching", text_color="red")
        self._advance()

    def _show_previews(self, barcode: str, urls: list[str], previews: list[PreviewInfo]) -> None:
        if self._cancelled:
            return
        self._image_urls = urls
        self._previews = previews
        self._clear_previews()

        if not urls:
            self._status_label.configure(text="No images found", text_color="orange")
            self._log(f"[WARN] {barcode} — no images found")
            self._advance()
            return

        count = len(urls)

        for i in range(count):
            var = self._check_vars[i]
            var.set(False)

            grid_row = i // _GRID_COLS
            grid_col = i % _GRID_COLS

            slot = ctk.CTkFrame(self._slots_frame, fg_color="transparent")
            slot.grid(row=grid_row, column=grid_col, padx=6, pady=4)

            img_lbl = ctk.CTkLabel(slot, text="", width=_THUMB_SIZE, height=_THUMB_SIZE)
            img_lbl.pack()

            info = previews[i] if i < len(previews) else PreviewInfo(image=None)

            if info.image is not None:
                ctk_img = ctk.CTkImage(light_image=info.image, size=info.image.size)
                img_lbl.configure(image=ctk_img, text="")
                img_lbl._ctk_image = ctk_img
            else:
                img_lbl.configure(text="Preview\nfailed", font=ctk.CTkFont(size=10))

            ctk.CTkLabel(
                slot,
                text=info.size_label or "size unknown",
                font=ctk.CTkFont(size=10),
                text_color="gray",
            ).pack(pady=(2, 0))

            row = ctk.CTkFrame(slot, fg_color="transparent")
            row.pack(pady=(4, 0), fill="x")

            cb = ctk.CTkCheckBox(
                row, text=f"Image {i + 1}", variable=var, state="normal")
            cb.pack(side="left")

            save_btn = ctk.CTkButton(
                row, text="⬇", width=26, height=24,
                fg_color="#2d8a4e", hover_color="#236b3c",
                font=ctk.CTkFont(size=13),
                command=lambda idx=i: self._on_save_single(idx),
            )
            save_btn.pack(side="right")

            slot_url = urls[i]
            link_text = slot_url[:20] + ("…" if len(slot_url) > 20 else "")
            link_btn = ctk.CTkButton(
                slot,
                text=link_text,
                width=_THUMB_SIZE,
                height=16,
                fg_color="transparent",
                hover_color="#1f3a50",
                text_color="#3b8ed0",
                font=ctk.CTkFont(size=10),
                anchor="w",
                command=lambda u=slot_url: self._open_url(u),
            )
            link_btn.pack(pady=(2, 4))

            self._slot_widgets.append(slot)
            self._img_labels.append(img_lbl)
            self._checkboxes.append(cb)
            self._slot_save_btns.append(save_btn)

        self._status_label.configure(
            text=f"Found {count} image{'s' if count > 1 else ''} — "
                 "click ⬇ to save one instantly, or check several and Save Selected",
            text_color="#3b8ed0")
        self._btn_save_all.configure(state="normal")
        self._btn_save.configure(state="normal")
        self._btn_skip.configure(state="normal")
        self._btn_panel.grid()
        self._fit_window()

    @staticmethod
    def _open_url(url: str) -> None:
        if is_safe_url(url):
            webbrowser.open(url)

    # ------------------------------------------------------------------ #
    #  Selection handlers                                                  #
    # ------------------------------------------------------------------ #

    def _on_save_all(self) -> None:
        selected = list(range(len(self._image_urls)))
        self._save_images(selected)

    def _on_save_single(self, idx: int) -> None:
        """Save just this image and move straight on to the next barcode."""
        if idx >= len(self._image_urls):
            return
        self._save_images([idx])

    def _on_save_selected(self) -> None:
        selected = [i for i in range(len(self._image_urls)) if self._check_vars[i].get()]
        if not selected:
            messagebox.showwarning("No Selection", "Please check at least one image to save.")
            return
        self._save_images(selected)

    def _save_images(self, selected: list[int]) -> None:
        self._set_buttons_enabled(False)
        self._status_label.configure(text="Saving images…", text_color="yellow")
        barcode = self._barcodes[self._current_idx]
        base = self._get_filename_base(barcode)  # Tk read — keep on UI thread
        jobs: list[tuple[str, str, bytes | None]] = []
        for save_num, slot_idx in enumerate(selected):
            filename = f"{base}.jpg" if save_num == 0 else f"{base}-{save_num + 1}.jpg"
            info = self._previews[slot_idx] if slot_idx < len(self._previews) else None
            raw = info.raw if info is not None else None
            jobs.append((self._image_urls[slot_idx], filename, raw))
        thread = threading.Thread(
            target=self._save_selected_thread,
            args=(self._batch_id, barcode, self._output_dir, jobs),
            daemon=True)
        thread.start()

    def _save_selected_thread(
        self,
        batch_id: int,
        barcode: str,
        output_dir: str,
        jobs: list[tuple[str, str, bytes | None]],
    ) -> None:
        saved_count = 0

        for url, filename, raw in jobs:
            if self._cancelled or batch_id != self._batch_id:
                break
            out_path = os.path.join(output_dir, filename)
            try:
                # Reuse the bytes fetched for the preview; only re-download if missing
                data = raw if raw else download_image_bytes(url)
                save_as_jpg(data, out_path)
                saved_count += 1
                self._post(self._log, f"[SAVED] {barcode} — {filename}")
            except Exception as exc:
                self._post(self._log, f"[ERROR] {barcode} — {filename}: {exc}")

        self._post(self._on_save_done, batch_id, saved_count)

    def _on_save_done(self, batch_id: int, saved_count: int) -> None:
        if saved_count > 0:
            self._open_btn.configure(state="normal")
        if self._cancelled or batch_id != self._batch_id:
            return
        self._advance()

    def _on_skip(self) -> None:
        barcode = self._barcodes[self._current_idx]
        self._log(f"[SKIP] {barcode} — skipped by user")
        self._set_buttons_enabled(False)
        self._advance()

    def _advance(self) -> None:
        if self._cancelled:
            return
        self._current_idx += 1
        total = len(self._barcodes)
        self._progress.set(self._current_idx / total)
        self._progress_label.configure(text=f"{self._current_idx} / {total}")
        batch_id = self._batch_id
        self.after(30, lambda: batch_id == self._batch_id and self._process_next_barcode())

    def _on_batch_done(self) -> None:
        self._progress.set(1.0)
        self._progress_label.configure(text=f"{len(self._barcodes)} / {len(self._barcodes)}")
        self._status_label.configure(text="Batch complete!", text_color="#2d8a4e")
        self._log("[INFO] Batch complete.")
        self._reset_to_idle()

    def _on_cancel_all(self) -> None:
        """Abort the whole batch and return the UI to a fresh start state."""
        if not self._running:
            return

        done = self._auto_done if self._auto_first else self._current_idx
        remaining = max(0, len(self._barcodes) - done)
        if not messagebox.askyesno(
            "Cancel All",
            f"Stop the current batch?\n\n{remaining} barcode(s) not yet processed. "
            "Images already saved stay on disk.",
        ):
            return

        self._cancelled = True
        self._batch_id += 1  # late results from in-flight work are now ignored
        for fut in self._auto_futures + list(self._prefetch.values()):
            fut.cancel()  # drops queued work; a download already running finishes its file
        self._log(f"[INFO] Cancelled by user — {remaining} barcode(s) not processed.")
        self._progress.set(0)
        self._progress_label.configure(text="")
        self._status_label.configure(
            text="Cancelled — edit the barcodes and click Download Images to start over",
            text_color="orange")
        self._reset_to_idle()

    def _reset_to_idle(self) -> None:
        """Common teardown after a batch finishes or is cancelled."""
        self._barcode_label.configure(text="")
        self._clear_previews()
        self._set_buttons_enabled(False)
        self._barcodes = []
        self._current_idx = 0
        self._image_urls = []
        self._previews = []
        self._prefetch = {}
        self._auto_futures = []
        self._download_btn.configure(state="normal")
        self._download_all_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")
        self._barcode_text.configure(state="normal")
        self._qty_entry.configure(state="normal")
        self._initials_entry.configure(state="normal")
        self._running = False
        self._fit_window()

    # ------------------------------------------------------------------ #
    #  Preview helpers                                                     #
    # ------------------------------------------------------------------ #

    def _clear_previews(self) -> None:
        for w in self._slot_widgets:
            w.destroy()
        self._slot_widgets.clear()
        self._img_labels.clear()
        self._checkboxes.clear()
        self._slot_save_btns.clear()
        for v in self._check_vars:
            v.set(False)
        self._btn_panel.grid_remove()

    def _fit_window(self) -> None:
        self.update_idletasks()
        req_w = self._inner.winfo_reqwidth() + 20  # scrollbar
        req_h = self._inner.winfo_reqheight()
        screen_h = self.winfo_screenheight() - 80  # taskbar margin
        w = max(req_w, 750)
        h = min(req_h + 40, screen_h)
        sx = self.winfo_screenwidth()
        sy = self.winfo_screenheight()
        x = max(0, (sx - w) // 2)
        y = max(0, (sy - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._btn_save_all.configure(state=state)
        self._btn_save.configure(state=state)
        self._btn_skip.configure(state=state)
        if not enabled:
            for cb in self._checkboxes:
                cb.configure(state="disabled")
            for btn in self._slot_save_btns:
                btn.configure(state="disabled")

    # ------------------------------------------------------------------ #
    #  Open folder                                                         #
    # ------------------------------------------------------------------ #

    def _open_folder(self) -> None:
        if self._output_dir and os.path.isdir(self._output_dir):
            import subprocess
            # Normalize to backslashes so Windows Explorer opens the correct folder
            folder = os.path.normpath(self._output_dir)
            subprocess.Popen(["explorer", folder])
        else:
            messagebox.showwarning("Open Folder", "Output folder no longer exists.")

    # ------------------------------------------------------------------ #
    #  Log helpers                                                         #
    # ------------------------------------------------------------------ #

    def _log(self, message: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", message + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ------------------------------------------------------------------ #
    #  Auto-update from GitHub Releases                                    #
    # ------------------------------------------------------------------ #

    def _start_update_check(self) -> None:
        threading.Thread(target=self._update_check_thread, daemon=True).start()

    def _on_check_updates(self) -> None:
        if self._running:
            return
        self._update_btn.configure(state="disabled", text="Checking…")
        threading.Thread(target=self._manual_update_check_thread, daemon=True).start()

    def _manual_update_check_thread(self) -> None:
        try:
            result = check_update_status(APP_VERSION)
        except Exception as exc:
            self._post(self._on_manual_update_result, "error", None, str(exc))
            return
        self._post(
            self._on_manual_update_result,
            result.status,
            result.info,
            result.message,
        )

    def _on_manual_update_result(
        self,
        status: str,
        info: UpdateInfo | None,
        message: str,
    ) -> None:
        self._update_btn.configure(state="normal", text="Check for updates")
        if status == "available" and info is not None:
            self._prompt_update(info)
        elif status == "current":
            messagebox.showinfo(
                "Check for updates",
                f"You already have the latest version (v{APP_VERSION}).",
            )
        else:
            detail = message or "Could not reach the update server."
            messagebox.showwarning(
                "Check for updates",
                f"Could not check for updates.\n\n{detail}",
            )

    def _update_check_thread(self) -> None:
        try:
            info = check_for_update(APP_VERSION)
        except Exception:
            return
        if info is not None:
            self._post(self._prompt_update, info)

    def _prompt_update(self, info: UpdateInfo) -> None:
        if self._running:
            return
        update_now = messagebox.askyesno(
            "Update available",
            f"Version {info.version} is available (you have {APP_VERSION}).\n\n"
            "Update now?",
        )
        if not update_now:
            return
        self._status_label.configure(text="Downloading update…", text_color="yellow")
        threading.Thread(target=self._download_update_thread, args=(info,), daemon=True).start()

    def _download_update_thread(self, info: UpdateInfo) -> None:
        try:
            path = download_update(info)
            self._post(self._apply_downloaded_update, path)
        except Exception as exc:
            self._post(self._on_update_failed, str(exc))

    def _on_update_failed(self, error: str) -> None:
        self._status_label.configure(text="Update failed", text_color="red")
        messagebox.showerror("Update failed", error)

    def _apply_downloaded_update(self, path: str) -> None:
        if not is_frozen():
            messagebox.showinfo(
                "Update downloaded",
                "This copy is running from source, so it was not replaced.\n"
                f"The new file is at:\n{path}",
            )
            return
        try:
            exe = install_update(path)
        except Exception as exc:
            self._on_update_failed(
                f"{exc}\n\nIf the app is in a protected folder (e.g. Program Files), "
                "move it to Desktop or Documents and try again.")
            return
        try:
            relaunch(exe)
        except Exception as exc:
            messagebox.showinfo(
                "Update installed",
                f"Version installed, but it could not restart automatically ({exc}).\n"
                "Please open the app again.",
            )
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()
    # Worker threads may still be waiting on a slow image server; don't let
    # them keep an invisible process alive after the window closes.
    os._exit(0)


if __name__ == "__main__":
    main()
