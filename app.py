"""Barcode Image Downloader — interactive preview & selection UI."""
from __future__ import annotations

import os
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk  # noqa: F401  (kept so PyInstaller bundles PIL's tk bridge)

from downloader import (
    BarcodeResult,
    PreviewInfo,
    download_first_image,
    download_image_bytes,
    download_preview_info,
    sanitize_barcode,
    save_as_jpg,
    search_image_urls,
)
from updater import (
    APP_VERSION,
    UpdateInfo,
    check_update_status,
    apply_update,
    check_for_update,
    download_update,
    is_frozen,
    launch_helper,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_PLACEHOLDER = "1234567890123\n9876543210987\n..."
_MAX_SLOTS = 20
_THUMB_SIZE = 120
_GRID_COLS = 4


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

        self._check_vars: list[ctk.BooleanVar] = [ctk.BooleanVar(value=False) for _ in range(_MAX_SLOTS)]
        self._naming_var = ctk.StringVar(value="barcode")

        self._build_ui()
        self._fit_window()
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

        self._barcode_text = ctk.CTkTextbox(parent, height=100)
        self._barcode_text.grid(row=1, column=0, padx=20, pady=(4, 0), sticky="ew")
        self._barcode_text.insert("1.0", _PLACEHOLDER)
        self._barcode_placeholder_active = True
        self._barcode_text.bind("<FocusIn>", self._on_barcode_focus_in)
        self._barcode_text.bind("<FocusOut>", self._on_barcode_focus_out)

        # 2. Output folder row
        folder_frame = ctk.CTkFrame(parent, fg_color="transparent")
        folder_frame.grid(row=2, column=0, padx=20, pady=(8, 0), sticky="ew")
        folder_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(folder_frame, text="Output folder:").grid(
            row=0, column=0, padx=(0, 8), sticky="w")

        self._folder_entry = ctk.CTkEntry(
            folder_frame, placeholder_text="No folder selected", state="readonly")
        self._folder_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            folder_frame, text="Browse", width=80,
            command=self._browse_folder,
        ).grid(row=0, column=2)

        # 3. Qty images row
        qty_frame = ctk.CTkFrame(parent, fg_color="transparent")
        qty_frame.grid(row=3, column=0, padx=20, pady=(8, 0), sticky="ew")

        ctk.CTkLabel(qty_frame, text="Qty images:").grid(
            row=0, column=0, padx=(0, 8), sticky="w")

        self._qty_var = ctk.StringVar(value="4")
        self._qty_entry = ctk.CTkEntry(qty_frame, width=60, textvariable=self._qty_var)
        self._qty_entry.grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(qty_frame, text="(number of preview images per barcode)",
                     text_color="gray").grid(row=0, column=2, padx=(10, 0), sticky="w")

        # 4. Naming choice row
        naming_frame = ctk.CTkFrame(parent, fg_color="transparent")
        naming_frame.grid(row=4, column=0, padx=20, pady=(8, 0), sticky="ew")

        ctk.CTkLabel(naming_frame, text="File naming:").grid(
            row=0, column=0, padx=(0, 12), sticky="w")

        ctk.CTkRadioButton(
            naming_frame, text="Barcode", variable=self._naming_var,
            value="barcode", command=self._on_naming_changed,
        ).grid(row=0, column=1, padx=(0, 16), sticky="w")

        ctk.CTkRadioButton(
            naming_frame, text="Initials", variable=self._naming_var,
            value="initials", command=self._on_naming_changed,
        ).grid(row=0, column=2, padx=(0, 8), sticky="w")

        self._initials_entry = ctk.CTkEntry(
            naming_frame, width=80, placeholder_text="e.g. FNK")
        self._initials_entry.grid(row=0, column=3, padx=(0, 8), sticky="w")

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

        self._download_all_btn = ctk.CTkButton(
            action_frame, text="Download all", width=130,
            fg_color="#1a6334", hover_color="#14502a",
            command=self._start_download_all,
        )
        self._download_all_btn.grid(row=0, column=1, padx=(0, 8))

        self._cancel_btn = ctk.CTkButton(
            action_frame, text="Cancel All", width=130,
            fg_color="#a33232", hover_color="#822828",
            command=self._on_cancel_all, state="disabled",
        )
        self._cancel_btn.grid(row=0, column=2)

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

        self._btn_save = ctk.CTkButton(
            self._btn_panel, text="Save Selected", width=130,
            fg_color="#2d8a4e", hover_color="#236b3c",
            command=self._on_save_selected, state="disabled")
        self._btn_save.pack(pady=(0, 8))

        self._btn_skip = ctk.CTkButton(
            self._btn_panel, text="Skip", width=130,
            fg_color="#6b6b6b", hover_color="#555555",
            command=self._on_skip, state="disabled")
        self._btn_skip.pack()

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

        # Bottom spacer
        ctk.CTkLabel(parent, text="").grid(row=9, column=0, pady=(0, 12))

    # ------------------------------------------------------------------ #
    #  Placeholder behaviour                                              #
    # ------------------------------------------------------------------ #

    def _on_barcode_focus_in(self, _event=None) -> None:
        if self._barcode_placeholder_active:
            self._barcode_text.delete("1.0", "end")
            self._barcode_placeholder_active = False

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
            initials = self._initials_entry.get().strip()
            if not initials:
                initials = "IMG"
            # Extract characters at positions 7–11 (0-based index 6:11)
            digits = barcode[6:11] if len(barcode) > 6 else barcode
            # Strip leading zeros (e.g. 09926 → 9926)
            digits = digits.lstrip("0") or digits  # keep original if all zeros
            return f"{initials}-{digits}"
        return sanitize_barcode(barcode)

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

        self._process_next_barcode()

    def _process_next_barcode(self) -> None:
        if self._cancelled:
            return
        if self._current_idx >= len(self._barcodes):
            self._on_batch_done()
            return

        barcode = self._barcodes[self._current_idx]
        safe = sanitize_barcode(barcode)

        if not safe:
            self._log(f"[ERROR] {barcode} — empty after sanitization")
            self._advance()
            return

        base = self._get_filename_base(barcode)
        out_path = os.path.join(self._output_dir, f"{base}.jpg")
        if os.path.exists(out_path):
            self._log(f"[SKIP] {barcode} — file already exists")
            self._advance()
            return

        self._barcode_label.configure(text=f"Barcode: {barcode}")
        self._status_label.configure(text="Searching for images…", text_color="yellow")
        self._clear_previews()
        self._set_buttons_enabled(False)

        if self._auto_first:
            thread = threading.Thread(
                target=self._auto_first_thread, args=(barcode,), daemon=True)
            thread.start()
            return

        thread = threading.Thread(
            target=self._search_thread, args=(barcode,), daemon=True)
        thread.start()

    def _auto_first_thread(self, barcode: str) -> None:
        if self._cancelled:
            return
        base = self._get_filename_base(barcode)
        out_path = os.path.join(self._output_dir, f"{base}.jpg")
        try:
            result = download_first_image(barcode, out_path)
        except Exception as exc:
            result = BarcodeResult(barcode, "error", str(exc))
        self.after(0, self._on_auto_first_done, result)

    def _on_auto_first_done(self, result: BarcodeResult) -> None:
        if self._cancelled:
            return
        if result.status == "saved":
            self._log(f"[SAVED] {result.barcode} — {result.message}")
            self._open_btn.configure(state="normal")
            self._status_label.configure(text="Saved first image", text_color="#2d8a4e")
        elif result.status == "skipped":
            self._log(f"[SKIP] {result.barcode} — {result.message}")
        elif result.status == "no_image":
            self._log(f"[WARN] {result.barcode} — {result.message}")
            self._status_label.configure(text="No images found", text_color="orange")
        else:
            self._log(f"[ERROR] {result.barcode} — {result.message}")
            self._status_label.configure(text="Error saving", text_color="red")
        self._advance()

    def _search_thread(self, barcode: str) -> None:
        try:
            urls = search_image_urls(barcode, max_results=self._num_images)
            previews: list[PreviewInfo] = []
            for url in urls:
                previews.append(download_preview_info(url, max_size=_THUMB_SIZE))
            self.after(0, self._show_previews, barcode, urls, previews)
        except Exception as exc:
            self.after(0, self._on_search_error, barcode, str(exc))

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
                command=lambda u=slot_url: webbrowser.open(u),
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
        self._status_label.configure(text="Downloading full images…", text_color="yellow")
        barcode = self._barcodes[self._current_idx]
        thread = threading.Thread(
            target=self._save_selected_thread, args=(barcode, selected), daemon=True)
        thread.start()

    def _save_selected_thread(self, barcode: str, selected: list[int]) -> None:
        base = self._get_filename_base(barcode)
        saved_count = 0

        for save_num, slot_idx in enumerate(selected):
            if self._cancelled:
                break
            url = self._image_urls[slot_idx]
            if save_num == 0:
                filename = f"{base}.jpg"
            else:
                filename = f"{base}-{save_num + 1}.jpg"
            out_path = os.path.join(self._output_dir, filename)

            try:
                raw = download_image_bytes(url)
                save_as_jpg(raw, out_path)
                saved_count += 1
                self.after(0, self._log, f"[SAVED] {barcode} — {filename}")
            except Exception as exc:
                self.after(0, self._log, f"[ERROR] {barcode} — {filename}: {exc}")

        self.after(0, self._on_save_done, saved_count)

    def _on_save_done(self, saved_count: int) -> None:
        if saved_count > 0:
            self._open_btn.configure(state="normal")
        if self._cancelled:
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
        self.after(300, self._process_next_barcode)

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

        remaining = max(0, len(self._barcodes) - self._current_idx)
        if not messagebox.askyesno(
            "Cancel All",
            f"Stop the current batch?\n\n{remaining} barcode(s) not yet processed. "
            "Images already saved stay on disk.",
        ):
            return

        self._cancelled = True
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
            self.after(0, self._on_manual_update_result, "error", None, str(exc))
            return
        self.after(
            0,
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
            self.after(0, self._prompt_update, info)

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
            self.after(0, self._apply_downloaded_update, path)
        except Exception as exc:
            self.after(0, self._on_update_failed, str(exc))

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
            script = apply_update(path)
            launch_helper(script)
        except Exception as exc:
            self._on_update_failed(str(exc))
            return
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
