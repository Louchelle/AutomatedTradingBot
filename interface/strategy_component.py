import tkinter as tk
from tkinter import messagebox
import typing
import json
import logging
import customtkinter as ctk

from interface.styling import *
from interface.scrollable_frame import ScrollableFrame
from database import WorkspaceData

logger = logging.getLogger()

if typing.TYPE_CHECKING:
    from interface.root_component import Root


class StrategyEditor(ctk.CTkFrame):
    def __init__(self, root: "Root", master: ctk.CTkFrame, binance, bitmex, binance_spot, **kwargs):
        kwargs.pop("bg", None)
        super().__init__(master, fg_color=CARD_BG, corner_radius=12, **kwargs)

        self.root: "Root" = root
        self.binance = binance
        self.bitmex = bitmex
        self.binance_spot = binance_spot
        self.db = WorkspaceData()

        self._exchanges = {}
        if self.binance: self._exchanges["binance_futures"] = self.binance
        if self.binance_spot: self._exchanges["binance_spot"] = self.binance_spot

        self._all_contracts = []
        self._all_timeframes = ["1m", "5m", "15m", "30m", "1h", "4h"]

        for exchange, client in self._exchanges.items():
            if client and hasattr(client, 'contracts'):
                for symbol in client.contracts.keys():
                    clean_symbol = symbol.split("_")[0].upper()
                    formatted = f"{clean_symbol}_{exchange}"
                    if formatted not in self._all_contracts:
                        self._all_contracts.append(formatted)

        # --- COMMANDS HEADER FRAME ---
        self._commands_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._commands_frame.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(8, 4))

        self._title_label = ctk.CTkLabel(self._commands_frame, text="Strategy Control Panel",
                                          font=HEADER_FONT, text_color=FG_PRIMARY)
        self._title_label.pack(side=tk.LEFT, padx=5)

        self._kill_button = ctk.CTkButton(self._commands_frame, text="STOP ALL", font=BOLD_FONT,
                                         fg_color=RED_NEON, hover_color="#d31952", width=90,
                                         corner_radius=20, command=self._kill_all_strategies)
        self._kill_button.pack(side=tk.RIGHT, padx=5)

        self._add_button = ctk.CTkButton(self._commands_frame, text="+ Add Strategy", font=BOLD_FONT,
                                      fg_color=CYAN_ACCENT, text_color="#000000", hover_color="#00c8d4",
                                      width=120, corner_radius=20, command=self._add_strategy_row)
        self._add_button.pack(side=tk.RIGHT, padx=5)

        # --- TABLE CONTAINER ---
        self._table_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=2)

        self.body_widgets = dict()
        self.additional_parameters = dict()
        self._extra_input = dict()

        self._base_params = [
            {"code_name": "strategy_type", "header": "Strategy", "weight": 2},
            {"code_name": "contract", "header": "Contract", "weight": 3},
            {"code_name": "timeframe", "header": "Timeframe", "weight": 1},
            {"code_name": "balance_pct", "header": "Balance %", "weight": 1},
            {"code_name": "take_profit", "header": "TP %", "weight": 1},
            {"code_name": "stop_loss", "header": "SL %", "weight": 1},
            {"code_name": "parameters", "header": "Params", "weight": 1},
            {"code_name": "activation", "header": "Status", "weight": 1},
            {"code_name": "delete", "header": "", "weight": 0},
        ]

        self.extra_params = {
            "Technical": [
                {"code_name": "rsi_length", "name": "RSI Periods", "data_type": int},
                {"code_name": "ema_fast", "name": "MACD Fast Length", "data_type": int},
                {"code_name": "ema_slow", "name": "MACD Slow Length", "data_type": int},
                {"code_name": "ema_signal", "name": "MACD Signal Length", "data_type": int},
            ],
            "Breakout": [
                {"code_name": "min_volume", "name": "Minimum Volume", "data_type": float},
                {"code_name": "window", "name": "Breakout Window (30)", "data_type": int},
            ],
            "Ichimoku": [
                {"code_name": "tenkan", "name": "Tenkan-sen Periods", "data_type": int},
                {"code_name": "kijun", "name": "Kijun-sen Periods", "data_type": int},
                {"code_name": "senkou", "name": "Senkou Span B Periods", "data_type": int},
            ]
        }

        for h in self._base_params:
            self.body_widgets[h['code_name']] = dict()
            if h['code_name'] in ["strategy_type", "contract", "timeframe"]:
                self.body_widgets[h['code_name'] + "_var"] = dict()

        # Scrollable Body Frame
        self._body_frame = ScrollableFrame(self._table_frame, bg=CARD_BG, height=150)
        self._body_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, anchor="nw")

        for idx, h in enumerate(self._base_params):
            if h['weight'] > 0:
                self._body_frame.sub_frame.grid_columnconfigure(idx, weight=h['weight'])
            else:
                self._body_frame.sub_frame.grid_columnconfigure(idx, minsize=35)

        # Header Row
        for idx, h in enumerate(self._base_params):
            header = ctk.CTkLabel(self._body_frame.sub_frame, text=h['header'], font=BOLD_FONT,
                                  text_color=FG_MUTED, anchor="center")
            header.grid(row=0, column=idx, padx=4, pady=(2, 6), sticky="ew")

        self._body_index = 1

    def _add_strategy_row(self, data=None):
        if not self._all_contracts and data is None:
            logger.warning("Try to add row, but contracts not loaded yet.")
            return

        if data is not None:
            new_contract = data.get('contract')
            new_tf = data.get('timeframe')
            for b_idx, contract_var in self.body_widgets['contract_var'].items():
                if contract_var.get() == new_contract and self.body_widgets['timeframe_var'][b_idx].get() == new_tf:
                    return

        while self._body_index in self.body_widgets['activation']:
            self._body_index += 1
        b_index = self._body_index

        for col, h in enumerate(self._base_params):
            code_name = h['code_name']
            saved_val = data.get(code_name) if data else None

            if code_name in ["strategy_type", "contract", "timeframe"]:
                self.body_widgets[code_name + "_var"][b_index] = tk.StringVar()
                options = ["Technical", "Breakout", "Ichimoku"] if code_name == "strategy_type" else (
                    self._all_timeframes if code_name == "timeframe" else (self._all_contracts or ["None"])
                )
                initial_val = saved_val if saved_val else options[0]
                self.body_widgets[code_name + "_var"][b_index].set(initial_val)

                self.body_widgets[code_name][b_index] = ctk.CTkOptionMenu(
                    self._body_frame.sub_frame, variable=self.body_widgets[code_name + "_var"][b_index],
                    values=options, fg_color=INPUT_BG, button_color=CARD_BORDER,
                    text_color=FG_PRIMARY, dropdown_fg_color=CARD_BG, anchor="center"
                )

            elif code_name in ["balance_pct", "take_profit", "stop_loss"]:
                self.body_widgets[code_name][b_index] = ctk.CTkEntry(
                    self._body_frame.sub_frame, fg_color=INPUT_BG,
                    text_color=FG_PRIMARY, border_color=CARD_BORDER, justify="center"
                )
                val = str(saved_val) if saved_val is not None else ("10.0" if code_name == "balance_pct" else "0.1")
                self.body_widgets[code_name][b_index].insert(0, val)

            elif code_name == "parameters":
                self.body_widgets[code_name][b_index] = ctk.CTkButton(
                    self._body_frame.sub_frame, text="Params",
                    fg_color=CARD_BORDER, hover_color="#2a3d75", corner_radius=12,
                    command=lambda b_idx=b_index: self._show_popup(b_idx)
                )

            elif code_name == "activation":
                self.body_widgets[code_name][b_index] = ctk.CTkButton(
                    self._body_frame.sub_frame, text="OFF",
                    fg_color=RED_NEON, hover_color="#d31952", corner_radius=15,
                    command=lambda b_idx=b_index: self._switch_strategy(b_idx)
                )

            elif code_name == "delete":
                self.body_widgets[code_name][b_index] = ctk.CTkButton(
                    self._body_frame.sub_frame, text="X", width=32,
                    fg_color="#334155", hover_color=RED_NEON, corner_radius=12,
                    command=lambda b_idx=b_index: self._delete_row(b_idx)
                )

            self.body_widgets[code_name][b_index].grid(row=b_index, column=col, padx=3, pady=3, sticky="ew")

        if data and 'extra_params' in data:
            if isinstance(data['extra_params'], str):
                self.additional_parameters[b_index] = json.loads(data['extra_params'])
            else:
                self.additional_parameters[b_index] = data['extra_params']

        if data and data.get('is_active') == 1:
            self.root.after(100, lambda: self._switch_strategy(b_index))

        self._body_index += 1
        return b_index

    def _show_popup(self, b_index: int):
        if b_index not in self.body_widgets['strategy_type_var']: return

        x, y = self.root.winfo_x(), self.root.winfo_y()
        self._popup_window = ctk.CTkToplevel(self)
        self._popup_window.wm_title("Strategy Parameters")
        self._popup_window.configure(fg_color=CARD_BG)
        self._popup_window.geometry(f"+{x + 400}+{y + 200}")

        # Keep pop-up pinned on top of main window
        self._popup_window.lift()
        self._popup_window.focus_force()
        self._popup_window.grab_set()

        strat_selected = self.body_widgets['strategy_type_var'][b_index].get()
        self.additional_parameters.setdefault(b_index, {})

        for idx, param in enumerate(self.extra_params[strat_selected]):
            code_name = param['code_name']
            ctk.CTkLabel(self._popup_window, text=param['name'], text_color=FG_PRIMARY, font=GLOBAL_FONT).grid(row=idx, column=0, padx=12, pady=8)
            self._extra_input[code_name] = ctk.CTkEntry(self._popup_window, fg_color=INPUT_BG, text_color=FG_PRIMARY, border_color=CARD_BORDER)

            saved_val = self.additional_parameters[b_index].get(code_name)
            if saved_val is not None:
                self._extra_input[code_name].insert(0, str(saved_val))
            elif strat_selected == "Breakout" and code_name == "window":
                self._extra_input[code_name].insert(0, "30")

            self._extra_input[code_name].grid(row=idx, column=1, padx=12, pady=8)

        ctk.CTkButton(self._popup_window, text="Validate", fg_color=CYAN_ACCENT, text_color="#000000",
                      hover_color="#00c8d4", corner_radius=15,
                      command=lambda: self._validate_parameters(b_index)).grid(row=len(self.extra_params[strat_selected]), column=0, columnspan=2, pady=12)

    def _validate_parameters(self, b_index: int):
        strat_selected = self.body_widgets['strategy_type_var'][b_index].get()
        for param in self.extra_params[strat_selected]:
            code_name = param['code_name']
            try:
                val = param['data_type'](self._extra_input[code_name].get())
                self.additional_parameters[b_index][code_name] = val
            except ValueError:
                continue
        self._popup_window.destroy()

    def _switch_strategy(self, b_index: int):
        current_status = self.body_widgets['activation'][b_index].cget("text")

        try:
            strategy_type = self.body_widgets['strategy_type_var'][b_index].get()
            contract_str = self.body_widgets['contract_var'][b_index].get()
            timeframe = self.body_widgets['timeframe_var'][b_index].get()
            balance_pct = float(self.body_widgets['balance_pct'][b_index].get())
            take_profit = float(self.body_widgets['take_profit'][b_index].get())
            stop_loss = float(self.body_widgets['stop_loss'][b_index].get())
            extra_params = self.additional_parameters.get(b_index, {})
        except (ValueError, KeyError) as e:
            messagebox.showerror("Invalid Input", f"Ensure all fields are filled correctly: {e}")
            return

        if current_status == "OFF":
            extra_params['row_index'] = b_index
            strat_dict = {
                "strategy_type": strategy_type, "contract": contract_str,
                "timeframe": timeframe, "balance_pct": balance_pct,
                "take_profit": take_profit, "stop_loss": stop_loss,
                "extra_params": extra_params, "is_active": 1
            }
            self.body_widgets['activation'][b_index].configure(text="STARTING", fg_color="orange")
            self.root.worker.task_queue.put(("ADD_STRATEGY", strat_dict, b_index))
            self.db.save_strategy_resilient(strat_dict)

        elif current_status in ["LIVE", "ONLINE", "STARTING"]:
            strat_dict = {
                "strategy_type": strategy_type, "contract": contract_str,
                "timeframe": timeframe, "balance_pct": balance_pct,
                "take_profit": take_profit, "stop_loss": stop_loss,
                "extra_params": extra_params, "is_active": 0
            }
            stop_params = {"contract": contract_str, "timeframe": timeframe, "strategy_type": strategy_type, "row_index": b_index}
            self.body_widgets['activation'][b_index].configure(text="OFF", fg_color=RED_NEON)
            self.root.worker.task_queue.put(("STOP_STRATEGY", None, stop_params))
            self.db.save_strategy_resilient(strat_dict)

    def _delete_row(self, b_index: int):
        if b_index not in self.body_widgets['activation']: return
        contract_str = self.body_widgets['contract_var'][b_index].get()
        timeframe = self.body_widgets['timeframe_var'][b_index].get()

        if not messagebox.askyesno("Confirm", f"Delete {contract_str} permanently?"): return

        if self.body_widgets['activation'][b_index].cget("text") != "OFF":
            self._switch_strategy(b_index)

        try: self.db.delete_strategy(contract_str, timeframe)
        except Exception as e: logger.error(f"DB Delete Error: {e}")

        for key in list(self.body_widgets.keys()):
            if b_index in self.body_widgets[key]:
                widget = self.body_widgets[key][b_index]
                if hasattr(widget, "destroy"): widget.destroy()
                del self.body_widgets[key][b_index]

    def load_strategies(self):
        live_positions = getattr(self.binance, 'open_positions', []) if self.binance else []
        live_symbols = [p.get('symbol') if isinstance(p, dict) else p.symbol for p in live_positions] if live_positions else []
        saved_strategies = self.db.get("strategies")

        for row in saved_strategies:
            row_dict = dict(row)
            symbol_only = row_dict['contract'].split('_')[0]
            if row_dict['is_active'] == 1 and symbol_only not in live_symbols:
                row_dict['is_active'] = 0
                self.db.save_strategy_resilient(row_dict)
            self._add_strategy_row(row_dict)

    def _kill_all_strategies(self):
        for b_index in list(self.body_widgets['activation'].keys()):
            current_status = self.body_widgets['activation'][b_index].cget("text")
            if current_status in ["LIVE", "ONLINE", "STARTING"]:
                self._switch_strategy(b_index)

    def update_contracts_menu(self):
        if not self._all_contracts: return
        self._all_contracts.sort()
        for b_index, menu_widget in self.body_widgets['contract'].items():
            try:
                menu_widget.configure(values=self._all_contracts)
                current = self.body_widgets['contract_var'][b_index].get()
                if current in ["None", ""]:
                    self.body_widgets['contract_var'][b_index].set(self._all_contracts[0])
            except Exception as e:
                logger.error(f"Failed to update menu at index {b_index}: {e}")