"""Barcode Image Downloader — main UI application."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from downloader import BarcodeResult, load_credentials, run_batch

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ENV_PATH = str(Path(__file__).parent / ".env")


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Barcode Image Downloader")
        self.geometry("700x620")
        self.resizable(False, False)

        self._output_dir: str = ""
        self._running = False

        self._build_ui()
        self._validate_credentials()

    # ------------------------------------------------------------------ #
    #  UI construction                                                     #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)   # barcode textbox row expands

        # Title
        ctk.CTkLabel(
            self, text="Barcode Image Downloader",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, pady=(16, 4), padx=20, sticky="w")

        # Barcode input area
        ctk.CTkLabel(
            self, text="Paste barcodes (one per line):",
        ).grid(row=1, column=0, padx=20, pady=(4, 0), sticky="w")

        self._barcode_text = ctk.CTkTextbox(self, height=200)
        self._barcode_text.grid(row=2, column=0, padx=20, sticky="ew")

        # Output folder row
        folder_frame = ctk.CTkFrame(self, fg_color="transparent")
        folder_frame.grid(row=3, column=0, padx=20, pady=(10, 0), sticky="ew")
        folder_frame.grid_columnconfigure(0, weight=1)

        self._folder_var = ctk.StringVar(value="No folder selected")
        ctk.CTkLabel(folder_frame, textvariable=self._folder_var, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            folder_frame, text="Browse…", width=100,
            command=self._browse_folder,
        ).grid(row=0, column=1)

        # Progress bar
        self._progress = ctk.CTkProgressBar(self)
        self._progress.grid(row=4, column=0, padx=20, pady=(10, 0), sticky="ew")
        self._progress.set(0)

        # Download button
        self._download_btn = ctk.CTkButton(
            self, text="Download Images",
            command=self._start_download,
        )
        self._download_btn.grid(row=5, column=0, pady=(10, 0), padx=20, sticky="ew")

        # Log area
        ctk.CTkLabel(self, text="Log:").grid(
            row=6, column=0, padx=20, pady=(10, 0), sticky="w")
        self._log_box = ctk.CTkTextbox(self, height=160, state="disabled")
        self._log_box.grid(row=7, column=0, padx=20, sticky="ew")

        # Open folder button (disabled until at least one image saved)
        self._open_btn = ctk.CTkButton(
            self, text="Open Folder",
            command=self._open_folder, state="disabled",
        )
        self._open_btn.grid(row=8, column=0, pady=(8, 16), padx=20, sticky="ew")

    # ------------------------------------------------------------------ #
    #  Credential validation                                               #
    # ------------------------------------------------------------------ #

    def _validate_credentials(self) -> None:
        try:
            load_credentials(env_path=ENV_PATH)
        except (ValueError, OSError) as exc:
            self._log(f"[ERROR] {exc}")
            self._log("[INFO] Create a .env file next to app.py with GOOGLE_API_KEY and GOOGLE_CX_ID.")
            self._download_btn.configure(state="disabled")

    # ------------------------------------------------------------------ #
    #  Folder browsing                                                     #
    # ------------------------------------------------------------------ #

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self._output_dir = folder
            self._folder_var.set(folder)

    # ------------------------------------------------------------------ #
    #  Download                                                            #
    # ------------------------------------------------------------------ #

    def _start_download(self) -> None:
        if self._running:
            return

        barcodes_raw = self._barcode_text.get("1.0", "end").strip()
        if not barcodes_raw:
            self._log("[WARN] No barcodes entered.")
            return

        if not self._output_dir:
            self._log("[WARN] Please select an output folder first.")
            return

        barcodes = [b.strip() for b in barcodes_raw.splitlines() if b.strip()]
        if not barcodes:
            self._log("[WARN] No valid barcodes found.")
            return

        try:
            api_key, cx_id = load_credentials(env_path=ENV_PATH)
        except ValueError as exc:
            self._log(f"[ERROR] Credentials error: {exc}")
            return

        self._running = True
        self._download_btn.configure(state="disabled")
        self._progress.set(0)
        self._log(f"[INFO] Starting batch of {len(barcodes)} barcode(s)…")

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
        status_prefix = {
            "saved": "[INFO]",
            "skipped": "[INFO]",
            "no_image": "[WARN]",
            "error": "[ERROR]",
        }.get(result.status, "[INFO]")
        self._log(f"{status_prefix} {result.barcode} → {result.message}")
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
            if sys.platform == "win32":
                subprocess.Popen(["explorer", self._output_dir])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self._output_dir])
            else:
                subprocess.Popen(["xdg-open", self._output_dir])
        else:
            self._log("[WARN] Output folder no longer exists.")

    # ------------------------------------------------------------------ #
    #  Log helper                                                          #
    # ------------------------------------------------------------------ #

    def _log(self, message: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", message + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
