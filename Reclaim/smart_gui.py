import os
import sys
import shutil
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False
    DND_FILES = None
    TkinterDnD = None

from hole_punch import get_allocated_size
from state_manager import (
    completed_file_is_valid,
    get_completed_record,
    load_state,
)
from smart_extract import (
    COLLISION_CANCEL,
    COLLISION_RENAME,
    COLLISION_REPLACE,
    COLLISION_SKIP,
    analyze_archive,
    find_collisions,
    smart_extract,
)
def resource_path(filename):
    """
    Return the correct path for a resource in both:
    - normal Python execution
    - PyInstaller packaged execution
    """

    if getattr(sys, "frozen", False):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).resolve().parent

    return base_path / filename
def resource_path(filename):
    """
    Resolve bundled resources both when running from Python
    and when running from a PyInstaller build.
    """
    if getattr(sys, "frozen", False):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).resolve().parent

    return base_path / filename
class ReclaimGUI:
    BG = ("#edf1f5", "#0c1117")
    SURFACE = ("#ffffff", "#151b23")
    ALT = ("#f5f7fa", "#11171f")
    SURFACE_ALT = ALT
    BORDER = ("#d7dfe8", "#283442")
    TEXT = ("#1d2939", "#f4f7fb")
    MUTED = ("#607086", "#9ba8b7")
    BLUE = ("#3f6fd4", "#5b8cff")
    BLUE_HOVER = ("#355fb9", "#719cff")
    BLUE_SOFT = ("#e9effc", "#192643")
    GREEN = ("#2c9f66", "#39c97a")
    GREEN_SOFT = ("#e8f7ef", "#132a20")
    ORANGE = ("#d98a24", "#f0a741")
    ORANGE_SOFT = ("#fff3df", "#2c2111")
    RED = ("#c94f5d", "#ff6876")
    RED_SOFT = ("#ffeff1", "#32171c")
    PURPLE = ("#7055c7", "#8b72eb")

    def __init__(self, root):
        self.root = root
        # ----------------------------------------------------------
        # Application icon
        # ----------------------------------------------------------

        icon_path = resource_path("reclaim.ico")

        if icon_path.exists():
            try:
                self.root.iconbitmap(
                    default=str(icon_path)
                )
            except Exception as error:
                print(
                    f"Warning: could not set application icon: {error}"
                )
        
        self.root.title("Reclaim — Extract. Verify. Reclaim.")
        self.root.geometry("1200x850")
        self.root.minsize(1100, 800)

        self.zip_path = None
        self.output_dir = Path("reclaim_output")
        self.is_running = False
        self.cancel_event = threading.Event()

        self.successful = self.skipped = self.failed = 0
        self.total_reclaimed = 0
        self.start_time = None
        self.last_progress_time = time.time()
        self.last_progress_bytes = 0

        self.archive_name = tk.StringVar(value="No ZIP archive selected")
        self.output_path = tk.StringVar(value=str(self.output_dir))
        self.archive_size = tk.StringVar(value="—")
        self.disk_space = tk.StringVar(value="—")
        self.file_count = tk.StringVar(value="—")
        self.reclaimed = tk.StringVar(value="0 B")
        self.current_file = tk.StringVar(value="Waiting for archive...")
        self.status = tk.StringVar(value="Select a ZIP archive to begin.")
        self.progress_percent = tk.StringVar(value="0%")

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.build_ui()
        self.setup_dnd()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    @staticmethod
    def fmt_bytes(value):
        value = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
            if value < 1024:
                return f"{value:.2f} {unit}"
            value /= 1024
        return f"{value:.2f} EB"

    @staticmethod
    def fmt_time(seconds):
        if seconds is None:
            return "—"
        seconds = max(0, int(seconds))
        h, r = divmod(seconds, 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def build_ui(self):
        self.main = ctk.CTkFrame(self.root, fg_color=self.BG, corner_radius=0)
        self.main.pack(fill="both", expand=True)

        self.header = ctk.CTkFrame(self.main, fg_color="transparent", height=94)
        self.header.pack(fill="x", padx=30, pady=(18, 8))
        self.header.pack_propagate(False)

        left = ctk.CTkFrame(self.header, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(left, text="RECLAIM", font=("Segoe UI", 34, "bold"),
                     text_color=self.TEXT).pack(anchor="w")
        ctk.CTkLabel(left, text="Extract. Verify. Reclaim.",
                     font=("Segoe UI", 15), text_color=self.MUTED).pack(anchor="w")

        right = ctk.CTkFrame(self.header, fg_color="transparent")
        right.pack(side="right")

        self.light_btn = ctk.CTkButton(
            right, text="☀  Light", width=98, height=42,
            fg_color=self.SURFACE, hover_color=self.BLUE_SOFT,
            text_color=self.TEXT, border_width=1, border_color=self.BORDER,
            font=("Segoe UI", 11, "bold"), command=lambda: self.set_theme("light")
        )
        self.light_btn.pack(side="left")

        self.dark_btn = ctk.CTkButton(
            right, text="☾  Dark", width=98, height=42,
            fg_color=self.BLUE_SOFT, hover_color=self.BLUE_SOFT,
            text_color=self.BLUE, font=("Segoe UI", 11, "bold"),
            command=lambda: self.set_theme("dark")
        )
        self.dark_btn.pack(side="left", padx=(5, 12))

        status = ctk.CTkFrame(
            right, width=205, height=58, fg_color=self.SURFACE,
            border_width=1, border_color=self.BORDER, corner_radius=10
        )
        status.pack(side="left")
        status.pack_propagate(False)
        self.status_dot = ctk.CTkLabel(status, text="●", font=("Segoe UI", 13),
                                       text_color=self.GREEN)
        self.status_dot.pack(side="left", padx=(12, 6))
        stext = ctk.CTkFrame(status, fg_color="transparent")
        stext.pack(side="left")
        self.header_status = ctk.CTkLabel(stext, text="READY",
                                          font=("Segoe UI", 11, "bold"),
                                          text_color=self.TEXT)
        self.header_status.pack(anchor="w")
        self.header_sub = ctk.CTkLabel(stext, text="Ready to extract",
                                       font=("Segoe UI", 9), text_color=self.MUTED)
        self.header_sub.pack(anchor="w")

        ctk.CTkButton(
            right, text="⚙", width=50, height=50, fg_color=self.SURFACE,
            hover_color=self.ALT, border_width=1, border_color=self.BORDER,
            text_color=self.TEXT, font=("Segoe UI", 18),
            command=self.show_settings
        ).pack(side="left", padx=(12, 0))

        self.body = ctk.CTkFrame(self.main, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=30)

        self.build_input()
        self.build_stats()
        self.build_progress()

        self.footer = ctk.CTkFrame(self.main, fg_color="transparent", height=84)
        self.footer.pack(fill="x", padx=30, pady=(10, 16))
        self.footer.pack_propagate(False)
        self.build_footer()

    def build_input(self):
        section = ctk.CTkFrame(self.body, fg_color="transparent", height=210)
        section.pack(fill="x", pady=(0, 12))
        section.pack_propagate(False)

        # Archive
        card = ctk.CTkFrame(section, fg_color=self.SURFACE, border_width=1,
                            border_color=self.BORDER, corner_radius=14)
        card.pack(side="left", fill="both", expand=True, padx=(0, 7))

        head = ctk.CTkFrame(card, fg_color="transparent", height=52)
        head.pack(fill="x", padx=16, pady=(11, 4)); head.pack_propagate(False)
        ctk.CTkLabel(head, text="▧", width=44, height=44, corner_radius=11,
                     fg_color=self.BLUE_SOFT, text_color=self.BLUE,
                     font=("Segoe UI", 23)).pack(side="left")
        t = ctk.CTkFrame(head, fg_color="transparent"); t.pack(side="left", padx=10)
        ctk.CTkLabel(t, text="ZIP ARCHIVE", font=("Segoe UI", 14, "bold"),
                     text_color=self.TEXT).pack(anchor="w")
        ctk.CTkLabel(t, text="Select or drag & drop a ZIP archive",
                     font=("Segoe UI", 12), text_color=self.MUTED).pack(anchor="w")

        self.drop_zone = ctk.CTkFrame(card, fg_color=self.ALT, border_width=1,
                                      border_color=self.BORDER, corner_radius=10)
        self.drop_zone.pack(fill="both", expand=True, padx=16, pady=(3, 5))
        ctk.CTkLabel(self.drop_zone, text="♧", font=("Segoe UI", 28),
                     text_color=self.BLUE).pack(pady=(4, 0))
        ctk.CTkLabel(self.drop_zone, text="Drag & drop your ZIP file here",
                     font=("Segoe UI", 13, "bold"), text_color=self.TEXT).pack()
        ctk.CTkLabel(self.drop_zone, text="or", font=("Segoe UI", 10),
                     text_color=self.MUTED).pack()
        self.browse_btn = ctk.CTkButton(
            self.drop_zone, text="Browse Files", width=135, height=34,
            fg_color=self.SURFACE, hover_color=self.BLUE_SOFT,
            border_width=1, border_color=self.BORDER,
            text_color=self.TEXT, font=("Segoe UI", 11, "bold"),
            command=self.select_zip
        )
        self.browse_btn.pack(pady=(1, 0))

        chosen = ctk.CTkFrame(card, fg_color="transparent", height=35)
        chosen.pack(fill="x", padx=16, pady=(0, 4)); chosen.pack_propagate(False)
        ctk.CTkLabel(chosen, text="Selected:", font=("Segoe UI", 10, "bold"),
                     text_color=self.MUTED).pack(side="left")
        ctk.CTkLabel(chosen, textvariable=self.archive_name,
                     font=("Segoe UI", 12, "bold"), text_color=self.TEXT,
                     anchor="w").pack(side="left", fill="x", expand=True, padx=(6, 0))

        # Output
        card = ctk.CTkFrame(section, fg_color=self.SURFACE, border_width=1,
                            border_color=self.BORDER, corner_radius=14)
        card.pack(side="left", fill="both", expand=True, padx=(7, 0))

        head = ctk.CTkFrame(card, fg_color="transparent", height=52)
        head.pack(fill="x", padx=16, pady=(11, 4)); head.pack_propagate(False)
        ctk.CTkLabel(head, text="□", width=44, height=44, corner_radius=11,
                     fg_color=self.GREEN_SOFT, text_color=self.GREEN,
                     font=("Segoe UI", 22)).pack(side="left")
        t = ctk.CTkFrame(head, fg_color="transparent"); t.pack(side="left", padx=10)
        ctk.CTkLabel(t, text="OUTPUT LOCATION", font=("Segoe UI", 14, "bold"),
                     text_color=self.TEXT).pack(anchor="w")
        ctk.CTkLabel(t, text="Choose where extracted files will be placed",
                     font=("Segoe UI", 12), text_color=self.MUTED).pack(anchor="w")

        row = ctk.CTkFrame(card, fg_color=self.ALT, border_width=1,
                           border_color=self.BORDER, corner_radius=9)
        row.pack(fill="x", padx=16, pady=(7, 8))
        ctk.CTkLabel(row, textvariable=self.output_path, font=("Segoe UI", 10),
                     text_color=self.TEXT, anchor="w").pack(
                         side="left", fill="x", expand=True, padx=11, pady=9
                     )
        ctk.CTkButton(
            row, text="Browse Folder", width=135, height=34,
            fg_color=self.SURFACE, hover_color=self.GREEN_SOFT,
            border_width=1, border_color=self.BORDER, text_color=self.TEXT,
            font=("Segoe UI", 11, "bold"), command=self.select_output
        ).pack(side="right", padx=5)

        self.storage_label = ctk.CTkLabel(card, text="", font=("Segoe UI", 10),
                                          text_color=self.MUTED)
        self.storage_label.pack(anchor="w", padx=17)
        self.storage_bar = ctk.CTkProgressBar(card, height=7, corner_radius=5,
                                               fg_color=self.BORDER, progress_color=self.GREEN)
        self.storage_bar.pack(fill="x", padx=17, pady=(4, 10))
        self.storage_bar.set(0)
        self.refresh_storage()

    def build_stats(self):
        section = ctk.CTkFrame(self.body, fg_color="transparent", height=98)
        section.pack(fill="x", pady=(0, 12)); section.pack_propagate(False)

        cards = [
            ("▣", "ARCHIVE SIZE", self.archive_size, "Compressed archive", self.PURPLE),
            ("▱", "DISK SPACE", self.disk_space, "Physical allocation", self.BLUE),
            ("▤", "FILES", self.file_count, "Archive members", self.ORANGE),
            ("♧", "RECLAIMED", self.reclaimed, "Space saved", self.GREEN),
        ]

        soft = {
            self.PURPLE: self.PURPLE[0],
            self.BLUE: self.BLUE[0],
            self.ORANGE: self.ORANGE[0],
            self.GREEN: self.GREEN[0],
        }

        for icon, title, var, sub, color in cards:
            card = ctk.CTkFrame(
                section, fg_color=self.SURFACE, border_width=1,
                border_color=self.BORDER, corner_radius=12
            )
            card.pack(side="left", fill="both", expand=True, padx=4)
            ctk.CTkLabel(card, text=icon, width=44, height=44, corner_radius=22,
                         fg_color=self.BLUE_SOFT if color == self.BLUE else self.GREEN_SOFT if color == self.GREEN else self.ALT,
                         text_color=color, font=("Segoe UI", 20)).pack(
                             side="left", padx=11, pady=10
                         )
            info = ctk.CTkFrame(card, fg_color="transparent"); info.pack(
                side="left", fill="both", expand=True, pady=9
            )
            ctk.CTkLabel(info, text=title, font=("Segoe UI", 10, "bold"),
                         text_color=self.MUTED).pack(anchor="w")
            ctk.CTkLabel(info, textvariable=var, font=("Segoe UI", 17, "bold"),
                         text_color=self.TEXT).pack(anchor="w")
            ctk.CTkLabel(info, text=sub, font=("Segoe UI", 10),
                         text_color=self.MUTED).pack(anchor="w")

    def build_progress(self):
        section = ctk.CTkFrame(self.body, fg_color="transparent")
        section.pack(fill="both", expand=True)

        card = ctk.CTkFrame(
            section, fg_color=self.SURFACE, border_width=1,
            border_color=self.BORDER, corner_radius=14
        )
        card.pack(fill="both", expand=True)

        head = ctk.CTkFrame(card, fg_color="transparent", height=40)
        head.pack(fill="x", padx=17, pady=(9, 3)); head.pack_propagate(False)
        ctk.CTkLabel(head, text="⌁", font=("Segoe UI", 22),
                     text_color=self.BLUE).pack(side="left")
        ctk.CTkLabel(head, text="EXTRACTION PROGRESS",
                     font=("Segoe UI", 14, "bold"),
                     text_color=self.TEXT).pack(side="left", padx=7)
        self.analysis_badge = ctk.CTkLabel(head, text="", font=("Segoe UI", 10),
                                           text_color=self.MUTED)
        self.analysis_badge.pack(side="left", padx=12)
        ctk.CTkLabel(head, textvariable=self.progress_percent,
                     font=("Segoe UI", 15, "bold"),
                     text_color=self.BLUE).pack(side="right")

        current = ctk.CTkFrame(card, fg_color=self.ALT, border_width=1,
                               border_color=self.BORDER, corner_radius=9)
        current.pack(fill="x", padx=17, pady=(0, 7))
        ctk.CTkLabel(current, text="CURRENT FILE", font=("Segoe UI", 9, "bold"),
                     text_color=self.MUTED).pack(anchor="w", padx=11, pady=(7, 1))
        ctk.CTkLabel(current, textvariable=self.current_file,
                     font=("Segoe UI", 13, "bold"), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=11)
        self.progress = ctk.CTkProgressBar(current, height=8, corner_radius=5,
                                           fg_color=self.BORDER, progress_color=self.BLUE)
        self.progress.pack(fill="x", padx=11, pady=(7, 9))
        self.progress.set(0)

        metrics = ctk.CTkFrame(card, fg_color="transparent", height=52)
        metrics.pack(fill="x", padx=17, pady=(0, 4)); metrics.pack_propagate(False)
        self.success_metric = self.live_metric(metrics, "SUCCESSFUL", "0", self.GREEN)
        self.skip_metric = self.live_metric(metrics, "SKIPPED", "0", self.ORANGE)
        self.fail_metric = self.live_metric(metrics, "FAILED", "0", self.RED)
        self.speed_metric = self.live_metric(metrics, "SPEED", "—", self.BLUE)
        self.eta_metric = self.live_metric(metrics, "ETA", "—", self.PURPLE)

        self.log_box = ctk.CTkTextbox(
            card, height=90, corner_radius=9, border_width=1,
            border_color=self.BORDER, fg_color=self.ALT,
            text_color=self.TEXT, font=("Consolas", 11)
        )
        self.log_box.pack(fill="both", expand=True, padx=17, pady=(0, 10))
        self.log_box.insert("1.0", "Select a ZIP archive to begin.")
        self.log_box.configure(state="disabled")

    def live_metric(self, parent, title, value, color):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(side="left", fill="x", expand=True, padx=3)
        ctk.CTkLabel(frame, text=title, font=("Segoe UI", 10, "bold"),
                     text_color=self.MUTED).pack(anchor="w")
        label = ctk.CTkLabel(frame, text=value, font=("Segoe UI", 12, "bold"),
                             text_color=color)
        label.pack(anchor="w")
        return label

    def build_footer(self):
        left = ctk.CTkFrame(self.footer, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(left, textvariable=self.status, font=("Segoe UI", 11),
                     text_color=self.GREEN, anchor="w").pack(anchor="w")
        ctk.CTkLabel(left, text="🛡 Secure • Efficient • Reliable",
                     font=("Segoe UI", 10), text_color=self.MUTED).pack(anchor="w")
        ctk.CTkLabel(left, text="Reclaim 1.0.0 • Built by Yash Raj Sondhi",
                     font=("Segoe UI", 10, "bold"), text_color=self.MUTED).pack(anchor="w")

        self.cancel_button = ctk.CTkButton(
            self.footer, text="Cancel", width=100, height=44,
            fg_color=self.RED_SOFT, hover_color=self.RED, text_color=self.RED,
            font=("Segoe UI", 11, "bold"), state="disabled",
            command=self.cancel_extraction
        )
        self.cancel_button.pack(side="right", padx=(8, 0))

        self.open_button = ctk.CTkButton(
            self.footer, text="▢  Open Output", width=165, height=44,
            fg_color=self.SURFACE, hover_color=self.ALT,
            border_width=1, border_color=self.BORDER, text_color=self.TEXT,
            font=("Segoe UI", 11, "bold"), state="disabled",
            command=self.open_output
        )
        self.open_button.pack(side="right", padx=(8, 0))

        self.analyze_button = ctk.CTkButton(
            self.footer, text="⌁  Analyze", width=130, height=44,
            fg_color=self.SURFACE, hover_color=self.BLUE_SOFT,
            border_width=1, border_color=self.BORDER, text_color=self.TEXT,
            font=("Segoe UI", 11, "bold"), state="disabled",
            command=self.analyze_selected
        )
        self.analyze_button.pack(side="right", padx=(8, 0))

        self.start_button = ctk.CTkButton(
            self.footer, text="▶  Start Extraction", width=190, height=44,
            fg_color=self.BLUE, hover_color=self.BLUE_HOVER, text_color="#ffffff",
            font=("Segoe UI", 12, "bold"), command=self.start_extraction
        )
        self.start_button.pack(side="right")

    # ==========================================================
    # FILE / OUTPUT
    # ==========================================================

    def setup_dnd(self):
        if not DND_AVAILABLE:
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self.handle_drop)
            self.drop_zone.drop_target_register(DND_FILES)
            self.drop_zone.dnd_bind("<<Drop>>", self.handle_drop)
        except Exception:
            pass

    def handle_drop(self, event):
        if self.is_running:
            return
        try:
            paths = self.root.tk.splitlist(event.data)
            if not paths:
                return
            path = Path(paths[0])
            if path.is_file() and path.suffix.lower() == ".zip":
                self.set_archive(path)
            else:
                messagebox.showwarning("Reclaim", "Please drop a ZIP archive.")
        except Exception as error:
            messagebox.showerror("Reclaim", str(error))

    def select_zip(self):
        if self.is_running:
            return
        path = filedialog.askopenfilename(
            title="Select ZIP archive",
            filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")]
        )
        if path:
            self.set_archive(Path(path))

    def set_archive(self, path):
        self.zip_path = Path(path)
        self.archive_name.set(self.zip_path.name)
        self.analyze_button.configure(state="normal")
        self.load_archive_info()
        self.log(f"Selected: {self.zip_path}")

    def select_output(self):
        if self.is_running:
            return
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_dir = Path(path)
            self.output_path.set(str(self.output_dir))
            self.refresh_storage()

    def load_archive_info(self):
        try:
            logical = self.zip_path.stat().st_size
            allocated = get_allocated_size(self.zip_path)

            with __import__("zipfile").ZipFile(self.zip_path, "r") as archive:
                count = sum(1 for info in archive.infolist() if not info.is_dir())

            self.archive_size.set(self.fmt_bytes(logical))
            self.disk_space.set(self.fmt_bytes(allocated))
            self.file_count.set(str(count))
            self.reclaimed.set("0 B")
            self.current_file.set("Ready to analyze")
            self.status.set("Archive ready.")
            self.progress.set(0)
            self.progress_percent.set("0%")
        except Exception as error:
            messagebox.showerror("Reclaim", f"Could not read ZIP:\n\n{error}")

    def refresh_storage(self):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            total, used, free = shutil.disk_usage(str(self.output_dir))
            self.storage_label.configure(
                text=f"▱ {self.fmt_bytes(free)} free of {self.fmt_bytes(total)}"
            )
            self.storage_bar.set(used / total if total else 0)
        except Exception:
            self.storage_label.configure(text="Storage information unavailable")
            self.storage_bar.set(0)

    # ==========================================================
    # ANALYZE UI
    # ==========================================================

    def analyze_selected(self):
        if not self.zip_path or self.is_running:
            return

        self.analyze_button.configure(state="disabled", text="Analyzing...")
        self.status.set("Analyzing archive...")
        self.current_file.set("Reading archive metadata...")

        def worker():
            try:
                summary = analyze_archive(self.zip_path)
                collisions = find_collisions(
                    self.zip_path,
                    self.output_dir
                )
                summary["collisions"] = collisions
                self.root.after(0, self.show_analysis, summary)
            except Exception as error:
                self.root.after(0, self.analysis_error, error)

        threading.Thread(target=worker, daemon=True).start()

    def analysis_error(self, error):
        self.analyze_button.configure(state="normal", text="⌁  Analyze")
        self.status.set("Analysis failed.")
        messagebox.showerror("Reclaim", str(error))

    def show_analysis(self, data):
        self.analyze_button.configure(state="normal", text="⌁  Analyze")
        self.status.set("Archive analysis complete.")
        self.current_file.set("Archive analyzed")
        self.analysis_badge.configure(
            text=f"{data['file_count']} files • {len(data['collisions'])} collisions"
        )

        win = ctk.CTkToplevel(self.root)
        win.title("Reclaim — Archive Analysis")
        win.geometry("720x620")
        win.minsize(650, 560)
        win.transient(self.root)

        frame = ctk.CTkFrame(win, fg_color=self.BG, corner_radius=0)
        frame.pack(fill="both", expand=True)

        ctk.CTkLabel(
            frame, text="Archive Analysis",
            font=("Segoe UI", 25, "bold"), text_color=self.TEXT
        ).pack(anchor="w", padx=24, pady=(22, 2))

        ctk.CTkLabel(
            frame, text=self.zip_path.name,
            font=("Segoe UI", 12), text_color=self.MUTED
        ).pack(anchor="w", padx=24)

        stats = ctk.CTkFrame(frame, fg_color="transparent")
        stats.pack(fill="x", padx=20, pady=16)

        pairs = [
            ("FILES", str(data["file_count"])),
            ("ARCHIVE", self.fmt_bytes(data["logical_size"])),
            ("COMPRESSED", self.fmt_bytes(data["compressed_bytes"])),
            ("UNCOMPRESSED", self.fmt_bytes(data["uncompressed_bytes"])),
            ("RATIO", f"{data['compression_ratio']:.2f}×"),
            ("EST. RECLAIM", self.fmt_bytes(data["estimated_reclaimable_bytes"])),
        ]

        for title, value in pairs:
            box = ctk.CTkFrame(
                stats, fg_color=self.SURFACE,
                border_width=1, border_color=self.BORDER, corner_radius=9
            )
            box.pack(side="left", fill="both", expand=True, padx=3)
            ctk.CTkLabel(box, text=title, font=("Segoe UI", 8, "bold"),
                         text_color=self.MUTED).pack(anchor="w", padx=9, pady=(8, 1))
            ctk.CTkLabel(box, text=value, font=("Segoe UI", 12, "bold"),
                         text_color=self.TEXT).pack(anchor="w", padx=9, pady=(0, 8))

        ctk.CTkLabel(
            frame, text="FILES IN ARCHIVE",
            font=("Segoe UI", 10, "bold"), text_color=self.MUTED
        ).pack(anchor="w", padx=24)

        listing = ctk.CTkScrollableFrame(
            frame, fg_color=self.SURFACE,
            border_width=1, border_color=self.BORDER, corner_radius=10
        )
        listing.pack(fill="both", expand=True, padx=20, pady=(6, 10))

        for item in data["files"]:
            row = ctk.CTkFrame(listing, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text="✓", font=("Segoe UI", 11, "bold"),
                         text_color=self.GREEN).pack(side="left", padx=8)
            ctk.CTkLabel(row, text=item["filename"], font=("Segoe UI", 10),
                         text_color=self.TEXT, anchor="w").pack(
                             side="left", fill="x", expand=True
                         )
            ctk.CTkLabel(
                row,
                text=self.fmt_bytes(item["compressed_size"]),
                font=("Segoe UI", 10, "bold"),
                text_color=self.MUTED
            ).pack(side="right", padx=8)

        collision_text = (
            "✓ No existing-file collisions detected."
            if not data["collisions"]
            else f"⚠ {len(data['collisions'])} existing file(s) detected."
        )
        ctk.CTkLabel(
            frame, text=collision_text,
            font=("Segoe UI", 11, "bold"),
            text_color=self.GREEN if not data["collisions"] else self.ORANGE,
            anchor="w"
        ).pack(fill="x", padx=24, pady=(0, 9))

        ctk.CTkButton(
            frame, text="Close", width=110, height=40,
            font=("Segoe UI", 11, "bold"), command=win.destroy
        ).pack(anchor="e", padx=24, pady=(0, 18))

    # ==========================================================
    # COLLISIONS
    # ==========================================================

    def get_collision_details(self, collisions):
        """
        Add resume-awareness to collision entries.

        A collision can be:
            - a verified Reclaim output from a previous run
            - an existing file that Reclaim did not verify
        """
        if not self.zip_path:
            return collisions

        try:
            state = load_state(
                self.zip_path,
                self.output_dir
            )
        except Exception:
            state = None

        details = []

        for item in collisions:
            detail = dict(item)
            detail["verified_by_reclaim"] = False

            if state is not None:
                try:
                    filename = item["filename"]
                    output_path = Path(item["output_path"])

                    record = get_completed_record(
                        state,
                        filename
                    )

                    if record is not None:
                        detail["verified_by_reclaim"] = (
                            completed_file_is_valid(
                                state,
                                filename,
                                output_path,
                                item["file_size"],
                                item["crc"],
                                verify_crc=True,
                            )
                        )
                except Exception:
                    detail["verified_by_reclaim"] = False

            details.append(detail)

        return details

    def ask_collision_policy(self, collisions):
        if not collisions:
            return COLLISION_SKIP

        collisions = self.get_collision_details(
            collisions
        )

        verified_count = sum(
            1
            for item in collisions
            if item.get("verified_by_reclaim")
        )

        unverified_count = (
            len(collisions) - verified_count
        )

        win = ctk.CTkToplevel(self.root)
        win.title("Reclaim — Files Already Present")
        win.geometry("720x680")
        win.minsize(720, 680)
        win.resizable(False, False)
        win.transient(self.root)

        result = {"value": COLLISION_CANCEL}

        frame = ctk.CTkFrame(
            win,
            fg_color=self.BG,
            corner_radius=0
        )
        frame.pack(
            fill="both",
            expand=True
        )

        # ------------------------------------------------------
        # Header
        # ------------------------------------------------------

        header = ctk.CTkFrame(
            frame,
            fg_color="transparent"
        )
        header.pack(
            fill="x",
            padx=24,
            pady=(22, 4)
        )

        ctk.CTkLabel(
            header,
            text="Files Already Present",
            font=("Segoe UI", 24, "bold"),
            text_color=self.TEXT
        ).pack(
            anchor="w"
        )

        ctk.CTkLabel(
            header,
            text=(
                f"{len(collisions)} file(s) from this archive "
                "already exist in the output folder."
            ),
            font=("Segoe UI", 12),
            text_color=self.MUTED
        ).pack(
            anchor="w",
            pady=(3, 0)
        )

        # ------------------------------------------------------
        # Status summary
        # ------------------------------------------------------

        summary_card = ctk.CTkFrame(
            frame,
            fg_color=self.SURFACE,
            border_width=1,
            border_color=self.BORDER,
            corner_radius=10
        )
        summary_card.pack(
            fill="x",
            padx=20,
            pady=(10, 8)
        )

        if verified_count and not unverified_count:
            summary_text = (
                f"✓ {verified_count} file(s) were previously "
                "extracted and verified by Reclaim.\n"
                "Running extraction again will not create duplicate "
                "files when Skip existing is selected."
            )
            summary_color = self.GREEN

        elif verified_count and unverified_count:
            summary_text = (
                f"✓ {verified_count} file(s) were previously "
                "extracted and verified by Reclaim.\n"
                f"⚠ {unverified_count} existing file(s) were not "
                "verified by Reclaim."
            )
            summary_color = self.ORANGE

        else:
            summary_text = (
                f"⚠ {unverified_count} existing file(s) were not "
                "verified by Reclaim."
            )
            summary_color = self.ORANGE

        ctk.CTkLabel(
            summary_card,
            text=summary_text,
            font=("Segoe UI", 11, "bold"),
            text_color=summary_color,
            justify="left",
            anchor="w"
        ).pack(
            fill="x",
            padx=14,
            pady=11
        )

        # ------------------------------------------------------
        # Fixed action bar
        # ------------------------------------------------------

        action_bar = ctk.CTkFrame(
            frame,
            fg_color="transparent",
            height=62
        )
        action_bar.pack(
            side="bottom",
            fill="x",
            padx=24,
            pady=(8, 18)
        )
        action_bar.pack_propagate(False)

        choice = tk.StringVar(
            value=COLLISION_SKIP
        )

        def confirm():
            result["value"] = choice.get()
            win.destroy()

        def abort():
            result["value"] = COLLISION_CANCEL
            win.destroy()

        continue_button = ctk.CTkButton(
            action_bar,
            text="Continue",
            width=135,
            height=44,
            corner_radius=9,
            fg_color=self.BLUE,
            hover_color=self.BLUE_HOVER,
            text_color="#ffffff",
            font=("Segoe UI", 11, "bold"),
            command=confirm
        )
        continue_button.pack(
            side="right",
            padx=(0, 10)
        )

        cancel_button = ctk.CTkButton(
            action_bar,
            text="Cancel",
            width=120,
            height=44,
            corner_radius=9,
            fg_color=self.RED_SOFT,
            hover_color=self.RED,
            text_color=self.RED,
            font=("Segoe UI", 11, "bold"),
            command=abort
        )
        cancel_button.pack(
            side="right"
        )

        # ------------------------------------------------------
        # Policy options
        # ------------------------------------------------------

        options_frame = ctk.CTkFrame(
            frame,
            fg_color=self.SURFACE,
            border_width=1,
            border_color=self.BORDER,
            corner_radius=10
        )
        options_frame.pack(
            side="bottom",
            fill="x",
            padx=20,
            pady=(8, 10)
        )

        options = [
            (
                COLLISION_SKIP,
                "Skip existing files",
                "Recommended — leave existing files untouched."
            ),
            (
                COLLISION_RENAME,
                "Rename automatically",
                "Create file (1), file (2), etc."
            ),
            (
                COLLISION_REPLACE,
                "Replace existing files",
                "Overwrite existing regular files."
            ),
            (
                COLLISION_CANCEL,
                "Cancel extraction",
                "Stop before extraction starts."
            ),
        ]

        for value, title, desc in options:
            row = ctk.CTkFrame(
                options_frame,
                fg_color="transparent"
            )
            row.pack(
                fill="x",
                padx=12,
                pady=3
            )

            ctk.CTkRadioButton(
                row,
                text=title,
                variable=choice,
                value=value,
                font=("Segoe UI", 11, "bold"),
                text_color=self.TEXT
            ).pack(
                side="left"
            )

            ctk.CTkLabel(
                row,
                text=desc,
                font=("Segoe UI", 10),
                text_color=self.MUTED,
                anchor="w"
            ).pack(
                side="left",
                padx=(12, 0)
            )

        # ------------------------------------------------------
        # File list
        # ------------------------------------------------------

        listing = ctk.CTkScrollableFrame(
            frame,
            fg_color=self.SURFACE,
            border_width=1,
            border_color=self.BORDER,
            corner_radius=10
        )
        listing.pack(
            fill="both",
            expand=True,
            padx=20,
            pady=(10, 0)
        )

        for item in collisions:
            row = ctk.CTkFrame(
                listing,
                fg_color=self.SURFACE_ALT,
                corner_radius=8
            )
            row.pack(
                fill="x",
                padx=3,
                pady=4
            )

            verified = item.get(
                "verified_by_reclaim",
                False
            )

            ctk.CTkLabel(
                row,
                text="✓" if verified else "⚠",
                font=("Segoe UI", 12, "bold"),
                text_color=(
                    self.GREEN
                    if verified
                    else self.ORANGE
                ),
                width=26
            ).pack(
                side="left",
                padx=(8, 4),
                pady=7
            )

            text_frame = ctk.CTkFrame(
                row,
                fg_color="transparent"
            )
            text_frame.pack(
                side="left",
                fill="x",
                expand=True,
                pady=6
            )

            ctk.CTkLabel(
                text_frame,
                text=item["filename"],
                font=("Segoe UI", 11, "bold"),
                text_color=self.TEXT,
                anchor="w"
            ).pack(
                fill="x"
            )

            ctk.CTkLabel(
                text_frame,
                text=(
                    "Previously verified by Reclaim"
                    if verified
                    else "Existing file — not verified by Reclaim"
                ),
                font=("Segoe UI", 9),
                text_color=(
                    self.GREEN
                    if verified
                    else self.MUTED
                ),
                anchor="w"
            ).pack(
                fill="x",
                pady=(1, 0)
            )

        # ------------------------------------------------------
        # Footer safety message
        # ------------------------------------------------------

        safety_text = (
            "Existing files are never changed when Skip is selected."
        )

        if choice.get() == COLLISION_REPLACE:
            safety_text = (
                "⚠ Replace will overwrite existing regular files."
            )

        safety_label = ctk.CTkLabel(
            frame,
            text=safety_text,
            font=("Segoe UI", 9, "bold"),
            text_color=self.MUTED,
            anchor="w"
        )
        safety_label.pack(
            side="bottom",
            fill="x",
            padx=24,
            pady=(3, 0)
        )

        def update_safety_text(*_):
            if choice.get() == COLLISION_REPLACE:
                safety_label.configure(
                    text="⚠ Replace will overwrite existing regular files.",
                    text_color=self.ORANGE
                )
                continue_button.configure(
                    text="Replace Files",
                    fg_color=self.ORANGE,
                    hover_color=self.ORANGE
                )
            elif choice.get() == COLLISION_CANCEL:
                safety_label.configure(
                    text="No extraction will be started.",
                    text_color=self.MUTED
                )
                continue_button.configure(
                    text="Cancel Extraction",
                    fg_color=self.RED,
                    hover_color=self.RED
                )
            else:
                safety_label.configure(
                    text=(
                        "Existing files are never changed when "
                        "Skip is selected."
                    ),
                    text_color=self.MUTED
                )
                continue_button.configure(
                    text="Continue",
                    fg_color=self.BLUE,
                    hover_color=self.BLUE_HOVER
                )

        choice.trace_add(
            "write",
            update_safety_text
        )

        # ------------------------------------------------------
        # Keyboard support / modal dialog
        # ------------------------------------------------------

        win.bind(
            "<Return>",
            lambda event: confirm()
        )

        win.bind(
            "<Escape>",
            lambda event: abort()
        )

        win.protocol(
            "WM_DELETE_WINDOW",
            abort
        )

        continue_button.focus_set()
        update_safety_text()

        win.grab_set()
        win.wait_window()

        return result["value"]

    # ==========================================================
    # EXTRACTION
    # ==========================================================

    def start_extraction(self):
        if not self.zip_path or self.is_running:
            if not self.zip_path:
                messagebox.showwarning("Reclaim", "Please select a ZIP archive first.")
            return

        try:
            collisions = find_collisions(
                self.zip_path,
                self.output_dir
            )
        except Exception as error:
            messagebox.showerror("Reclaim", str(error))
            return

        policy = self.ask_collision_policy(collisions)
        if policy == COLLISION_CANCEL:
            self.status.set("Extraction cancelled before start.")
            return

        self.is_running = True
        self.cancel_event.clear()
        self.successful = self.skipped = self.failed = 0
        self.total_reclaimed = 0
        self.start_time = time.time()
        self.last_progress_time = time.time()
        self.last_progress_bytes = 0

        self.progress.set(0)
        self.progress_percent.set("0%")
        self.current_file.set("Preparing extraction...")
        self.status.set("Starting extraction...")
        self.reclaimed.set("0 B")
        self.success_metric.configure(text="0")
        self.skip_metric.configure(text="0")
        self.fail_metric.configure(text="0")
        self.speed_metric.configure(text="—")
        self.eta_metric.configure(text="—")
        self.analysis_badge.configure(text=f"Collision policy: {policy}")

        self.header_status.configure(text="RUNNING")
        self.header_sub.configure(text="Extraction in progress")
        self.status_dot.configure(text_color=self.BLUE)
        self.start_button.configure(state="disabled", text="Extracting...")
        self.analyze_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")

        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.log(f"Starting Reclaim • collision policy: {policy}")

        threading.Thread(
            target=self.run_extraction,
            args=(policy,),
            daemon=True
        ).start()

    def run_extraction(self, policy):
        try:
            smart_extract(
                self.zip_path,
                self.output_dir,
                progress_callback=self.handle_progress,
                cancel_event=self.cancel_event,
                collision_policy=policy,
            )
        except Exception as error:
            self.root.after(0, self.extraction_error, error)

    def handle_progress(self, event, data):
        self.root.after(0, self.on_progress, event, data)

    def on_progress(self, event, data):
        total = max(data.get("total", 1), 1)
        index = data.get("index", 0)

        if event == "preflight_started":
            self.status.set("Checking archive security...")
            self.current_file.set("Running security preflight...")
            self.header_status.configure(text="CHECKING")
            self.header_sub.configure(text="Validating archive")

        elif event == "preflight_complete":
            self.log("✓ Security preflight successful")
            self.header_status.configure(text="VALIDATED")
            self.header_sub.configure(text="Archive is safe to process")

        elif event == "file_started":
            name = data["filename"]
            self.current_file.set(name)
            self.status.set(f"Extracting {name}")
            self.log(f"→ {index}/{total}  {name}")
            self.update_progress(index - 1, total)

        elif event == "bytes_progress":
            processed = data.get("bytes_processed", 0)
            size = data.get("file_size", 0)

            if size:
                self.update_progress(
                    index - 1 + processed / size,
                    total
                )

            now = time.time()
            if now - self.last_progress_time >= 0.5:
                elapsed = now - self.last_progress_time
                delta = processed - self.last_progress_bytes

                if elapsed > 0 and delta >= 0:
                    speed = delta / elapsed
                    self.speed_metric.configure(
                        text=self.fmt_bytes(speed) + "/s"
                    )
                    if speed > 0:
                        self.eta_metric.configure(
                            text=self.fmt_time(max(size - processed, 0) / speed)
                        )

                self.last_progress_time = now
                self.last_progress_bytes = processed

        elif event == "collision_renamed":
            self.log(
                f"↳ Renamed to {data.get('output_path', '')}"
            )

        elif event == "file_completed":
            self.successful += 1
            self.total_reclaimed += data.get("reclaimed", 0)
            self.reclaimed.set(self.fmt_bytes(self.total_reclaimed))
            self.success_metric.configure(text=str(self.successful))
            self.status.set(f"Completed {data['filename']}")
            self.log(f"✓ Completed {data['filename']}")
            self.update_progress(index, total)

        elif event == "file_skipped":
            self.skipped += 1
            self.skip_metric.configure(text=str(self.skipped))
            self.current_file.set(data["filename"])
            if data.get("resume_verified"):
                self.log(f"↪ Resume verified: {data['filename']}")
            elif data.get("collision"):
                self.log(f"↪ Collision skipped: {data['filename']}")
            else:
                self.log(f"↪ Skipped: {data['filename']}")
            self.update_progress(index, total)

        elif event == "resume_invalid":
            self.log(f"⚠ Resume invalid: {data['filename']}")

        elif event == "file_failed":
            self.failed += 1
            self.fail_metric.configure(text=str(self.failed))
            self.current_file.set(data["filename"])
            self.status.set(f"Failed {data['filename']}")
            self.log(f"✕ Failed {data['filename']}: {data.get('error', '')}")

        elif event == "state_save_failed":
            self.log("✕ Resume state could not be saved.")

        elif event in ("cancelled", "cancelled_complete"):
            self.header_status.configure(text="CANCELLED")
            self.header_sub.configure(text="Safe to resume later")
            self.status_dot.configure(text_color=self.ORANGE)
            self.status.set("Extraction cancelled.")
            self.finish(True)

        elif event == "interrupted_complete":
            self.header_status.configure(text="PAUSED")
            self.header_sub.configure(text="Safe to resume later")
            self.status_dot.configure(text_color=self.ORANGE)
            self.status.set("Extraction paused safely.")
            self.finish(True)

        elif event == "complete":
            self.successful = data["successful"]
            self.skipped = data["skipped"]
            self.failed = data["failed"]
            self.total_reclaimed = data["total_reclaimed"]
            self.reclaimed.set(self.fmt_bytes(self.total_reclaimed))
            self.success_metric.configure(text=str(self.successful))
            self.skip_metric.configure(text=str(self.skipped))
            self.fail_metric.configure(text=str(self.failed))
            self.progress.set(1)
            self.progress_percent.set("100%")
            self.current_file.set("Extraction complete")
            self.status.set("Extraction completed successfully.")
            self.header_status.configure(text="COMPLETE")
            self.header_sub.configure(text="Extraction finished")
            self.status_dot.configure(text_color=self.GREEN)
            self.log("")
            self.log("✓ EXTRACTION COMPLETE")
            self.log(f"Successful: {self.successful}")
            self.log(f"Skipped: {self.skipped}")
            self.log(f"Failed: {self.failed}")
            self.log(
                f"Space reclaimed: {self.fmt_bytes(self.total_reclaimed)}"
            )
            self.finish(False)

    def update_progress(self, current, total):
        fraction = max(0, min(1, current / total)) if total else 0
        self.progress.set(fraction)
        self.progress_percent.set(f"{fraction * 100:.0f}%")

    def cancel_extraction(self):
        if not self.is_running:
            return

        if not messagebox.askyesno(
            "Cancel Extraction",
            "Stop the current extraction?\n\nCompleted files remain safe."
        ):
            return

        self.cancel_event.set()
        self.status.set("Stopping extraction...")
        self.header_status.configure(text="STOPPING")
        self.log("Cancellation requested...")

    def finish(self, cancelled=False):
        self.is_running = False
        self.start_button.configure(
            state="normal",
            text="▶  Start Extraction"
        )
        self.analyze_button.configure(
            state="normal" if self.zip_path else "disabled",
            text="⌁  Analyze"
        )
        self.cancel_button.configure(state="disabled")
        self.open_button.configure(state="normal")
        self.refresh_storage()

    def extraction_error(self, error):
        self.is_running = False
        self.start_button.configure(
            state="normal",
            text="▶  Start Extraction"
        )
        self.analyze_button.configure(
            state="normal" if self.zip_path else "disabled",
            text="⌁  Analyze"
        )
        self.cancel_button.configure(state="disabled")
        self.open_button.configure(state="normal")
        self.status.set("Extraction failed.")
        self.header_status.configure(text="ERROR")
        self.header_sub.configure(text="Extraction did not complete")
        self.status_dot.configure(text_color=self.RED)
        self.log(f"✕ ERROR: {error}")
        messagebox.showerror("Reclaim", str(error))

    def log(self, text):
        try:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        except Exception:
            pass

    # ==========================================================
    # THEME / SETTINGS / OPEN
    # ==========================================================

    def set_theme(self, theme):
        if self.is_running:
            return
        ctk.set_appearance_mode("Light" if theme == "light" else "Dark")

        if theme == "light":
            self.light_btn.configure(
                fg_color=self.BLUE_SOFT, text_color=self.BLUE
            )
            self.dark_btn.configure(
                fg_color=self.SURFACE, text_color=self.TEXT
            )
        else:
            self.light_btn.configure(
                fg_color=self.SURFACE, text_color=self.TEXT
            )
            self.dark_btn.configure(
                fg_color=self.BLUE_SOFT, text_color=self.BLUE
            )

    def show_settings(self):
        win = ctk.CTkToplevel(self.root)
        win.title("Reclaim Settings")
        win.geometry("430x300")
        win.resizable(False, False)
        win.transient(self.root)

        frame = ctk.CTkFrame(win, fg_color=self.SURFACE, corner_radius=0)
        frame.pack(fill="both", expand=True)

        ctk.CTkLabel(
            frame, text="Settings",
            font=("Segoe UI", 23, "bold"), text_color=self.TEXT
        ).pack(anchor="w", padx=25, pady=(25, 8))

        ctk.CTkLabel(
            frame,
            text=(
                f"Output location:\n{self.output_dir}\n\n"
                "Reclaim 1.0.0\nExtract. Verify. Reclaim."
            ),
            font=("Segoe UI", 11), text_color=self.MUTED, justify="left"
        ).pack(anchor="w", padx=25)

        ctk.CTkButton(
            frame, text="Close", width=110, height=40,
            font=("Segoe UI", 11, "bold"), command=win.destroy
        ).pack(anchor="e", padx=25, pady=25)

    def open_output(self):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.output_dir))
        except Exception as error:
            messagebox.showerror("Reclaim", str(error))

    def on_close(self):
        if self.is_running:
            if not messagebox.askyesno(
                "Extraction in progress",
                "Reclaim is still extracting.\n\nAre you sure you want to close?"
            ):
                return
        self.root.destroy()


if DND_AVAILABLE:
    root = TkinterDnD.Tk()
else:
    root = tk.Tk()

app = ReclaimGUI(root)
root.mainloop()