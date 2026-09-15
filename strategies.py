import logging
import typing
from typing import *
import time
import pandas as pd
import numpy as np
from utils import send_notification

# 1. ONLY import models to prevent the circular crash with BinanceClient
from models.models import Contract, Candle, Trade

# 2. Use TYPE_CHECKING strings for the IDE, but Python ignores this at runtime
if TYPE_CHECKING:
    from connectors.bitmex import BitmexClient
    from connectors.binance_client import BinanceClient

logger = logging.getLogger()

TF_EQUIV = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}


class Strategy:
    def __init__(self, client: Any, contract: Contract, exchange: str,
                 timeframe: str, balance_pct: float, take_profit: float, stop_loss: float,
                 other_params: dict, ui_callback: callable):

        self.client = client
        self.contract = contract
        self.exchange = exchange
        self.tf = timeframe
        self.tf_equiv = TF_EQUIV[timeframe] * 1000
        self.balance_pct = balance_pct
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.other_params = other_params

        self.strat_name = self.__class__.__name__
        self.ongoing_position = False
        self.trades: List[Trade] = []
        self.candles: List[Candle] = []
        self.ui_callback = ui_callback

        # This tag ensures Root handles both the UI update and the logger.info
        self.callback_tag = f"LOG:{self.strat_name}_{self.contract.symbol}"

        self.is_processing = False
        self._last_hb_time = 0
        self._initialized_ui = False
        self.dead = False

        # Protective targets for local synthetic checks
        self._tp_price: Optional[float] = None
        self._sl_price: Optional[float] = None
        self._entry_price: Optional[float] = None

    def _add_log(self, msg: str):
        """Single source of truth for logging via the UI Queue"""
        self.ui_callback((self.callback_tag, msg))

    def parse_trades(self, price: float, size: float, timestamp: int) -> str:
        if len(self.candles) == 0: return "error"
        last_candle = self.candles[-1]

        if timestamp < last_candle.timestamp + self.tf_equiv:
            last_candle.close = price
            last_candle.volume += size
            if price > last_candle.high:
                last_candle.high = price
            elif price < last_candle.low:
                last_candle.low = price
            return "same_candle"

        elif timestamp >= last_candle.timestamp + self.tf_equiv:
            new_ts = last_candle.timestamp + self.tf_equiv
            candle_info = {'ts': new_ts, 'open': price, 'high': price, 'low': price, 'close': price, 'volume': size}
            self.candles.append(Candle(candle_info, self.tf, "parse_trade"))
            if len(self.candles) > 1000: self.candles.pop(0)
            return "new_candle"
        return "error"

    def _open_position(self, signal_result: int, current_price: float):
        import time
        if time.time() - getattr(self, '_last_entry_time', 0) < 60.0:
            self._add_log("STRAT_LOG: Cooldown active. Entry blocked.")
            return

        if self.ongoing_position: return  # Guard: Don't open if already in a trade
        self.ongoing_position = True  # Lock immediately
        self._last_entry_time = time.time()

        # 1. Fallback to last candle close if live price is somehow None
        if not current_price:
            current_price = self.candles[-1].close if self.candles else 0

        # 2. Calculate Position Size
        trade_size = self.client.get_trade_size(self.contract, current_price, self.balance_pct)
        if not trade_size or trade_size <= 0:
            self.ongoing_position = False
            return
        rounded_qty = self.client.round_quantity(self.contract, trade_size)

        # 3. Determine sides
        entry_side = "BUY" if signal_result == 1 else "SELL"
        exit_side = "SELL" if signal_result == 1 else "BUY"

        # ==========================================
        # 4. SEND THE ENTRY ORDER
        # ==========================================
        entry_status = self.client.place_order(self.contract, "MARKET", rounded_qty, entry_side)

        if entry_status and entry_status.order_id:
            actual_entry_price = float(entry_status.avg_price) if entry_status.avg_price else current_price

            # ==========================================
            # 5. CALCULATE EXACT TP / SL TARGET PRICES
            # ==========================================
            tp_pct = self.take_profit / 100
            sl_pct = self.stop_loss / 100

            if signal_result == 1:  # LONG Math
                tp_price = actual_entry_price * (1 + tp_pct)
                sl_price = actual_entry_price * (1 - sl_pct)
            else:  # SHORT Math
                tp_price = actual_entry_price * (1 - tp_pct)
                sl_price = actual_entry_price * (1 + sl_pct)

            # Round prices to match Binance tick sizes (e.g., 2 decimals)
            tp_price = self.client.round_price(self.contract, tp_price)
            sl_price = self.client.round_price(self.contract, sl_price)

            # --- THE FIX: STORE SYNTHETIC SL PRICE FOR BACKUP MONITORING ---
            self._sl_price = sl_price
            self._tp_price = tp_price

            # ==========================================
            # 6. SEND PROTECTIVE ORDERS TO BINANCE
            # ==========================================
            tp_order_id = "None"
            sl_order_id = "None"

            # Dynamic buffer minimum distance threshold
            buffer = actual_entry_price * 0.005

            # Take Profit
            if self.take_profit > 0:
                # Ensure TP is at least 'buffer' away from entry
                safe_tp = tp_price if abs(tp_price - actual_entry_price) > buffer else (
                    actual_entry_price + buffer if signal_result == 1 else actual_entry_price - buffer)

                tp_status = self.client.place_order(
                    contract=self.contract,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=rounded_qty,
                    side=exit_side,
                    extra_params={
                        "triggerPrice": self.client.round_price(self.contract, safe_tp),
                        "closePosition": "true"
                    },
                    is_exit=True
                )
                if tp_status:
                    tp_order_id = str(tp_status.order_id)
                    self._add_log(f"STRAT_LOG: Exchange TP set at {safe_tp}")

            # Stop Loss
            if self.stop_loss is not None and self.stop_loss > 0:
                # Ensure SL is at least 'buffer' away from entry
                safe_sl = sl_price if abs(sl_price - actual_entry_price) > buffer else (
                    actual_entry_price - buffer if signal_result == 1 else actual_entry_price + buffer)

                sl_status = self.client.place_order(
                    contract=self.contract,
                    order_type="STOP_MARKET",
                    quantity=rounded_qty,
                    side=exit_side,
                    extra_params={
                        "triggerPrice": self.client.round_price(self.contract, safe_sl),
                        "closePosition": "true"
                    },
                    is_exit=True
                )
                if sl_status:
                    sl_order_id = str(sl_status.order_id)
                    self._add_log(f"STRAT_LOG: Exchange SL set at {safe_sl}")

            # ==========================================
            # 7. LOG THE TRADE INTERNALLY
            # ==========================================
            self.ongoing_position = True
            new_trade = Trade({
                "time": int(time.time() * 1000),
                "entry_price": actual_entry_price,
                "contract": self.contract,
                "strategy": self.strat_name,
                "symbol": self.contract.symbol,
                "side": "long" if signal_result == 1 else "short",
                "status": "filling", # Initial status
                "pnl": 0,
                "quantity": rounded_qty,
                "entry_id": str(entry_status.order_id), # This MUST be a string
                "tp_order_id": tp_order_id,
                "sl_order_id": sl_order_id
            })
            self.trades.append(new_trade)
            self.ui_callback((f"NEW_TRADE:{self.strat_name}", new_trade.as_dict()))
            self._add_log(f"Opened {new_trade.side}: {rounded_qty} @ {actual_entry_price}")
            send_notification(
                f"🟢 **OPENED {new_trade.side.upper()}** on {self.contract.symbol}\nPrice: {actual_entry_price}\nTP: {tp_price} | SL: {sl_price}")

        else:
            self.ongoing_position = False  # The critical reset
            self._add_log(f"STRAT_LOG: Entry order rejected! Aborting trade.")

    def _safe_int(self, param_key: str, default_val: int) -> int:
        """Safely extracts an integer from GUI params, falling back to default if empty/invalid."""
        raw_val = self.other_params.get(param_key, default_val)
        if raw_val in [None, ""]:
            return default_val
        try:
            return int(raw_val)
        except (ValueError, TypeError):
            return default_val

    def _execute_exit(self, trade: Trade):
        """Sends a Market order to close the position upon hitting SL."""
        order_side = "SELL" if trade.side == "long" else "BUY"

        # Use reduceOnly to ensure we only close the existing position
        order_status = self.client.place_order(
            contract=self.contract,
            order_type="MARKET",
            quantity=trade.quantity,
            side=order_side,
            extra_params={"reduceOnly": True}
        )

        if order_status:
            self._sl_price = None  # Reset local targets
            self._tp_price = None
            self.ongoing_position = False

            # Cancel remaining conditional TP/SL orders via client wrapper method
            try:
                self.client.cancel_all_open_orders(self.contract)
                self._add_log(f"Soft SL executed for {self.contract.symbol}. Cancelled open orders.")
            except Exception as e:
                self._add_log(f"Error cancelling remaining orders: {e}")
        else:
            self._add_log(f"CRITICAL: Soft SL exit order rejected by Binance!")

    def _check_exits(self, current_price: float):
        """Monitors live price against the local Synthetic Stop Loss."""
        if not self.ongoing_position or current_price is None or not self.trades:
            return

        trade = self.trades[-1]

        # Safety: Don't exit within 2 seconds of opening to avoid WebSocket lag
        if time.time() - (trade.time / 1000) < 2.0:
            return

        # Check for Synthetic Stop Loss / Take Profit triggers
        trigger_exit = False
        if trade.side == "long":
            if self._sl_price and current_price <= self._sl_price:
                self._add_log(f"STRAT_LOG: Synthetic SL Triggered at {current_price}!")
                trigger_exit = True
            elif self._tp_price and current_price >= self._tp_price:
                self._add_log(f"STRAT_LOG: Synthetic TP Triggered at {current_price}!")
                trigger_exit = True
        elif trade.side == "short":
            if self._sl_price and current_price >= self._sl_price:
                self._add_log(f"STRAT_LOG: Synthetic SL Triggered at {current_price}!")
                trigger_exit = True
            elif self._tp_price and current_price <= self._tp_price:
                self._add_log(f"STRAT_LOG: Synthetic TP Triggered at {current_price}!")
                trigger_exit = True

        if trigger_exit:
            self._close_position()

    def _close_position(self):
        """Wrapper to trigger the exit execution."""
        if not self.ongoing_position or not self.trades: return
        trade = self.trades[-1]
        self._execute_exit(trade)


