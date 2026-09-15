import tkinter as tk
import customtkinter as ctk
import logging
import threading
import datetime
import time
from typing import Union

from models.models import Trade
from interface.styling import *
from interface.scrollable_frame import ScrollableFrame
from database import WorkspaceData

logger = logging.getLogger()


class TradesWatch(ctk.CTkFrame):
    def __init__(self, root, parent, *args, **kwargs):
        kwargs.pop("bg", None)
        super().__init__(parent, fg_color=CARD_BG, corner_radius=12, **kwargs)

        self.db = WorkspaceData()
        self.root = root

        self._headers = ["Time", "Symbol", "Exchange", "Strategy", "Side", "Quantity", "Status", "PnL", "Action"]

        # Explicit widths tailored to fit the full panel width (~800px total)
        self._col_widths = [110, 110, 90, 110, 70, 80, 80, 80, 30]

        self.body_widgets = dict()
        for h in self._headers:
            self.body_widgets[h.lower()] = dict()
            if h.lower() in ["status", "pnl", "quantity"]:
                self.body_widgets[h.lower() + "_var"] = dict()

        # --- HEADER CONTAINER ---
        self._commands_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._commands_frame.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(8, 4))

        self._title_label = ctk.CTkLabel(self._commands_frame, text="Open Positions & Trade Watch", font=HEADER_FONT,
                                         text_color=FG_PRIMARY)
        self._title_label.pack(side=tk.LEFT, padx=5)

        # --- TABLE CONTAINER ---
        self._table_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=2)

        # Scrollable Body Frame
        self._body_frame = ScrollableFrame(self._table_frame, bg=CARD_BG, height=160)
        self._body_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, anchor="nw")

        # Configure equal grid column weight distribution
        for idx in range(len(self._headers)):
            self._body_frame.sub_frame.grid_columnconfigure(idx, weight=1)

        # Render Header Row at Row 0 with explicit widths
        for idx, h in enumerate(self._headers):
            title = h if h != "Action" else ""
            header = ctk.CTkLabel(self._body_frame.sub_frame, text=title, font=BOLD_FONT,
                                  text_color=FG_MUTED, width=self._col_widths[idx], anchor="center")
            header.grid(row=0, column=idx, padx=2, pady=(2, 6))

        self._body_index = 1

    def add_trade(self, data_in: Union[Trade, dict]):
        if isinstance(data_in, dict):
            t_index = data_in.get('time')
            symbol_val = data_in.get('symbol')
            strat_val = data_in.get('strategy', '')
            side_val = data_in.get('side', '')
            qty_val = data_in.get('quantity', 0.0)
            status_val = data_in.get('status', '')
            pnl_val = data_in.get('pnl', 0.0)
            exch_val = data_in.get('platform', 'Binance').capitalize()
        else:
            t_index = data_in.time
            symbol_val = data_in.symbol
            strat_val = data_in.strategy
            side_val = data_in.side
            qty_val = data_in.quantity
            status_val = data_in.status
            pnl_val = data_in.pnl
            exch_val = getattr(data_in.contract, 'exchange', 'Binance').capitalize()

        if t_index in self.body_widgets['symbol']:
            return

        for tid, widget in self.body_widgets['symbol'].items():
            if widget.cget("text") == symbol_val:
                if strat_val and strat_val != "None":
                    self.body_widgets['strategy'][tid].configure(text=strat_val)
                self.update_trade_log(data_in)
                return

        b_index = self._body_index
        dt_str = datetime.datetime.fromtimestamp(t_index / 1000).strftime("%b %d %H:%M")

        self.body_widgets['time'][t_index] = ctk.CTkLabel(self._body_frame.sub_frame, text=dt_str,
                                                          font=GLOBAL_FONT, text_color=FG_MUTED,
                                                          width=self._col_widths[0], anchor="center")
        self.body_widgets['time'][t_index].grid(row=b_index, column=0, padx=2, pady=3)

        self.body_widgets['symbol'][t_index] = ctk.CTkLabel(self._body_frame.sub_frame, text=symbol_val,
                                                            font=BOLD_FONT, text_color=CYAN_ACCENT,
                                                            width=self._col_widths[1], anchor="center")
        self.body_widgets['symbol'][t_index].grid(row=b_index, column=1, padx=2, pady=3)

        self.body_widgets['exchange'][t_index] = ctk.CTkLabel(self._body_frame.sub_frame, text=exch_val,
                                                              font=GLOBAL_FONT, text_color=FG_MUTED,
                                                              width=self._col_widths[2], anchor="center")
        self.body_widgets['exchange'][t_index].grid(row=b_index, column=2, padx=2, pady=3)

        self.body_widgets['strategy'][t_index] = ctk.CTkLabel(self._body_frame.sub_frame, text=strat_val,
                                                              font=GLOBAL_FONT, text_color=FG_PRIMARY,
                                                              width=self._col_widths[3], anchor="center")
        self.body_widgets['strategy'][t_index].grid(row=b_index, column=3, padx=2, pady=3)

        side_color = GREEN_NEON if str(side_val).lower() == "long" else RED_NEON
        self.body_widgets['side'][t_index] = ctk.CTkLabel(self._body_frame.sub_frame, text=str(side_val).upper(),
                                                          font=BOLD_FONT, text_color=side_color,
                                                          width=self._col_widths[4], anchor="center")
        self.body_widgets['side'][t_index].grid(row=b_index, column=4, padx=2, pady=3)

        self.body_widgets['quantity_var'][t_index] = tk.StringVar(value=str(qty_val))
        self.body_widgets['quantity'][t_index] = ctk.CTkLabel(self._body_frame.sub_frame,
                                                              textvariable=self.body_widgets['quantity_var'][t_index],
                                                              font=GLOBAL_FONT, text_color=FG_PRIMARY,
                                                              width=self._col_widths[5], anchor="center")
        self.body_widgets['quantity'][t_index].grid(row=b_index, column=5, padx=2, pady=3)

        self.body_widgets['status_var'][t_index] = tk.StringVar(value=str(status_val).capitalize())
        self.body_widgets['status'][t_index] = ctk.CTkLabel(self._body_frame.sub_frame,
                                                            textvariable=self.body_widgets['status_var'][t_index],
                                                            font=GLOBAL_FONT, text_color=FG_PRIMARY,
                                                            width=self._col_widths[6], anchor="center")
        self.body_widgets['status'][t_index].grid(row=b_index, column=6, padx=2, pady=3)

        self.body_widgets['pnl_var'][t_index] = tk.StringVar(value=f"{pnl_val:.2f}")
        pnl_color = GREEN_NEON if pnl_val > 0 else RED_NEON if pnl_val < 0 else FG_PRIMARY
        self.body_widgets['pnl'][t_index] = ctk.CTkLabel(self._body_frame.sub_frame,
                                                         textvariable=self.body_widgets['pnl_var'][t_index],
                                                         font=BOLD_FONT, text_color=pnl_color,
                                                         width=self._col_widths[7], anchor="center")
        self.body_widgets['pnl'][t_index].grid(row=b_index, column=7, padx=2, pady=3)

        self.body_widgets['action'][t_index] = ctk.CTkButton(self._body_frame.sub_frame, text="X",
                                                             width=self._col_widths[8], fg_color="#334155",
                                                             hover_color=RED_NEON, corner_radius=10,
                                                             command=lambda: self._close_trade(data_in))
        self.body_widgets['action'][t_index].grid(row=b_index, column=8, padx=2, pady=3)

        self._body_index += 1
        trade_dict = data_in if isinstance(data_in, dict) else data_in.as_dict()
        self.db.insert_trade(trade_dict)

    def _close_trade(self, trade_data: Union[Trade, dict]):
        t_index = trade_data.get('time') if isinstance(trade_data, dict) else trade_data.time
        if t_index in self.body_widgets['action']:
            self.body_widgets['action'][t_index].configure(state="disabled")
        if t_index in self.body_widgets['status_var']:
            self.body_widgets['status_var'][t_index].set("Closing...")
        threading.Thread(target=self._execute_close, args=(trade_data,), daemon=True).start()

    def _execute_close(self, trade_data: Union[Trade, dict]):
        try:
            t_index = trade_data.get('time') if isinstance(trade_data, dict) else trade_data.time
            if t_index in self.body_widgets['quantity_var']:
                live_qty = self.body_widgets['quantity_var'][t_index].get()
                if isinstance(trade_data, dict):
                    trade_data['quantity'] = live_qty
                else:
                    trade_data.quantity = float(live_qty)

            success = self.root.manual_close(trade_data)
            if success:
                time.sleep(0.5)
                self.after(0, lambda: self.remove_trade(t_index))
            else:
                self.after(0, lambda: self._reset_trade_ui(trade_data))
        except Exception as e:
            logger.error(f"Thread Error: {e}")
            self.after(0, lambda: self._reset_trade_ui(trade_data))

    def _reset_trade_ui(self, trade_data: Union[Trade, dict]):
        t_index = trade_data.get('time') if isinstance(trade_data, dict) else trade_data.time
        status = trade_data.get('status', 'open') if isinstance(trade_data, dict) else trade_data.status
        if t_index in self.body_widgets['action']:
            self.body_widgets['action'][t_index].configure(state="normal")
            self.body_widgets['status_var'][t_index].set(str(status).capitalize())

    def remove_trade(self, t_index: int):
        for key in self.body_widgets.keys():
            if t_index in self.body_widgets[key]:
                widget = self.body_widgets[key][t_index]
                if hasattr(widget, "destroy"):
                    widget.destroy()
                del self.body_widgets[key][t_index]

    def update_trade_log(self, data_in: Union[Trade, dict]):
        if isinstance(data_in, dict):
            t_index = getattr(data_in, 'time', 0)
            symbol_val = data_in.get('symbol')
            pnl_value = data_in.get('pnl')
            status_val = data_in.get('status')
            qty_val = data_in.get('quantity')
            side_val = data_in.get('side', '')
            strat_val = data_in.get('strategy', '')
        else:
            t_index = data_in.time
            symbol_val = data_in.symbol
            pnl_value = data_in.pnl
            status_val = data_in.status
            qty_val = data_in.quantity
            side_val = getattr(data_in, 'side', '')
            strat_val = getattr(data_in, 'strategy', '')

        target_id = None
        if t_index in self.body_widgets['symbol']:
            target_id = t_index
        else:
            for tid, widget in self.body_widgets['symbol'].items():
                if widget.cget("text") == symbol_val:
                    target_id = tid
                    break

        if target_id is not None:
            if qty_val is not None:
                self.body_widgets['quantity_var'][target_id].set(str(qty_val))
            if status_val:
                self.body_widgets['status_var'][target_id].set(str(status_val).capitalize())
                if str(status_val).lower() == "closed":
                    e_id = data_in.get('entry_id') if isinstance(data_in, dict) else getattr(data_in, 'entry_id',
                                                                                             target_id)
                    self.db.update_trade_status(e_id, "closed")
            if pnl_value is not None:
                color = GREEN_NEON if pnl_value > 0 else RED_NEON if pnl_value < 0 else FG_PRIMARY
                self.body_widgets['pnl_var'][target_id].set(f"{pnl_value:.2f}")
                self.body_widgets['pnl'][target_id].configure(text_color=color)
            if strat_val and strat_val != "None":
                self.body_widgets['strategy'][target_id].configure(text=strat_val)
            if side_val and side_val != "None":
                side_color = GREEN_NEON if str(side_val).lower() == "long" else RED_NEON
                self.body_widgets['side'][target_id].configure(text=str(side_val).upper(), text_color=side_color)
        else:
            try:
                if float(qty_val) > 0 and str(status_val).lower() != "closed":
                    self.add_trade(data_in)
            except (ValueError, TypeError):
                pass