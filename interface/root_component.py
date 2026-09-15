import queue
import threading
import time
import tkinter as tk
from tkinter.messagebox import askquestion
import logging
import json
from typing import Any, Union
import customtkinter as ctk

from connectors.bitmex import BitmexClient
from connectors.binance_client import BinanceClient

from interface.styling import *
from interface.logging_component import Logging
from interface.watchlist_component import Watchlist
from interface.trades_component import TradesWatch
from interface.strategy_component import StrategyEditor

from models.models import Trade

logger = logging.getLogger()


class Root(ctk.CTk):
    def __init__(self, binance, bitmex, binance_spot, worker,
                 order_status_queue, ui_update_queue, pnl_update_queue):

        super().__init__()

        # --- CUSTOMTKINTER APPEARANCE & THEME ---
        ctk.set_appearance_mode("Dark")
        self.configure(fg_color=BG_DARK)
        self.title("Algorithmic Trading Dashboard")
        self.geometry("1280x720")

        self.binance = binance
        self.bitmex = bitmex
        self.binance_spot = binance_spot
        self.worker = worker

        self.ui_update_queue = ui_update_queue
        self.order_status_queue = order_status_queue
        self.pnl_update_queue = pnl_update_queue

        if self.binance:
            self.binance.pnl_update_callback = self.pnl_update_queue.put
            self.binance.on_trade_update = self.order_status_queue.put

        if self.bitmex:
            self.bitmex.pnl_update_callback = self.pnl_update_queue.put
            self.bitmex.on_trade_update = self.order_status_queue.put

        if self.binance_spot:
            self.binance_spot.pnl_update_callback = self.pnl_update_queue.put
            self.binance_spot.on_trade_update = self.order_status_queue.put

        self.after(100, self._check_ui_queue)
        self._check_order_status_queue()
        self._check_pnl_queue()

        self.protocol("WM_DELETE_WINDOW", self._ask_before_close)

        # --- MENU ---
        self.main_menu = tk.Menu(self)
        self.configure(menu=self.main_menu)
        self.workspace_menu = tk.Menu(self.main_menu, tearoff=False)
        self.main_menu.add_cascade(label="Workspace", menu=self.workspace_menu)
        self.workspace_menu.add_command(label="Save workspace", command=self._save_workspace)

        # =========================================================================
        # FIXED SIDEBAR GRID
        # =========================================================================
        self.grid_columnconfigure(0, weight=0, minsize=340)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=2)
        self.grid_rowconfigure(2, weight=0)

        # --- TOP CARD: STRATEGY CONTROL PANEL ---
        self.strategy_card = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=12,
                                          border_width=1, border_color=CARD_BORDER)
        self.strategy_card.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=10, pady=(10, 5))

        # --- BOTTOM-LEFT CARD: WATCHLIST ---
        self.watchlist_card = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=12,
                                           border_width=1, border_color=CARD_BORDER)
        self.watchlist_card.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=(5, 5))

        # --- BOTTOM-RIGHT CONTAINER: TRADES WATCH & SYSTEM LOGS ---
        self._right_container = ctk.CTkFrame(self, fg_color="transparent")
        self._right_container.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=(5, 5))
        self._right_container.grid_columnconfigure(0, weight=1)
        self._right_container.grid_rowconfigure(0, weight=2)
        self._right_container.grid_rowconfigure(1, weight=1)

        self.trades_card = ctk.CTkFrame(self._right_container, fg_color=CARD_BG, corner_radius=12,
                                         border_width=1, border_color=CARD_BORDER)
        self.trades_card.grid(row=0, column=0, sticky="nsew", pady=(0, 5))

        self.logging_card = ctk.CTkFrame(self._right_container, fg_color=CARD_BG, corner_radius=12,
                                          border_width=1, border_color=CARD_BORDER)
        self.logging_card.grid(row=1, column=0, sticky="nsew", pady=(5, 0))

        # --- MOUNT COMPONENTS ---
        binance_contracts = self.binance.contracts if self.binance else {}
        bitmex_contracts = self.bitmex.contracts if self.bitmex else {}
        binance_spot_contracts = self.binance_spot.contracts if self.binance_spot else {}

        self._strategy_editor = StrategyEditor(self, self.strategy_card, binance=self.binance,
                                               bitmex=self.bitmex, binance_spot=self.binance_spot,
                                               bg=CARD_BG)
        self._strategy_editor.pack(fill="both", expand=True, padx=5, pady=5)

        self._watchlist_frame = Watchlist(self.watchlist_card, binance_contracts, binance_spot_contracts, bg=CARD_BG)
        self._watchlist_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self._trades_frame = TradesWatch(self, self.trades_card, bg=CARD_BG)
        self._trades_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.logging_frame = Logging(self.logging_card, bg=CARD_BG)
        self.logging_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # --- STATUS BAR ---
        self.status_var = tk.StringVar(value="Status: Synchronizing Market Data...")
        self.status_bar = tk.Label(self, textvariable=self.status_var, bd=1,
                                   relief=tk.SUNKEN, anchor=tk.W,
                                   bg=BG_DARK, fg=FG_MUTED, font=GLOBAL_FONT)
        self.status_bar.grid(row=2, column=0, columnspan=2, sticky="we", padx=10, pady=(2, 5))

        self.after(500, lambda: self.logging_frame.add_log("GUI Initialized. Waiting for Worker..."))
        self.after(1000, self._start_threads)
        self.after(2000, self._check_worker_status)
        self.after(3000, lambda: self._watchlist_frame.update_ui(self.binance))

    def _check_ui_queue(self):
        for _ in range(50):
            try:
                item = self.ui_update_queue.get_nowait()
            except queue.Empty:
                break

            try:
                tag, data = item

                if tag == "CONNECTIONS_READY":
                    logger.info("UI: Connections ready. Triggering strategy & watchlist reconciliation...")
                    self._strategy_editor.load_strategies()

                    saved_watchlist = self._watchlist_frame.db.get("watchlist")
                    if saved_watchlist:
                        self._watchlist_frame.load_watchlist(saved_watchlist)

                    binance_syms = list(self.binance.contracts.keys()) if self.binance else []
                    if self.binance_spot:
                        binance_syms.extend(list(self.binance_spot.contracts.keys()))

                    self._watchlist_frame.update_symbols(binance_syms)

                    self.status_var.set("Status: Online")
                    self.status_bar.config(fg=GREEN_NEON)

                elif "NEW_TRADE" in tag:
                    self._trades_frame.add_trade(data)

                elif tag == "RESTORE_STRATEGY":
                    self._strategy_editor._add_strategy_row(data)

                else:
                    self.process_ui_update(tag, data)

            except Exception as e:
                logger.error(f"Error processing UI update '{tag}': {e}")
            finally:
                self.ui_update_queue.task_done()

        self.after(100, self._check_ui_queue)

    def process_ui_update(self, tag: str, data: Any):
        if tag == "STRATEGY_ON":
            try:
                b_index = int(data)
                if b_index in self._strategy_editor.body_widgets['activation']:
                    btn = self._strategy_editor.body_widgets['activation'][b_index]
                    btn.configure(text="LIVE", fg_color=GREEN_NEON, hover_color="#00be7e", state="normal")
                logger.info(f"GUI: Strategy at row {b_index} is now LIVE.")
            except (ValueError, TypeError, KeyError) as e:
                logger.error(f"Error setting STRATEGY_ON row {data}: {e}")
            return

        elif tag == "STRATEGY_OFF":
            try:
                b_index = int(data)
                if b_index in self._strategy_editor.body_widgets['activation']:
                    btn = self._strategy_editor.body_widgets['activation'][b_index]
                    btn.configure(text="OFF", fg_color=RED_NEON, hover_color="#d31952", state="normal")
                logger.info(f"GUI: Strategy at row {b_index} is now OFFLINE.")
            except (ValueError, TypeError, KeyError) as e:
                logger.error(f"Error setting STRATEGY_OFF row {data}: {e}")
            return

        parts = tag.split(":", 1)
        if parts[0] == "LOG":
            strategy_name = parts[1] if len(parts) > 1 else "SYSTEM"
            self.logging_frame.add_log(f"[{strategy_name}] {data}")
            logger.info(f"GUI_LOG: [{strategy_name}] {data}")

    def _check_worker_status(self):
        if self.binance and len(self.binance.contracts) > 0:
            logger.info("Worker sync confirmed. Updating UI menus...")

            def heavy_setup():
                sorted_contracts = sorted(list(self.binance.contracts.keys()))
                self.after(0, lambda: self._finalize_gui_setup(sorted_contracts))

            threading.Thread(target=heavy_setup, daemon=True).start()
        else:
            self.after(1000, self._check_worker_status)

    def _finalize_gui_setup(self, sorted_contracts):
        self._strategy_editor._all_contracts = sorted_contracts
        self._strategy_editor.update_contracts_menu()

    def _start_threads(self):
        for client in [self.binance, self.binance_spot]:
            if client and hasattr(client, 'start_ws_thread'):
                if not getattr(client, 'ws_connected', False):
                    client.start_ws_thread()

    def manual_close(self, trade_data: Union[Trade, dict]):
        if isinstance(trade_data, dict):
            symbol = trade_data.get('symbol')
            side_val = trade_data.get('side')
            qty_val = trade_data.get('quantity')
            contract = self.binance.contracts.get(symbol) if self.binance else None
        else:
            symbol = trade_data.symbol
            side_val = trade_data.side
            qty_val = trade_data.quantity
            contract = trade_data.contract

        self.logging_frame.add_log(f"Sending close order for {symbol}...")

        try:
            if hasattr(qty_val, 'get'):
                val = qty_val.get()
            else:
                val = qty_val

            qty = abs(float(val if str(val).strip().upper() != "N/A" else 0))
            side = "SELL" if str(side_val).lower() == "long" else "BUY"

            if self.binance and contract:
                return self.binance.place_order(contract, "MARKET", qty, side, is_exit=True)

        except Exception as e:
            logger.error(f"Manual close Error: {e}")
            self.logging_frame.add_log(f"Error: {e}")
            return None

    def _save_workspace(self):
        watchlist_symbols = []
        for key, value in self._watchlist_frame.body_widgets['symbol'].items():
            symbol = value.cget("text")
            exchange = self._watchlist_frame.body_widgets['exchange'][key].cget("text")
            watchlist_symbols.append((symbol, exchange,))
        self._watchlist_frame.db.save("watchlist", watchlist_symbols)

        strategies = []
        strat_widgets = self._strategy_editor.body_widgets
        for b_index in strat_widgets['contract']:
            strategy_type = strat_widgets['strategy_type_var'][b_index].get()
            contract_raw = strat_widgets['contract_var'][b_index].get()
            contract = contract_raw.rsplit("_", 1)[0] + "_" + contract_raw.rsplit("_", 1)[
                1].lower() if "_" in contract_raw else contract_raw

            strategies.append((
                strategy_type, contract, strat_widgets['timeframe_var'][b_index].get(),
                strat_widgets['balance_pct'][b_index].get(), strat_widgets['take_profit'][b_index].get(),
                strat_widgets['stop_loss'][b_index].get(),
                json.dumps(
                    {p['code_name']: self._strategy_editor.additional_parameters.get(b_index, {}).get(p['code_name'])
                     for p in self._strategy_editor.extra_params[strategy_type]}),
                1 if strat_widgets['activation'][b_index].cget("text") != "OFF" else 0
            ))
        self._strategy_editor.db.save("strategies", strategies)
        self.logging_frame.add_log("Workspace saved")

    def _ask_before_close(self):
        if askquestion("Confirmation", "Do you really want to exit?") == "yes":
            if self.worker: self.worker.stop()
            for client in [self.binance, self.binance_spot]:
                if client:
                    client.reconnect = False
                    if hasattr(client, 'ws') and client.ws: client.ws.close()
            self.destroy()

    def _check_order_status_queue(self):
        try:
            for _ in range(15):
                item = self.order_status_queue.get_nowait()
                self._trades_frame.update_trade_log(item)
                self.order_status_queue.task_done()
        except queue.Empty:
            pass
        self.after(200, self._check_order_status_queue)

    def _check_pnl_queue(self):
        try:
            for _ in range(15):
                trade = self.pnl_update_queue.get_nowait()
                if hasattr(trade, 'as_dict'):
                    trade = trade.as_dict()

                self._trades_frame.update_trade_log(trade)
                self.pnl_update_queue.task_done()
        except queue.Empty:
            pass
        except Exception as e:
            logger.error(f"CRITICAL PNL ERROR: {e}")

        self.after(100, self._check_pnl_queue)