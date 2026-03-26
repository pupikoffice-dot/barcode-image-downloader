"""Barcode Image Downloader — main UI application."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from downloader import BarcodeResult, load_credentials, run_batch

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ENV_PATH = str(Path(__file__).parent / ".env")

_STATUS_PREFIX = {
    "saved": "[SAVED]",
    "skipped": "[SKIP]",
    "no_image": "[WARN]",
    "error": "[ERROR]",
}


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Barcode Image Downloader")
        self.geometry("700x600")
        self.resizable(False, False)

        self._output_dir: str = ""
        self._running = False

        self._build_ui()

    # ------------------------------------------------------------------ #
    #  UI construction                                                     #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        # 1. Label: barcode input
        ctk.CTkLabel(
            self, text="Paste barcodes (one per line):",
        ).grid(row=0, column=0, padx=20, pady=(16, 0), sticky="w")

        # 2. Barcode text area (~8 lines ≈ 160 px)
        self._barcode_text = ctk.CTkTextbox(self, height=160)
        self._barcode_text.grid(row=1, column=0, padx=20, pady=(4, 0), sticky="ew")
        # Insert placeholder hint (light grey look via tag not available in CTkTextbox,
        # so we use a simple placeholder string the user clears on first edit)
        _placeholder = "1234567890123\n9876543210987\n..."
        self._barcode_text.insert("1.0", _placeholder)
        self._barcode_placeholder_active = True
        self._barcode_text.bind("<FocusIn>", self._on_barcode_focus_in)
        self._barcode_text.bind("<FocusOut>", self._on_barcode_focus_out)

        # 3. Output folder row
        folder_frame = ctk.CTkFrame(self, fg_color="transparent")
        folder_frame.grid(row=2, column=0, padx=20, pady=(10, 0), sticky="ew")
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

        # 4. Download Images button (full width)
        self._download_btn = ctk.CTkButton(
            self, text="Download Images",
            command=self._start_download,
        )
        self._download_btn.grid(row=3, column=0, pady=(10, 0), padx=20, sticky="ew")

        # 5. Progress bar
        self._progress = ctk.CTkProgressBar(self)
        self._progress.grid(row=4, column=0, padx=20, pady=(8, 0), sticky="ew")
        self._progress.set(0)

        # 6. Log text area (~10 lines ≈ 200 px, read-only)
        ctk.CTkLabel(self, text="Log:").grid(
            row=5, column=0, padx=20, pady=(10, 0), sticky="w")
        self._log_box = ctk.CTkTextbox(self, height=200, state="disabled")
        self._log_box.grid(row=6, column=0, padx=20, pady=(4, 0), sticky="ew")

        # 7. Open Folder button (disabled at startup)
        self._open_btn = ctk.CTkButton(
            self, text="Open Folder",
            command=self._open_folder, state="disabled",
        )
        self._open_btn.grid(row=7, column=0, pady=(8, 16), padx=20, sticky="ew")

    # ------------------------------------------------------------------ #
    #  Placeholder behaviour for barcode text area                        #
    # ------------------------------------------------------------------ #

    def _on_barcode_focus_in(self, _event=None) -> None:
        if self._barcode_placeholder_active:
            self._barcode_text.delete("1.0", "end")
            self._barcode_placeholder_active = False

    def _on_barcode_focus_out(self, _event=None) -> None:
        if not self._barcode_text.get("1.0", "end").strip():
            self._barcode_text.insert("1.0", "1234567890123\n9876543210987\n...")
            self._barcode_placeholder_active = True

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
    #  Download                                                            #
    # ------------------------------------------------------------------ #

    def _start_download(self) -> None:
        if self._running:
            return

        # Read barcodes (ignore placeholder)
        raw_text = "" if self._barcode_placeholder_active else self._barcode_text.get("1.0", "end").strip()
        barcodes = [b.strip() for b in raw_text.splitlines() if b.strip()]

        # Validate inputs
        if not barcodes:
            messagebox.showerror(
                "Validation Error",
                "Please enter at least one barcode.",
            )
            return

        if not self._output_dir:
            messagebox.showerror(
                "Validation Error",
                "Please select an output folder.",
            )
            return

        # Load credentials — show error and stop if invalid
        try:
            api_key, cx_id = load_credentials(env_path=ENV_PATH)
        except ValueError as exc:
            messagebox.showerror("Credentials Error", str(exc))
            return

        # Prepare UI for batch run
        self._running = True
        self._download_btn.configure(state="disabled")
        self._progress.set(0)
        self._clear_log()

        thread = threading.Thread(
            target=self._run_batch_thread,
            args=(barcodes, api_key, cx_id),
            daemon=True,
        )
        thread.start()

    def _run_batch_thread(self, barcodes: list[str], api_key: str, cx_id: str) -> None:
        def on_progress(result: BarcodeResult, idx: int, total: int) -> None:
            self.after(0, self._on_result, result, idx, total)

        run_batch(
            barcodes=barcodes,
            output_dir=self._output_dir,
            api_key=api_key,
            cx_id=cx_id,
            progress_callback=on_progress,
        )
        self.after(0, self._on_batch_done)

    def _on_result(self, result: BarcodeResult, idx: int, total: int) -> None:
        self._progress.set(idx / total)
        prefix = _STATUS_PREFIX.get(result.status, "[INFO]")
        self._log(f"{prefix} {result.barcode} — {result.message}")
        if result.status == "saved":
            self._open_btn.configure(state="normal")

    def _on_batch_done(self) -> None:
        self._progress.set(1.0)
        self._log("[INFO] Batch complete.")
        self._download_btn.configure(state="normal")
        self._running = False

    # ------------------------------------------------------------------ #
    #  Open folder                                                         #
    # ------------------------------------------------------------------ #

    def _open_folder(self) -> None:
        if self._output_dir and os.path.isdir(self._output_dir):
            os.startfile(self._output_dir)
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


if __name__ == "__main__":
    app = App()
    app.mainloop()
