import tkinter as tk
import customtkinter as ctk
from interface.styling import *


class ScrollableFrame(ctk.CTkFrame):
    def __init__(self, container, height=200, bg=CARD_BG, *args, **kwargs):
        kwargs.pop("fg_color", None)
        super().__init__(container, fg_color=bg, corner_radius=8, **kwargs)

        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0)

        # Custom Dark Scrollbar
        self.scrollbar = ctk.CTkScrollbar(
            self, orientation="vertical", command=self.canvas.yview,
            fg_color=bg, button_color=CARD_BORDER, button_hover_color="#2a3d75",
            width=10
        )

        self.sub_frame = ctk.CTkFrame(self.canvas, fg_color=bg)

        self.sub_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window((0, 0), window=self.sub_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        # Force sub_frame to match canvas width dynamically on resize
        self.canvas.bind('<Configure>', self._on_canvas_configure)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.sub_frame.bind("<MouseWheel>", self._on_mousewheel)

    def _on_canvas_configure(self, event):
        # Stretch sub_frame to fill exact canvas width
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        if self.winfo_exists():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")