class TechnicalStrategy(Strategy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # GUI Parameters - Now 100% crash-proof
        self._rsi_period = self._safe_int('rsi_period', 14)
        self._macd_fast = self._safe_int('macd_fast', 12)
        self._macd_slow = self._safe_int('macd_slow', 26)
        self._macd_signal = self._safe_int('macd_signal', 9)
        self._ema_fast = self._safe_int('ema_fast', 12)
        self._ema_slow = self._safe_int('ema_slow', 26)

        # High-speed memory variables for tick checking
        self.c_ema_f = 0.0
        self.c_ema_s = 0.0
        self.p_ema_f = 0.0
        self.p_ema_s = 0.0
        self.c_macd = 0.0
        self.c_macd_signal = 0.0
        self.c_rsi = 0.0

    def _calculate_indicators(self) -> pd.DataFrame:
        df = pd.DataFrame([c.as_dict() for c in self.candles])
        closes = df['close']

        # 1. EMAs
        df['ema_fast'] = closes.ewm(span=self._ema_fast, adjust=False).mean()
        df['ema_slow'] = closes.ewm(span=self._ema_slow, adjust=False).mean()

        # 2. MACD
        macd_fast_line = closes.ewm(span=self._macd_fast, adjust=False).mean()
        macd_slow_line = closes.ewm(span=self._macd_slow, adjust=False).mean()
        df['macd'] = macd_fast_line - macd_slow_line
        df['macd_signal'] = df['macd'].ewm(span=self._macd_signal, adjust=False).mean()

        # 3. Standard Wilder's Exponential Smoothing RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/self._rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/self._rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss
        df['rsi'] = 100.0 - (100.0 / (1.0 + rs))

        return df

    def check_trade(self, tick_type: str):
        if self.dead or self.is_processing: return

        live_price = self.client.prices.get(self.contract.symbol, {}).get('bid')
        if live_price is None and self.candles:
            live_price = self.candles[-1].close

        if self.ongoing_position:
            self._check_exits(live_price)
            # CRITICAL: Return here so we don't evaluate
            # new entries while an exit is in progress
            return

        #==========================================
        #--- TEMPORARY FORCE TRADE HACK ---
        # if live_price is not None:
        #     self._add_log(f"!!! FORCE HACK: OVERRIDING ONGOING POSITION AT {live_price} !!!")
        #     self._open_position(1, live_price)
        #     return
        #==========================================

        if time.time() - self._last_hb_time > 30:
            self._add_log(f"HB: Technical active | Price: {live_price}")
            self._last_hb_time = time.time()

        if not self._initialized_ui:
            b_index = self.other_params.get('row_index')
            if b_index is not None: self.ui_callback(("STRATEGY_ON", b_index))
            self._initialized_ui = True

        if len(self.candles) < max(self._ema_slow, self._macd_slow + self._macd_signal) + 2:
            return

        self.is_processing = True
        try:
            # 1. Heavy Pandas Math ONLY on 1-minute candle close
            if tick_type == "new_candle":
                df = self._calculate_indicators()
                c = df.iloc[-2]  # Last fully closed candle
                p = df.iloc[-3]  # Prior closed candle

                # Save the new indicator values into memory
                self.c_ema_f, self.c_ema_s = c['ema_fast'], c['ema_slow']
                self.p_ema_f, self.p_ema_s = p['ema_fast'], p['ema_slow']
                self.c_macd, self.c_macd_signal = c['macd'], c['macd_signal']
                self.c_rsi = c['rsi']

                # 2. Trade execution check on closed candle indicators
                if live_price is not None and self.c_ema_f > 0 and not self.ongoing_position:

                    # LONG Logic: EMA crosses UP + Bullish MACD + RSI in momentum zone
                    if (self.p_ema_f <= self.p_ema_s and self.c_ema_f > self.c_ema_s and
                            self.c_macd > self.c_macd_signal and 48 < self.c_rsi < 70):

                        self._add_log("STRAT_LOG: LONG Confirmed by RSI & MACD!")
                        self._open_position(1, live_price)

                    # SHORT Logic: EMA crosses DOWN + Bearish MACD + RSI in momentum zone
                    elif (self.p_ema_f >= self.p_ema_s and self.c_ema_f < self.c_ema_s and
                          self.c_macd < self.c_macd_signal and 30 < self.c_rsi < 52):

                        self._add_log("STRAT_LOG: SHORT Confirmed by RSI & MACD!")
                        self._open_position(-1, live_price)

        finally:
            self.is_processing = False


class BreakoutStrategy(Strategy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # GUI Parameters - Now 100% crash-proof
        self._window = self._safe_int('window', 30)

    def check_trade(self, tick_type: str):
        if self.dead or self.is_processing: return
        live_price = self.client.prices.get(self.contract.symbol, {}).get('bid')
        if live_price is None and self.candles:
            live_price = self.candles[-1].close

        if self.ongoing_position:
            self._check_exits(live_price)
            # CRITICAL: Return here so we don't evaluate
            # new entries while an exit is in progress
            return

        # ==========================================
        # --- TEMPORARY FORCE TRADE HACK ---
        #if not self.ongoing_position and live_price is not None:
        #    self._add_log(f"STRAT_LOG: FORCING A TEST TRADE AT {live_price}")
        #    self._open_position(1, live_price)
        #    return
        # ==========================================

        # Update Heartbeat logic to be cleaner
        if time.time() - self._last_hb_time > 30:
            self._add_log(f"HB: Breakout active | Price: {live_price}")
            self._last_hb_time = time.time()

        if not self._initialized_ui:
            b_index = self.other_params.get('row_index')
            if b_index is not None: self.ui_callback(("STRATEGY_ON", b_index))
            self._initialized_ui = True

        if tick_type == "new_candle" and len(self.candles) > (self._window + 2) and not self.ongoing_position:
            self.is_processing = True
            try:
                # Calculate bounds excluding active candle (-1) and breakout candle (-2)
                recent = self.candles[-(self._window + 2):-2]
                if not recent:
                    return

                hh, ll = max(c.high for c in recent), min(c.low for c in recent)
                current_close = self.candles[-2].close

                # --- X-RAY VISION LOGGING ---
                self._add_log(f"STRAT_LOG: Breakout Check - Close: {current_close} | Upper: {hh} | Lower: {ll}")

                # Entry Logic
                if current_close > hh:
                    self._add_log("STRAT_LOG: Breakout UP detected!")
                    self._open_position(1, live_price)
                elif current_close < ll:
                    self._add_log("STRAT_LOG: Breakout DOWN detected!")
                    self._open_position(-1, live_price)
            finally:
                self.is_processing = False


class IchimokuStrategy(Strategy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # GUI Parameters - Now 100% crash-proof
        self._tenkan_p = self._safe_int("tenkan", 9)
        self._kijun_p = self._safe_int("kijun", 26)
        self._senkou_b_p = self._safe_int("senkou", 52)

        # New memory variables for high-speed tick checking
        self.c_tenkan = 0.0
        self.c_kijun = 0.0
        self.p_tenkan = 0.0
        self.p_kijun = 0.0
        self.c_senkou_a = 0.0
        self.c_senkou_b = 0.0

    def _calculate_ichimoku(self) -> pd.DataFrame:
        df = pd.DataFrame([c.as_dict() for c in self.candles])
        df['tenkan'] = (df['high'].rolling(self._tenkan_p).max() + df['low'].rolling(self._tenkan_p).min()) / 2
        df['kijun'] = (df['high'].rolling(self._kijun_p).max() + df['low'].rolling(self._kijun_p).min()) / 2
        df['senkou_a'] = (df['tenkan'] + df['kijun']) / 2
        df['senkou_b'] = (df['high'].rolling(self._senkou_b_p).max() + df['low'].rolling(self._senkou_b_p).min()) / 2
        return df

    def check_trade(self, tick_type: str):
        if self.dead or self.is_processing: return

        live_price = self.client.prices.get(self.contract.symbol, {}).get('bid')
        if live_price is None and self.candles:
            live_price = self.candles[-1].close

        if self.ongoing_position:
            self._check_exits(live_price)
            # CRITICAL: Return here so we don't evaluate
            # new entries while an exit is in progress
            return

        #==========================================
        #--- TEMPORARY FORCE TRADE HACK ---
        #if not self.ongoing_position and live_price is not None:
        #     self._add_log(f"STRAT_LOG: FORCING A TEST TRADE AT {live_price}")
        #     self._open_position(1, live_price)  # 1 forces a LONG position
        #     return
        #==========================================

        if time.time() - self._last_hb_time > 15:
            self._add_log(f"HB: Ichimoku active | Price: {live_price}")
            self._last_hb_time = time.time()

        if not self._initialized_ui:
            b_index = self.other_params.get('row_index')
            if b_index is not None: self.ui_callback(("STRATEGY_ON", b_index))
            self._initialized_ui = True

        if len(self.candles) < (self._senkou_b_p + 2): return

        self.is_processing = True
        try:
            # 1. Heavy Pandas Math ONLY happens once per minute on closed candles
            if tick_type == "new_candle":
                df = self._calculate_ichimoku()
                c = df.iloc[-2]  # Last fully closed candle
                p = df.iloc[-3]  # Prior closed candle

                # Save bounds to memory
                self.c_tenkan, self.c_kijun = c.tenkan, c.kijun
                self.p_tenkan, self.p_kijun = p.tenkan, p.kijun
                self.c_senkou_a, self.c_senkou_b = c.senkou_a, c.senkou_b

                # 2. Live price check against calculated bounds
                if live_price is not None and self.c_tenkan > 0 and not self.ongoing_position:
                    cloud_top = max(self.c_senkou_a, self.c_senkou_b)
                    cloud_bottom = min(self.c_senkou_a, self.c_senkou_b)

                    if self.p_tenkan <= self.p_kijun and self.c_tenkan > self.c_kijun and live_price > cloud_top:
                        self._open_position(1, live_price)
                    elif self.p_tenkan >= self.p_kijun and self.c_tenkan < self.c_kijun and live_price < cloud_bottom:
                        self._open_position(-1, live_price)
        finally:
            self.is_processing = False


    def cancel_all_open_orders(self, contract: Contract):
        """Cancels all open and conditional orders for a specific symbol."""
        data = {
            'symbol': contract.symbol,
            'timestamp': int(time.time() * 1000) + getattr(self, '_time_offset', 0),
            'recvWindow': 60000
        }
        data['signature'] = self._generate_signature(data)

        endpoint = "/fapi/v1/allOpenOrders" if self.futures else "/api/v3/openOrders"
        response = self._make_request("DELETE", endpoint, data)

        if response is not None:
            logger.info(f"Successfully cancelled all open orders for {contract.symbol}")
            return response
        else:
            logger.error(f"Failed to cancel open orders for {contract.symbol}")
            return None