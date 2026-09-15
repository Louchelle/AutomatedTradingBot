import tkinter as tk
import customtkinter as ctk
from logger import logger

from connectors.binance_client import BinanceClient
from interface.styling import *
from interface.autocomplete_widget import Autocomplete
from interface.scrollable_frame import ScrollableFrame
from database import WorkspaceData


class Watchlist(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkFrame, binance_contracts: dict, binance_spot_contracts: dict, *args, **kwargs):
        kwargs.pop("bg", None)
        super().__init__(master, fg_color=CARD_BG, corner_radius=12, **kwargs)

        self.db = WorkspaceData()
        self.binance_symbols = list(binance_contracts.keys()) if binance_contracts else []

        # --- COMMANDS / SEARCH HEADER PANEL ---
        self._commands_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._commands_frame.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(6, 2))

        self._title_label = ctk.CTkLabel(self._commands_frame, text="Watchlist", font=HEADER_FONT, text_color=FG_PRIMARY)
        self._title_label.pack(side=tk.LEFT, padx=2)

        # Search Control
        self._search_container = ctk.CTkFrame(self._commands_frame, fg_color="transparent")
        self._search_container.pack(side=tk.RIGHT, padx=2)

        ctk.CTkLabel(self._search_container, text="Add Symbol:", font=GLOBAL_FONT, text_color=FG_MUTED).grid(row=0, column=0, padx=(0, 4))
        self._binance_entry = Autocomplete(self.binance_symbols, self._search_container, fg=FG_PRIMARY, justify=tk.CENTER,
                                           insertbackground=FG_PRIMARY, bg=INPUT_BG, highlightthickness=1, highlightbackground=CARD_BORDER,
                                           bd=0, font=GLOBAL_FONT, width=10)
        self._binance_entry.bind("<Return>", self._add_binance_symbol)
        self._binance_entry.grid(row=0, column=1)

        # --- TABLE CONTAINER ---
        self._table_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=(2, 4))

        self.body_widgets = dict()
        self._headers = ["symbol", "exchange", "bid", "ask", "remove"]
        self._col_widths = [80, 65, 60, 60, 28]

        for h in self._headers:
            self.body_widgets[h] = dict()
            if h in ["bid", "ask"]:
                self.body_widgets[h + "_var"] = dict()

        # Scrollable Body Frame
        self._body_frame = ScrollableFrame(self._table_frame, bg=CARD_BG, height=220)
        self._body_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, anchor="nw")

        # Header Row
        for idx, h in enumerate(self._headers):
            title = h.capitalize() if h != "remove" else ""
            header = ctk.CTkLabel(self._body_frame.sub_frame, text=title, font=BOLD_FONT,
                                  text_color=FG_MUTED, width=self._col_widths[idx], anchor="center")
            header.grid(row=0, column=idx, padx=1, pady=(2, 4))

        self._body_index = 1

    def load_watchlist(self, saved_symbols):
        if saved_symbols:
            for s in saved_symbols:
                self._add_symbol(s['symbol'], s.get('exchange', 'Binance'))

    def _remove_symbol(self, b_index: int):
        for h in self._headers:
            if b_index in self.body_widgets[h]:
                widget = self.body_widgets[h][b_index]
                if hasattr(widget, "destroy"):
                    widget.destroy()
                del self.body_widgets[h][b_index]

    def _add_binance_symbol(self, event):
        symbol = event.widget.get().strip().upper()
        if symbol in self.binance_symbols:
            self._add_symbol(symbol, "Binance")
            event.widget.delete(0, tk.END)
            if hasattr(event.widget, '_close_suggestions'):
                event.widget._close_suggestions()

    def _add_symbol(self, symbol: str, exchange: str = "Binance"):
        b_index = self._body_index

        self.body_widgets['symbol'][b_index] = ctk.CTkLabel(self._body_frame.sub_frame, text=symbol,
                                                           font=GLOBAL_FONT, text_color=CYAN_ACCENT,
                                                           width=self._col_widths[0], anchor="center")
        self.body_widgets['symbol'][b_index].grid(row=b_index, column=0, padx=1, pady=2)

        self.body_widgets['exchange'][b_index] = ctk.CTkLabel(self._body_frame.sub_frame, text=exchange,
                                                             font=GLOBAL_FONT, text_color=FG_MUTED,
                                                             width=self._col_widths[1], anchor="center")
        self.body_widgets['exchange'][b_index].grid(row=b_index, column=1, padx=1, pady=2)

        self.body_widgets['bid_var'][b_index] = tk.StringVar(value="-")
        self.body_widgets['bid'][b_index] = ctk.CTkLabel(self._body_frame.sub_frame, textvariable=self.body_widgets['bid_var'][b_index],
                                                         font=GLOBAL_FONT, text_color=FG_PRIMARY,
                                                         width=self._col_widths[2], anchor="center")
        self.body_widgets['bid'][b_index].grid(row=b_index, column=2, padx=1, pady=2)

        self.body_widgets['ask_var'][b_index] = tk.StringVar(value="-")
        self.body_widgets['ask'][b_index] = ctk.CTkLabel(self._body_frame.sub_frame, textvariable=self.body_widgets['ask_var'][b_index],
                                                         font=GLOBAL_FONT, text_color=FG_PRIMARY,
                                                         width=self._col_widths[3], anchor="center")
        self.body_widgets['ask'][b_index].grid(row=b_index, column=3, padx=1, pady=2)

        self.body_widgets['remove'][b_index] = ctk.CTkButton(self._body_frame.sub_frame, text="X",
                                                            width=self._col_widths[4], fg_color="#334155",
                                                            hover_color=RED_NEON, corner_radius=10,
                                                            command=lambda: self._remove_symbol(b_index))
        self.body_widgets['remove'][b_index].grid(row=b_index, column=4, padx=1, pady=2)

        client = self.master.master.binance if hasattr(self.master.master, 'binance') else None
        if client:
            contract_obj = client.contracts.get(symbol)
            if contract_obj:
                client.subscribe_channel([contract_obj], "bookticker")

        self._body_index += 1

    def update_ui(self, binance: 'BinanceClient', *args):
        try:
            for b_index in list(self.body_widgets['symbol'].keys()):
                symbol = self.body_widgets['symbol'][b_index].cget("text")
                prices = binance.prices.get(symbol) if binance else None

                if prices:
                    if 'bid' in prices and prices['bid'] is not None:
                        self.body_widgets['bid_var'][b_index].set(f"{prices['bid']:.2f}")
                    if 'ask' in prices and prices['ask'] is not None:
                        self.body_widgets['ask_var'][b_index].set(f"{prices['ask']:.2f}")
        except Exception as e:
            logger.error(f"Watchlist Update Loop Error: {e}")

        if getattr(self, '_update_job', None):
            self.after_cancel(self._update_job)
        self._update_job = self.after(200, lambda: self.update_ui(binance))

    def update_symbols(self, binance_symbols: list, *args):
        self.binance_symbols = binance_symbols
        self._binance_entry.set_suggestions(self.binance_symbols)