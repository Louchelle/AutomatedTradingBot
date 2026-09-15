# models/worker.py

import threading
import logging
import time
import queue
from utils import send_notification
from typing import TYPE_CHECKING
from database import WorkspaceData

logger = logging.getLogger()

if TYPE_CHECKING:
    from connectors.binance_client import BinanceClient
    from connectors.bitmex import BitmexClient

class Worker(threading.Thread):


    def __init__(self,
                 binance_futures_client: 'BinanceClient',
                 order_status_queue: queue.Queue,
                 ui_update_queue: queue.Queue,
                 pnl_update_queue: queue.Queue,
                 binance_spot_client: 'BinanceClient' = None,
                 bitmex_client: 'BitmexClient' = None):


        super().__init__()
        self.daemon = True  # Allows the program to exit even if this thread is still running

        # Store the clients
        self.binance = binance_futures_client
        self.binance_spot = binance_spot_client
        self.bitmex = bitmex_client

        # Communication Queues
        self.order_status_queue = order_status_queue
        self.ui_update_queue = ui_update_queue
        self.pnl_update_queue = pnl_update_queue

        self.task_queue = queue.Queue()
        self._should_stop = threading.Event()

        threading.Thread(target=self._monitor_strategies, daemon=True).start()
        threading.Thread(target=self._connection_watchdog, daemon=True).start()


    def _monitor_strategies(self):
        while not self._should_stop.is_set():
            # Check Binance Futures
            if self.binance:
                with self.binance.strategies_lock:
                    for strategy in self.binance.strategies.values():
                        strategy.check_trade(tick_type="heartbeat_check")

            # Check Bitmex if you're using it
            if self.bitmex:
                with self.bitmex.strategies_lock:
                    for strategy in self.bitmex.strategies.values():
                        strategy.check_trade(tick_type="heartbeat_check")

            time.sleep(30)

    def run(self):
        logger.info("Worker thread started.")
        logger.info("Worker: Starting background synchronization...")

        # Trigger the initial sync
        self.task_queue.put(("RELOAD_DB", None, None))

        if self.binance:
            # Reconcile local database with the exchange right here
            self._reconcile_boot_state()

        while not self._should_stop.is_set():
            # --- 1. PROCESS THE TASK QUEUE ---
            try:
                task = self.task_queue.get(block=False)

                # Safer unpacking: handles tasks with 2 or 3 items
                action = task[0]
                data = task[1] if len(task) > 1 else None
                extra = task[2] if len(task) > 2 else None

                if action == "RELOAD_DB":
                    self._handle_db_recovery()
                elif action == "ADD_STRATEGY":
                    # data = strat_dict, extra = b_index
                    self._handle_add_strategy(data, extra)
                elif action == "STOP_STRATEGY":
                    # In your UI, you sent: ("STOP_STRATEGY", None, stop_params)
                    # So 'extra' contains the dictionary needed for stopping.
                    self._handle_stop_strategy(extra)

                self.task_queue.task_done()

            except queue.Empty:
                pass
            except Exception as e:
                logger.error(f"Worker Task Error: {e}")

            # --- 2. PROCESS ORDER STATUS UPDATES (Market Events) ---
            try:
                update = self.order_status_queue.get(block=False)
                logger.info(f"Worker received order update: {update}")
                # Logic to route update to the correct strategy...

                self.order_status_queue.task_done()
            except queue.Empty:
                pass

            # Heartbeat sleep to prevent 100% CPU usage
            time.sleep(0.1)

        logger.info("Worker thread stopped gracefully.")

    def _handle_db_recovery(self):
        logger.info("Worker: Starting background synchronization...")

        # 1. SYNC BINANCE FUTURES
        while not self.binance.is_ready:
            try:
                self.binance.connect()
                if len(self.binance.contracts) > 0:
                    self.binance.is_ready = True
                else:
                    raise Exception("No contracts returned.")
            except Exception as e:
                logger.error(f"Worker: Binance Futures sync failed. Retrying... {e}")
                time.sleep(10)

        # --- 2. CONNECT OTHER CLIENTS (Independently) ---
        try:
            if self.bitmex:
                if hasattr(self.bitmex, 'connect'):
                    self.bitmex.connect()
                else:
                    logger.info("Worker: Bitmex client skipping manual connect().")

            if self.binance_spot:
                self.binance_spot.connect()
                logger.info("Worker: Binance Spot synchronized.")

        except Exception as e:
            logger.error(f"Worker: Secondary connection sync failed: {e}")

        # --- 3. RESTORE STRATEGIES ---
        from database import WorkspaceData
        db = WorkspaceData()
        saved_strategies = db.get("strategies")

        # 3. SIGNAL UI TO START LOADING
        # Instead of iterating through DB here, we tell the UI it's safe to run its load logic.
        # This ensures the 'load_strategies' reconciliation happens in the UI thread.
        self.ui_update_queue.put(("CONNECTIONS_READY", None))

        logger.info(f"Worker: Recovery complete. Restored {len(saved_strategies)} strategies.")

    def stop(self):
        """
        Sets the internal flag to stop the thread gracefully.
        """
        self._should_stop.set()

    def _handle_add_strategy(self, params: dict, b_index: int):
        def start_task():
            try:
                self.binance.start_strategy(params, b_index)

            except Exception as e:
                logger.error(f"Worker Error: {e}")
                self.ui_update_queue.put(("STRATEGY_OFF", b_index))

        threading.Thread(target=start_task, daemon=True).start()

    def _handle_stop_strategy(self, data: dict):
        def stop_task():
            try:
                # 1. Safety Check: If data is just an int, we can't 'get' from it
                if not isinstance(data, dict):
                    logger.error(f"Worker: Received invalid stop data type: {type(data)}. Expected dict.")
                    return

                # 2. Extract info safely
                symbol = data.get('contract') or data.get('symbol')
                tf = data.get('timeframe')
                strat_type = data.get('strategy_type')
                b_index = data.get('row_index')

                # 3. Stop the logic in the client
                if symbol and tf and strat_type:
                    self.binance.remove_strategy(symbol, tf, strat_type)

                # 4. Update the UI button state
                if b_index is not None:
                    self.ui_update_queue.put(("STRATEGY_OFF", b_index))

                logger.info(f"Worker: Successfully stopped {strat_type} for {symbol}")

            except Exception as e:
                logger.error(f"Worker: Error during stop_task: {e}")

        threading.Thread(target=stop_task, daemon=True).start()

    def _reconcile_boot_state(self):
        logger.info("Worker: Starting On-Boot Reconciliation...")
        db = WorkspaceData()

        # 1. Get the absolute truth from Binance
        live_positions = self.binance.get_open_positions() if self.binance else []

        # Map clean symbols (e.g. 'BTCUSDT') to live position data
        live_symbols = {p['symbol'].split('_')[0].upper(): p for p in live_positions}

        # 2. Get open trades from local database
        local_trades = db.get("trades") or []
        open_local_trades = [t for t in local_trades if t.get('status') == 'open']

        remaining_open_trades = []

        # 3. Check for ghosts (Trades the DB thinks are open, but Binance already closed)
        for trade in open_local_trades:
            raw_symbol = trade.get('symbol', '')
            clean_symbol = raw_symbol.split('_')[0].upper()

            if clean_symbol not in live_symbols:
                logger.warning(f"Reconciliation: Ghost trade found for {raw_symbol}. Marking as closed.")
                with db.lock:
                    db.cursor.execute("UPDATE trades SET status = 'closed' WHERE entry_id = ?", (trade['entry_id'],))
                db.conn.commit()
            else:
                remaining_open_trades.append(trade)
                # Push valid existing open trade to the UI
                trade_dict = dict(trade)
                self.ui_update_queue.put(("NEW_TRADE", trade_dict))

        # 4. Check for unrecorded positions (Trades open on Binance, but not in our database)
        saved_strategies = db.get("strategies") or []

        for clean_sym, pos_data in live_symbols.items():
            found = any(t.get('symbol', '').split('_')[0].upper() == clean_sym for t in remaining_open_trades)
            if not found:
                logger.info(
                    f"Reconciliation: Adopting unrecorded {pos_data['side'].upper()} position for {clean_sym}...")

                assigned_strat = "Manual / Adopted"
                for s in saved_strategies:
                    strat_contract = s.get('contract', '').split('_')[0].upper()
                    if strat_contract == clean_sym:
                        assigned_strat = s.get('strategy_type', 'Adopted Strategy')
                        break

                adopted_trade = {
                    "time": int(time.time() * 1000),
                    "entry_price": pos_data['entry_price'],
                    "symbol": pos_data['symbol'],
                    "strategy": assigned_strat,
                    "side": pos_data['side'],
                    "status": "open",
                    "pnl": pos_data.get('pnl', 0.0),
                    "quantity": pos_data['size'],
                    "entry_id": str(int(time.time() * 1000))
                }

                # --- THE FIX: Track for Real-Time PnL ---
                if self.binance:
                    from models.models import Trade
                    contract_obj = self.binance.contracts.get(clean_sym)
                    adopted_trade_obj = Trade(adopted_trade, contract_obj)
                    self.binance.active_trades.append(adopted_trade_obj)
                    if contract_obj:
                        self.binance.subscribe_channel([contract_obj], "bookticker")
                # ----------------------------------------

                # Save adopted trade to SQLite and display in UI
                db.insert_trade(adopted_trade)
                self.ui_update_queue.put(("NEW_TRADE", adopted_trade))

        logger.info(f"Worker: Reconciliation complete. Verified {len(live_positions)} live positions.")

    def _connection_watchdog(self):
        logger.info("Worker: WebSocket Watchdog thread initialized.")

        while not self._should_stop.is_set():
            if self.binance:

                # --- 1. WEBSOCKET HEARTBEAT CHECK (THE FIX) ---
                if self.binance.is_ready:
                    try:
                        self.binance.check_connection()
                    except Exception as e:
                        logger.error(f"Watchdog: Error during connection check: {e}")
                # ----------------------------------------------

                # --- 2. FINANCIAL CIRCUIT BREAKER ---
                if getattr(self.binance, 'trading_allowed', False):
                    # Fetch live equity from the client
                    equity = self.binance.get_account_equity()

                    # Only trigger if equity is valid (>0) but below the safety net
                    if 0 < equity < self.binance.min_safe_equity:
                        self.binance.trading_allowed = False
                        logger.critical(f"CIRCUIT BREAKER: Equity dropped to {equity}. Halting new entries.")

                        # Tell the UI
                        if hasattr(self, 'ui_update_queue'):
                            self.ui_update_queue.put(("LOG:SYSTEM",
                                                      f"EMERGENCY: Equity {equity} < {self.binance.min_safe_equity}. Entries halted!"))

                        # --- EMERGENCY NOTIFICATION ---
                        send_notification(
                            f"⚠️ **EMERGENCY CIRCUIT BREAKER TRIPPED** ⚠️\nAccount Equity dropped to ${equity:.2f}. All new entries halted!")
                # ------------------------------------

                # --- 3. 24-HOUR WEBSOCKET UPTIME WATCHDOG ---
                if getattr(self.binance, 'ws_start_time', 0) > 0:
                    uptime = time.time() - self.binance.ws_start_time

                    # 23 hours = 82,800 seconds
                    if uptime > 82800:
                        logger.warning("Watchdog: WebSocket approaching 24h limit. Triggering preventative restart...")
                        try:
                            self.binance.reconnect_ws()
                            self.binance.ws_start_time = time.time()
                            logger.info("Watchdog: Preventative restart successful. Clock reset.")
                        except Exception as e:
                            logger.error(f"Watchdog: Failed to execute preventative restart: {e}")

            # Sleep for 1 minute before checking again
            time.sleep(60)