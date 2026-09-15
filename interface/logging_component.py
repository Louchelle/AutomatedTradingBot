import threading
import tkinter as tk
import customtkinter as ctk
import logging
from interface.styling import *


class Logging(ctk.CTkFrame, logging.Handler):
    def __init__(self, parent, *args, **kwargs):
        kwargs.pop("bg", None)
        ctk.CTkFrame.__init__(self, parent, fg_color=CARD_BG, corner_radius=12, **kwargs)
        logging.Handler.__init__(self)

        # Title Header
        self._title_label = ctk.CTkLabel(self, text="System Terminal & Event Logs", font=HEADER_FONT, text_color=FG_PRIMARY)
        self._title_label.pack(anchor="w", padx=12, pady=(8, 4))

        # Terminal Text Box
        self.logging_text = ctk.CTkTextbox(
            self, fg_color=INPUT_BG, text_color=GREEN_NEON,
            font=("Consolas", 10), corner_radius=8,
            border_width=1, border_color=CARD_BORDER,
            activate_scrollbars=True
        )
        self.logging_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.logging_text.configure(state="disabled")

        self.log_queue = []
        self.log_lock = threading.Lock()

        self.setLevel(logging.INFO)
        logging.getLogger().addHandler(self)

        self.after(100, self._process_log_batches)

    def emit(self, record):
        message = self.format(record)

        with self.log_lock:
            if message in self.log_queue:
                return

        noise = ["pnl", "price", "ticker", "ping", "pong", "update"]
        if any(word in message.lower() for word in noise):
            if "heartbeat" not in message.lower() and "hb:" not in message.lower():
                return

        with self.log_lock:
            self.log_queue.append(message)

    def add_log(self, message: str):
        if not self.logging_text.winfo_exists():
            return

        try:
            self.logging_text.configure(state='normal')
            self.logging_text.insert("end", message + '\n')

            line_count = int(self.logging_text.index('end-1c').split('.')[0])
            if line_count > 500:
                self.logging_text.delete("1.0", "2.0")

            self.logging_text.see("end")
            self.logging_text.configure(state='disabled')
        except Exception:
            pass

    def _process_log_batches(self):
        if not self.logging_text.winfo_exists():
            return

        with self.log_lock:
            if not self.log_queue:
                self.after(100, self._process_log_batches)
                return

            to_print = "\n".join(self.log_queue) + "\n"
            self.log_queue.clear()

        try:
            self.logging_text.configure(state='normal')
            self.logging_text.insert("end", to_print)

            line_count = int(self.logging_text.index('end-1c').split('.')[0])
            if line_count > 500:
                self.logging_text.delete("1.0", f"{line_count - 500}.0")

            self.logging_text.see("end")
            self.logging_text.configure(state='disabled')
        except Exception:
            pass

        self.after(100, self._process_log_batches)