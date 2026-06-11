from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from src.logger.app_logger import list_log_files, read_log_file


class LogViewerDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Napló előzmények")
        self.geometry("900x520")
        self.minsize(640, 360)
        self.transient(parent)

        toolbar = ttk.Frame(self, padding=12)
        toolbar.pack(fill="x")

        ttk.Label(toolbar, text="Log fájl:").pack(side="left")
        self._file_var = tk.StringVar()
        self._file_combo = ttk.Combobox(
            toolbar,
            textvariable=self._file_var,
            state="readonly",
            width=48,
        )
        self._file_combo.pack(side="left", padx=(8, 0))
        self._file_combo.bind("<<ComboboxSelected>>", self._on_file_selected)

        ttk.Button(toolbar, text="Frissítés", command=self._reload_current).pack(
            side="left", padx=(8, 0)
        )

        self._text = scrolledtext.ScrolledText(
            self,
            wrap="word",
            font=("Consolas", 11),
        )
        self._text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._text.configure(state="disabled")

        self._files: list[Path] = []
        self._load_file_list()

    def _load_file_list(self) -> None:
        self._files = list_log_files()
        labels = [path.name for path in self._files]
        self._file_combo["values"] = labels

        if labels:
            self._file_var.set(labels[0])
            self._show_file(self._files[0])
        else:
            self._set_text("Még nincs napló fájl.")

    def _on_file_selected(self, _event: tk.Event | None = None) -> None:
        name = self._file_var.get()
        for path in self._files:
            if path.name == name:
                self._show_file(path)
                return

    def _reload_current(self) -> None:
        self._files = list_log_files()
        self._file_combo["values"] = [path.name for path in self._files]

        name = self._file_var.get()
        for path in self._files:
            if path.name == name:
                self._show_file(path)
                return

        if self._files:
            self._file_var.set(self._files[0].name)
            self._show_file(self._files[0])
        else:
            messagebox.showinfo("Napló", "Nincs elérhető napló fájl.")

    def _show_file(self, path: Path) -> None:
        content = read_log_file(path)
        if not content:
            self._set_text(f"Üres napló: {path.name}")
            return
        self._set_text(content)

    def _set_text(self, content: str) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("1.0", content)
        self._text.see("1.0")
        self._text.configure(state="disabled")
