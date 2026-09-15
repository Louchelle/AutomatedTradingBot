from binance import AsyncClient
import logging
import threading
import requests
import typing
from urllib.parse import urlencode
import hmac
import hashlib
from utils import send_notification
from math import log, floor
from binance import ThreadedWebsocketManager
from strategies import Strategy, TechnicalStrategy, BreakoutStrategy, IchimokuStrategy
from models.models import *

logger = logging.getLogger()


class BinanceClient:
    WS_URL = "wss://stream.binance.com:9443/ws/"

    # Combined mapping for all stream types
    STREAM_SUFFIXES = {
        "aggtrade": "@aggTrade",
        "bookticker": "@bookTicker",
        "kline": "@kline"
    }
    _ws_startup_lock = False

    def __init__(self, public_key: str, secret_key: str, testnet: bool, futures: bool, pnl_update_callback=None,
                 on_trade_update=None, root=None):

        self.root = root

        self.ws_subscriptions: typing.Dict = {
            "bookticker": [],
            "aggtrade": [],
            "kline": []
        }

        self.pnl_update_callback = pnl_update_callback
        self.futures = futures
        self.on_trade_update = on_trade_update

        if self.futures:
            self.platform = "binance_futures"
            self.ws_start_time = 0
            if testnet:
                # Use the specific Testnet domain
                self._base_url = "https://testnet.binancefuture.com"
                self._wss_url = "wss://fstream.binancefuture.com/stream"
            else:
                self._base_url = "https://fapi.binance.com"
                self._wss_url = "wss://fstream.binance.com/stream"
        else:
            self.platform = "binance_spot"
            if testnet:
                self._base_url = "https://testnet.binance.vision"
                self._wss_url = "wss://testnet.binance.vision/stream"
            else:
                self._base_url = "https://api.binance.com"
                self._wss_url = "wss://stream.binance.com:9443/stream"

        self._public_key = public_key
        self._secret_key = secret_key
        self._headers = {'X-MBX-APIKEY': self._public_key}

        if not self.futures:
            time.sleep(0.5)

        self.contracts = {}
        self.balances = {}
        self.prices = dict()
        self.strategies_lock = threading.Lock()
        self.logs = []

        async def mock_ping(*args, **kwargs):
            return {}

        async def mock_time(*args, **kwargs):
            import time  # Ensure time is available
            return {'serverTime': int(time.time() * 1000) + getattr(self, '_time_offset', 0)}

        AsyncClient.ping = mock_ping
        AsyncClient.get_server_time = mock_time

        self._twm = ThreadedWebsocketManager(api_key=self._public_key,
                                             api_secret=self._secret_key,
                                             testnet=testnet)

        if self.futures:
            self._twm.is_futures = True

        self.ws_connected = False
        self.last_update_time = time.time()
        self.testnet = testnet
        self.active_trades: typing.List[Trade] = []
        self.pnl_update_queue = None
        self._active_subscriptions = set()
        self.strategies: typing.Dict[str, Strategy] = {}
        self.is_ready = False
        self._ws_lock = threading.Lock()

        if self.futures:
            logger.info("Binance Futures Client successfully initialized (WS deferred)")

        self._last_order_timestamps = {}
        self._order_cooldown = 10

        # Time Sync for strict Binance timestamp rules
        self._time_offset = 0
        self._sync_server_time()

        # Maximum Drawdown Circuit Breaker
        self.trading_allowed = True
        self.max_drawdown_pct = 0.20  # Shut down if we lose 20% of starting balance
        self.min_safe_equity = 0.0  # This will automatically calculate on boot

    def _initial_setup(self):
        try:
            self.get_contracts()

            if self.contracts:
                self.is_ready = True
                self.root.ui_update_queue.put(("CONNECTIONS_READY", None))
                logger.info(f"Binance Client: Initial setup complete. {len(self.contracts)} symbols loaded.")
            else:
                logger.error("Binance Client: Setup failed - No symbols loaded.")
        except Exception as e:
            logger.error(f"Binance Client: Initial setup failed: {e}")

    def connect(self):
        logger.info(f"BinanceClient: Attempting connection to {'Futures' if self.futures else 'Spot'}...")

        try:
            self.get_contracts()

            if not self.contracts:
                logger.error("BinanceClient: Failed to retrieve contracts. Connection aborted.")
                self.is_ready = False
                return False

            self.get_balances()

            if self.futures:
                self.open_positions = self.get_open_positions()

            # --- DYNAMIC CIRCUIT BREAKER MATH ---
            initial_equity = self.get_account_equity()
            # Only calculate this once per session (when it is 0.0)
            if initial_equity > 0 and self.min_safe_equity == 0.0:
                self.min_safe_equity = initial_equity * (1 - self.max_drawdown_pct)
                logger.info(
                    f"Circuit Breaker Set: Starting Balance ${initial_equity:.2f}. Kill-switch locked at ${self.min_safe_equity:.2f}")
            # ------------------------------------

            self.is_ready = True
            logger.info("BinanceClient: Successfully synchronized exchange data.")
            return True

        except requests.exceptions.ConnectionError:
            logger.error("BinanceClient: Network connection error. Check your internet.")
            self.is_ready = False
            return False

        except requests.exceptions.HTTPError as e:
            logger.error(f"BinanceClient: Binance API returned an error: {e}")
            self.is_ready = False
            return False

        except Exception as e:
            logger.error(f"BinanceClient: Unexpected error during connection: {e}")
            self.is_ready = False
            return False

    def _add_log(self, msg: str):

        logger.info("%s", msg)
        self.logs.append({"log": msg, "displayed": False})

    def _generate_signature(self, data: typing.Dict) -> str:
        query_string = urlencode(data)

        # THE FIX: Wrap the secret key in a bytearray too!
        mac = hmac.new(bytearray(self._secret_key.encode('utf-8')), digestmod=hashlib.sha256)
        mac.update(bytearray(query_string.encode('utf-8')))

        return mac.hexdigest()

    def _make_request(self, method: str, endpoint: str, data: typing.Dict, is_retry: bool = False):
        url = self._base_url + endpoint
        headers = {'X-MBX-APIKEY': self._public_key}
        timeout = (10, 20)
        response = None

        try:
            if method == "GET":
                response = requests.get(url, params=data, headers=headers, timeout=timeout)
            elif method == "POST":
                response = requests.post(url, params=data, headers=headers, timeout=timeout)
            elif method == "DELETE":
                response = requests.delete(url, params=data, headers=headers, timeout=timeout)
            elif method == "PUT":
                response = requests.put(url, params=data, headers=headers, timeout=timeout)
            else:
                logger.error(f"Error while making {method} request to {endpoint}: "
                             f"{response.json()} (Status {response.status_code})")
                return None

        except requests.exceptions.Timeout as er:
            logger.error(f"Connection error while making {method} request to {endpoint}: {er}")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"Binance Connection Error: Could not reach {url}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during {method} {endpoint}: {e}")
            return None

        # Handle Response
        if response is not None:
            if response.status_code == 200:
                return response.json()
            else:
                try:
                    err_data = response.json()
                    err_code = err_data.get('code')

                    # AUTO-RECOVERY: Binance error -1021 (Timestamp out of recvWindow)
                    if err_code == -1021 and not is_retry:
                        logger.warning(
                            f"Binance API Timestamp Error -1021 on {endpoint}. Re-syncing clock & retrying...")
                        self._sync_server_time()

                        # Re-stamp and re-sign payload
                        retry_data = dict(data)
                        if 'timestamp' in retry_data:
                            retry_data['timestamp'] = int(time.time() * 1000) + getattr(self, '_time_offset', 0)
                        if 'signature' in retry_data:
                            retry_data.pop('signature', None)
                            retry_data['signature'] = self._generate_signature(retry_data)

                        return self._make_request(method, endpoint, retry_data, is_retry=True)

                    logger.error(f"Binance API Error {response.status_code} ({endpoint}): {err_data}")
                except Exception:
                    logger.error(f"Binance API Error {response.status_code} ({endpoint}): {response.text}")
                return None

        return None

    def get_contracts(self) -> typing.Dict[str, Contract]:

        # 1. Determine correct endpoint based on Futures vs Spot
        endpoint = "/fapi/v1/exchangeInfo" if self.futures else "/api/v3/exchangeInfo"

        try:
            exchange_info = self._make_request("GET", endpoint, dict())
        except Exception as e:
            logger.error(f"Error fetching exchange info from {endpoint}: {e}")
            return {}

        temp_contracts = dict()

        if exchange_info is not None and 'symbols' in exchange_info:
            for contract_data in exchange_info['symbols']:
                if contract_data.get('status') != 'TRADING':
                    continue

                raw_symbol = contract_data['symbol'].upper()

                # Create the base Contract object
                contract_obj = Contract(contract_data, self.platform)

                # --- FIX: MAP FILTERS TO ATTRIBUTES ---
                # Extract tick_size (Price precision) and lot_size (Quantity precision)
                for f in contract_data['filters']:
                    if f['filterType'] == 'PRICE_FILTER':
                        contract_obj.tick_size = float(f['tickSize'])
                    elif f['filterType'] in ['LOT_SIZE', 'MARKET_LOT_SIZE']:
                        # Use float to ensure math operations in round_quantity work
                        contract_obj.lot_size = float(f['stepSize'])

                temp_contracts[raw_symbol] = contract_obj

            logging.info(f"Binance {'Futures' if self.futures else 'Spot'} loaded {len(temp_contracts)} symbols.")

            self.contracts = temp_contracts
            return self.contracts
        else:
            logging.warning("Exchange info received was empty or malformed.")
            return {}

    def get_historical_candles(self, contract: Contract, interval: str) -> typing.List[Candle]:

        data = dict()
        data['symbol'] = contract.symbol
        data['interval'] = interval
        data['limit'] = 1000

        endpoint = "/fapi/v1/klines" if self.futures else "/api/v3/klines"

        logger.info(f"Fetching {data['limit']} candles for {contract.symbol}...")

        try:
            raw_candles = self._make_request("GET", endpoint, data)
        except Exception as e:
            logger.error(f"Error connecting to {endpoint}: {e}")
            return []

        candles = []

        if raw_candles is not None:
            for c in raw_candles:
                candles.append(Candle(c, interval, self.platform))

            logger.info(f"Successfully loaded {len(candles)} candles for {contract.symbol}")
        else:
            logger.error(f"Failed to fetch candles for {contract.symbol} - Binance returned None")

        return candles

    def get_bid_ask(self, contract: Contract) -> typing.Dict[str, float]:
        data = dict()
        data['symbol'] = contract.symbol

        if self.futures:
            ob_data = self._make_request("GET", "/fapi/v1/ticker/bookTicker", data)
        else:
            ob_data = self._make_request("GET", "/api/v3/ticker/bookTicker", data)

        if ob_data is not None:
            if contract.symbol not in self.prices:  # Add the symbol to the dictionary if needed
                self.prices[contract.symbol] = {'bid': float(ob_data['bidPrice']), 'ask': float(ob_data['askPrice'])}
            else:
                self.prices[contract.symbol]['bid'] = float(ob_data['bidPrice'])
                self.prices[contract.symbol]['ask'] = float(ob_data['askPrice'])

            return self.prices[contract.symbol]

    def get_balances(self) -> typing.Dict[str, Balance]:

        data = {
            'timestamp': int(time.time() * 1000) + getattr(self, '_time_offset', 0),
            'recvWindow': 60000
        }
        data['signature'] = self._generate_signature(data)

        endpoint = "/fapi/v2/account" if self.futures else "/api/v3/account"
        account_data = self._make_request("GET", endpoint, data)

        balances = dict()

        if account_data is not None:
            try:
                key = 'assets' if self.futures else 'balances'
                if key in account_data:
                    for a in account_data[key]:
                        balances[a['asset']] = Balance(a, self.platform)
            except Exception as e:
                logger.error("Error parsing Binance balance data: %s", e)

        return balances

    def place_order(self, contract: Contract, order_type: str, quantity: float, side: str, extra_params=None,
                    is_exit=False):
        try:
            data = dict()
            data['symbol'] = contract.symbol
            data['side'] = side.upper()
            data['type'] = order_type.upper()

            if extra_params:
                for k, v in extra_params.items():
                    data[k] = v

            # 1. Handle endpoint routing and Algo parameters
            algo_types = ["STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT", "TRAILING_STOP_MARKET"]

            if self.futures and data['type'] in algo_types:
                endpoint = "/fapi/v1/algoOrder"
                data['algoType'] = "CONDITIONAL"

                # --- THE FINAL FIX: Rename stopPrice to triggerPrice for Algo API ---
                if 'stopPrice' in data:
                    data['triggerPrice'] = data.pop('stopPrice')
            else:
                endpoint = "/fapi/v1/order" if self.futures else "/api/v3/order"

            # 2. Quantity handling (Only add if not a closePosition order)
            is_close_position = str(data.get('closePosition', 'false')).lower() == 'true'
            if quantity > 0 and not is_close_position:
                data['quantity'] = round(float(quantity), contract.quantity_decimals)

            # 3. Finalize and Sign
            data['timestamp'] = int(time.time() * 1000) + getattr(self, '_time_offset', 0)
            data['recvWindow'] = 60000
            data['signature'] = self._generate_signature(data)

            logger.info(f"DEBUG_ORDER: Routing {data['type']} to {endpoint} with payload: {data}")
            order_status = self._make_request("POST", endpoint, data)

            if order_status is not None:
                # Algo orders return 'clientAlgoId' instead of 'orderId'
                order_id = order_status.get('clientAlgoId', order_status.get('orderId'))
                logger.info(f"ORDER SUCCESS: {contract.symbol} ID: {order_id}")
                return OrderStatus(order_status, self.platform)

        except Exception as e:
            logger.error(f"CRITICAL: Failed to place {side} order for {contract.symbol}: {e}")
            return None

    def cancel_order(self, contract: Contract, order_id: int) -> OrderStatus:
        data = dict()
        data['orderId'] = order_id
        data['symbol'] = contract.symbol
        data['timestamp'] = int(time.time() * 1000) + getattr(self, '_time_offset', 0)
        data['recvWindow'] = 60000

        data['signature'] = self._generate_signature(data)

        # Try standard order cancellation first
        endpoint = "/fapi/v1/order" if self.futures else "/api/v3/order"
        order_status = self._make_request("DELETE", endpoint, data)

        # If it returns None (failed), try the new Algo endpoint
        if order_status is None and self.futures:
            logger.info("Standard cancel failed, attempting Algo cancel...")
            order_status = self._make_request("DELETE", "/fapi/v1/algoOrder", data)

        if order_status is not None:
            if not self.futures:
                order_status['avgPrice'] = self._get_execution_price(contract, order_id)
            order_status = OrderStatus(order_status, self.platform)

        return order_status

    def _get_execution_price(self, contract: Contract, order_id: int) -> float:
        data = {
            'timestamp': int(time.time() * 1000) + getattr(self, '_time_offset', 0),
            'symbol': contract.symbol,
            'signature': None
        }

        endpoint = "/fapi/v1/userTrades" if self.futures else "/api/v3/myTrades"

        data['signature'] = self._generate_signature(data)
        trades = self._make_request("GET", endpoint, data)

        avg_price = 0
        if trades:
            relevant_trades = [t for t in trades if int(t.get('orderId')) == order_id]
            if not relevant_trades:
                return 0.0

            total_qty = sum(float(t['qty']) for t in relevant_trades)
            for t in relevant_trades:
                fill_pct = float(t['qty']) / total_qty
                avg_price += (float(t['price']) * fill_pct)

        return round(round(avg_price / contract.tick_size) * contract.tick_size, 8)

    def get_order_status(self, contract: Contract, order_id: int) -> OrderStatus:

        data = {
            'timestamp': int(time.time() * 1000) + getattr(self, '_time_offset', 0),
            'symbol': contract.symbol,
            'orderId': order_id,
            'recvWindow': 60000
        }
        data['signature'] = self._generate_signature(data)

        endpoint = "/fapi/v1/order" if self.futures else "/api/v3/order"
        raw_response = self._make_request("GET", endpoint, data)

        if raw_response:
            return OrderStatus(raw_response, self.platform)
        return None

    def _on_message(self, msg: typing.Dict):
        self.last_update_time = time.time()

        # 1. Catch specific connection/read loop errors from the underlying library
        if "error" in msg:
            err_msg = str(msg.get("error")).lower()
            if "read loop has been closed" in err_msg or "reset" in err_msg:
                logger.warning("Binance WS: Read loop closed detected. Triggering recovery...")
                self.ws_connected = False
                self.reconnect_ws()
                return

        try:
            # Unwrap multiplexed streams
            if "stream" in msg and "data" in msg:
                msg = msg["data"]

            event_type = msg.get("e")

            # -------------------------------------------------------------
            # 1. ACCOUNT UPDATE (Position PnL & Balance Stream)
            # -------------------------------------------------------------
            if event_type == "ACCOUNT_UPDATE":
                account_data = msg.get("a", {})
                positions = account_data.get("P", [])
                for pos in positions:
                    symbol = pos.get("s")
                    realtime_pnl = float(pos.get("up", 0.0))
                    position_amt = abs(float(pos.get("pa", 0.0)))

                    if self.pnl_update_callback and position_amt > 0:
                        self.pnl_update_callback({
                            "symbol": symbol,
                            "pnl": realtime_pnl,
                            "quantity": position_amt,
                            "status": "open"
                        })

            # -------------------------------------------------------------
            # 2. ORDER TRADE UPDATE (Fills, Exits, Cancels)
            # -------------------------------------------------------------
            elif event_type == "ORDER_TRADE_UPDATE":
                order_data = msg.get("o", {})
                status = order_data.get("X")  # FILLED, NEW, CANCELED, etc.
                symbol = order_data.get("s")
                order_id = str(order_data.get("i"))
                order_type = order_data.get("o")  # LIMIT, MARKET, STOP_MARKET, etc.

                fill_qty = float(order_data.get("z", 0.0))

                if status == "FILLED":
                    with self.strategies_lock:
                        for strat in self.strategies.values():
                            if strat.contract.symbol == symbol:
                                # Safe iteration over shallow copy to allow list modifications
                                for trade in strat.trades[:]:
                                    # A. ENTRY ORDER MATCH
                                    if str(getattr(trade, 'entry_id', '')) == order_id:
                                        trade.status = "open"
                                        trade.quantity = fill_qty

                                        if self.pnl_update_callback:
                                            self.pnl_update_callback({
                                                "symbol": symbol,
                                                "pnl": 0.0,
                                                "quantity": trade.quantity,
                                                "status": "open",
                                                "entry_id": trade.entry_id
                                            })
                                        break

                                    # B. CLOSED TRADE (Exit Order Match)
                                    elif str(getattr(trade, 'entry_id', '')) != str(order_id) and trade.status in [
                                        "open", "closed"]:
                                        # 1. Bulk cancel any remaining orphaned TP/SL orders on the exchange
                                        try:
                                            self.cancel_all_open_orders(strat.contract)
                                        except Exception as e:
                                            logger.error(f"Error cancelling open orders on fill: {e}")

                                        # 2. Update database via WorkspaceData
                                        db = None
                                        try:
                                            from database_2 import WorkspaceData
                                            db = WorkspaceData()
                                            db.update_trade_status(trade.entry_id, "closed")
                                        except Exception as e:
                                            logger.error(f"Database update failed on trade close: {e}")
                                        finally:
                                            if db and hasattr(db, 'conn') and db.conn:
                                                try:
                                                    db.cursor.close()
                                                    db.conn.close()
                                                except Exception:
                                                    pass

                                        # 3. Calculate PnL and Fees
                                        fill_price = float(order_data.get("L", order_data.get("ap", 0.0)))
                                        entry = float(trade.entry_price)
                                        side_mult = 1 if trade.side.lower() == "long" else -1

                                        taker_fee = 0.0004  # 0.04% Market
                                        maker_fee = 0.0002  # 0.02% Limit
                                        exit_fee = maker_fee if order_type == "LIMIT" else taker_fee

                                        gross_pnl_pct = ((fill_price - entry) / entry) * side_mult
                                        net_pnl_pct = gross_pnl_pct - (taker_fee + exit_fee)

                                        pnl_pct = net_pnl_pct * 100
                                        pnl_usdt = (trade.quantity * entry) * net_pnl_pct

                                        # 4. Dispatch Notifications & Append to Ledger
                                        status_emoji = "🟢 WIN" if pnl_pct > 0 else "🔴 LOSS"
                                        notif_msg = f"{status_emoji}: CLOSED {trade.side.upper()} on {symbol}\nEntry: {entry}\nExit: {fill_price}\n**PnL: {pnl_pct:.2f}% (${pnl_usdt:.2f})**"
                                        send_notification(notif_msg)

                                        from datetime import datetime
                                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                        log_line = f"{timestamp} | {status_emoji} | {symbol} {trade.side.upper()} | PnL: {pnl_pct:.2f}% | Profit: ${pnl_usdt:.2f}\n"

                                        with open("Win_Loss_Ledger.txt", "a", encoding='utf-8') as file:
                                            file.write(log_line)

                                        # 5. Clear strategy locks and purge trade object from memory
                                        strat.ongoing_position = False
                                        strat._sl_price = None
                                        strat._tp_price = None

                                        if trade in strat.trades:
                                            strat.trades.remove(trade)
                                        break

            # -------------------------------------------------------------
            # 3. KLINE (CANDLE) LOGIC
            # -------------------------------------------------------------
            elif event_type == "kline":
                kline = msg.get("k", {})
                symbol = kline.get("s")
                timeframe = kline.get("i")
                is_closed = kline.get("x", False)

                close_price = float(kline.get("c"))
                volume = float(kline.get("v"))
                timestamp = int(kline.get("t"))

                with self.strategies_lock:
                    for strat_id, strat in self.strategies.items():
                        if strat.contract.symbol == symbol and strat.tf == timeframe:
                            strat.parse_trades(close_price, volume, timestamp)
                            if is_closed:
                                strat.check_trade("new_candle")
                            else:
                                strat.check_trade("tick")

            # -------------------------------------------------------------
            # 4. BOOKTICKER LOGIC (Real-time Price & PnL Calculations)
            # -------------------------------------------------------------
            elif event_type == "bookTicker" or ("u" in msg and "s" in msg and event_type is None):
                symbol = msg["s"]
                bid = float(msg["b"])
                ask = float(msg["a"])

                if symbol not in self.prices:
                    self.prices[symbol] = {}
                self.prices[symbol] = {'bid': bid, 'ask': ask}

                # 1. Run Strategy Ticks (If active)
                with self.strategies_lock:
                    for strat in self.strategies.values():
                        if strat.contract.symbol == symbol:
                            strat.check_trade("tick")

                            for trade in strat.trades:
                                if trade.status == "open" and trade.quantity and trade.quantity > 0:
                                    curr_price = bid if trade.side.lower() == "long" else ask
                                    trade.pnl = (
                                                            curr_price - trade.entry_price) * trade.quantity if trade.side.lower() == "long" else (
                                                                                                                                                              trade.entry_price - curr_price) * trade.quantity

                                    current_time = time.time()
                                    if current_time - getattr(trade, 'last_pnl_update', 0) > 0.5:
                                        trade.last_pnl_update = current_time
                                        if self.pnl_update_callback:
                                            self.pnl_update_callback({
                                                "symbol": symbol,
                                                "pnl": trade.pnl,
                                                "quantity": trade.quantity,
                                                "time": getattr(trade, 'time', 0),
                                                "entry_id": getattr(trade, 'entry_id', ''),
                                                "status": trade.status
                                            })

                # 2. Fallback: Synced Open Positions (If strategy is OFF)
                if hasattr(self, 'open_positions') and self.open_positions:
                    for pos in self.open_positions:
                        if pos['symbol'] == symbol and float(pos.get('size', 0)) > 0:
                            curr_price = bid if pos['side'].lower() == 'long' else ask
                            entry = pos['entry_price']
                            qty = pos['size']
                            pnl = (curr_price - entry) * qty if pos['side'].lower() == 'long' else (
                                                                                                               entry - curr_price) * qty

                            if self.pnl_update_callback:
                                self.pnl_update_callback({
                                    "symbol": symbol,
                                    "pnl": pnl,
                                    "quantity": qty,
                                    "status": "open"
                                })

        except Exception as e:
            import traceback
            logger.error(f"CRITICAL ERROR IN WS CALLBACK:\n{traceback.format_exc()}")

    def subscribe_channel(self, contracts: typing.List[Contract], channel_type: str, reconnection=False,
                          interval: str = None):

        logger.info(f"DEBUG WS: subscribe_channel triggered for '{channel_type}'")

        if self._twm is None:
            logger.error("DEBUG WS: FAILED - self._twm is None! Socket manager not initialized.")
            return

        streams_to_subscribe = []

        for contract in contracts:
            sym = contract.symbol.lower()

            # 1. Formatting Logic (REMOVED .lower() to preserve Binance's required camelCase)
            if channel_type == "kline" and interval:
                stream_name = f"{sym}@kline_{interval}"
            else:
                suffix = self.STREAM_SUFFIXES.get(channel_type)
                if not suffix:
                    logger.warning(f"DEBUG WS: FAILED - No suffix found in STREAM_SUFFIXES for '{channel_type}'")
                    continue
                stream_name = f"{sym}{suffix}"

                # 2. Check Cache
            if stream_name not in self._active_subscriptions:
                streams_to_subscribe.append(stream_name)
            else:
                logger.info(f"DEBUG WS: '{stream_name}' skipped - already in _active_subscriptions.")

        # 3. Execution Check
        if not streams_to_subscribe:
            logger.warning(f"DEBUG WS: FAILED - No valid streams to subscribe to for '{channel_type}'. Aborting.")
            return

        logger.info(f"DEBUG WS: Attempting multiplex subscription for: {streams_to_subscribe}")

        if not self.ws_connected:
            logger.info("DEBUG WS: ws_connected is False, starting WS thread...")
            self.start_ws_thread()

        # RETRY LOOP START
        for attempt in range(1, 6):
            try:
                if self._twm:
                    if self.futures:
                        self._twm.start_futures_multiplex_socket(callback=self._on_message,
                                                                 streams=streams_to_subscribe)
                    else:
                        self._twm.start_multiplex_socket(callback=self._on_message,
                                                         streams=streams_to_subscribe)

                    logger.info(f"Binance WS: New subscriptions successful: {streams_to_subscribe}")
                    for s in streams_to_subscribe:
                        self._active_subscriptions.add(s)

                    return

            except Exception as e:
                logger.warning(f"Attempt {attempt}: Multiplex failed. Error: {e}. Waiting 2s...")

            time.sleep(2)

    def get_trade_size(self, contract: Contract, price: float, balance_pct: float):
        logger.info("Getting Binance trade size...")
        balances = self.get_balances()

        if balances is not None:
            if contract.quote_asset in balances:
                if self.futures:
                    balance = balances[contract.quote_asset].wallet_balance
                else:
                    balance = balances[contract.quote_asset].free
            else:
                logger.warning("WARNING: Quote asset balance (%s) not found in client balances.", contract.quote_asset)
                return None
        else:
            return None

        raw_trade_size = (balance * (balance_pct / 100)) / price
        final_trade_size = self.round_quantity(contract, raw_trade_size)

        if final_trade_size <= 0.0:
            logger.warning(
                "WARNING: Calculated raw trade size (%.8f) resulted in zero after rounding for %s. "
                "The position size might be smaller than the minimum lot size.",
                raw_trade_size, contract.symbol)
            return None

        logger.info("DEBUG_TRADE_SIZE: %s Balance = %s, Calculated Quantity = %.8f",
                    contract.quote_asset, balance, final_trade_size)

        return final_trade_size

    def start_ws_thread(self):
        if not hasattr(self, '_ws_lock'):
            self._ws_lock = threading.Lock()

        with self._ws_lock:
            if self._twm is None:
                logger.info("Binance WS: Initializing ThreadedWebsocketManager...")
                self._twm = ThreadedWebsocketManager(
                    api_key=self._public_key,
                    api_secret=self._secret_key,
                    testnet=self.testnet
                )

            try:
                if not self._twm.is_alive():
                    logger.info("Starting Binance TWM loop...")
                    self._twm.start()

                    for i in range(100):  # Increase from 20/50 to 100
                        if getattr(self._twm, '_bsm', None) is not None:
                            logger.info(f"Binance WS: Engine confirmed READY after {i * 0.1}s.")
                            break
                        time.sleep(0.1)
                    else:
                        logger.error("CRITICAL: TWM engine failed to initialize.")
                        return

                    try:
                        if self.futures:
                            self.listen_key = self._twm.start_futures_user_socket(callback=self._on_message)
                            logger.info("Binance Futures: User Data Stream active.")
                        else:
                            self.listen_key = self._twm.start_user_data_socket(callback=self._on_message)
                            logger.info("Binance Spot: User Data Stream active.")
                    except Exception as user_data_err:
                        logger.error(f"Failed to start User Data Stream: {user_data_err}")

                # 5. Final state update
                self.ws_connected = True

                # 6. Start Keep-Alive safely
                if not any(t.name == "KeepAliveThread" for t in threading.enumerate()):
                    keep_alive_thread = threading.Thread(
                        target=self._keep_alive_listen_key,
                        daemon=True,
                        name="KeepAliveThread"
                    )
                    keep_alive_thread.start()

            except Exception as e:
                logger.error(f"WS Startup Error: {e}")
                self.ws_connected = False

    def start_symbol_ticker_socket(self, callback, symbol):
        # 1. Log the subscription request using the correct global logger
        logger.info("Binance: preparing to subscribe to %s@bookticker", symbol.lower())

        # 2. Add the symbol to the existing bookticker subscription list
        if symbol not in self.ws_subscriptions["bookticker"]:
            self.ws_subscriptions["bookticker"].append(symbol)

        # 3. Subscription method
        self.subscribe_channel(contracts=[self.contracts[symbol]], channel="bookticker")

    def round_quantity(self, contract: Contract, quantity: float) -> float:

        # 1. Calculate how many full lots we can trade
        num_lots = int(quantity / contract.lot_size)

        # 2. Check if we can buy at least one lot
        if num_lots <= 0:
            logger.warning(
                "WARNING: Quantity %.8f is less than the minimum lot size %.8f for %s.",
                quantity, contract.lot_size, contract.symbol)
            return 0.0

        # 3. Calculate the final trade size and round it to 8 decimal places for safety/precision
        final_trade_size = round(num_lots * contract.lot_size, 8)

        return final_trade_size

    def close_connections(self):
        if self._twm:
            self._twm.stop()
            logger.info("Binance TWM stopped for %s", self.platform)

    def reconnect_ws(self):
        """Production recovery: Restores data flow for this specific client."""
        logger.warning(f"Restoring {self.platform} WebSocket connection...")
        try:
            # 1. Brutally kill the broken background manager
            if self._twm:
                try:
                    self._twm.stop()
                    # FIX: Wait up to 5 seconds for the old asyncio thread to safely die
                    self._twm.join(timeout=5)
                except Exception as e:
                    logger.error(f"Error stopping old TWM: {e}")

            # 2. Reset the state and CLEAR THE MEMORY CACHE
            time.sleep(2)  # Give the network 2 seconds to close the broken pipe
            self._twm = None
            self.ws_connected = False
            self._active_subscriptions.clear()

            # 3. Boot a fresh manager
            self.start_ws_thread()

            # 4. Thread-safe re-subscription
            with self.strategies_lock:
                for b_index, strat in self.strategies.items():
                    self.subscribe_channel([strat.contract], "aggtrade")
                    self.subscribe_channel([strat.contract], "bookticker")
                    self.subscribe_channel([strat.contract], "kline", interval=strat.tf)

            logger.info(f"{self.platform} recovery sequence complete.")
        except Exception as e:
            logger.error(f"Critical failure during {self.platform} reconnect: {e}")

    def check_connection(self):
        # 1. Immediate Recovery: If disconnected but strategies are running
        if not self.ws_connected:
            if len(self.strategies) > 0:
                logger.warning(f"WS disconnected on {self.platform} with active strategies. Reconnecting...")
                self.start_ws_thread()
            return

        # 2. Heartbeat Check & Auto-Sync
        # FIX: ONLY trigger the heartbeat alarm IF we actually have active strategies expecting data!
        if len(self.strategies) > 0:

            # Check Heartbeat
            time_since_last_update = time.time() - self.last_update_time
            if time_since_last_update > 60:
                logger.warning(f"Heartbeat lost on {self.platform} ({int(time_since_last_update)}s since last update).")
                self.reconnect_ws()
                return  # Exit here so we don't try to sync on a dead connection

            # Check Subscriptions
            missing_subscriptions = []
            for strategy in self.strategies.values():
                if strategy.contract.symbol not in self.prices:
                    missing_subscriptions.append(strategy.contract)

            if missing_subscriptions:
                logger.info(
                    f"Sync: Subscribing to {len(missing_subscriptions)} missing price streams for {self.platform}.")
                self.subscribe_channel(missing_subscriptions, "bookticker")

    def get_liquidation_price(self, symbol: str) -> float:
        if not self.futures:
            return 0.0

        data = {
            'symbol': symbol,
            'timestamp': int(time.time() * 1000) + getattr(self, '_time_offset', 0)
        }
        data['signature'] = self._generate_signature(data)  # Removed extra args

        endpoint = "/fapi/v2/positionRisk"
        positions = self._make_request("GET", endpoint, data)

        if positions:
            for pos in positions:
                if pos['symbol'] == symbol:
                    return float(pos.get('liquidationPrice', 0.0))
        return 0.0

    def get_open_positions(self) -> typing.List[typing.Dict]:
        if not self.futures:
            logger.warning("Position sync currently only supported for Futures.")
            return []

        data = {
            'timestamp': int(time.time() * 1000) + getattr(self, '_time_offset', 0),
            'recvWindow': 10000
        }
        # Signature only requires the data dictionary
        data['signature'] = self._generate_signature(data)
        endpoint = "/fapi/v2/positionRisk"

        try:
            raw_positions = self._make_request("GET", endpoint, data)
            open_positions = []

            if raw_positions is not None:
                for pos in raw_positions:
                    size = float(pos.get('positionAmt', 0.0))

                    if size != 0:
                        open_positions.append({
                            'symbol': pos['symbol'],
                            'side': 'long' if size > 0 else 'short',
                            'entry_price': float(pos['entryPrice']),
                            'size': abs(size),
                            'pnl': float(pos['unRealizedProfit']),
                            'liq_price': float(pos['liquidationPrice'])
                        })

                if len(open_positions) > 0:
                    logger.info(f"Sync: Found {len(open_positions)} active positions on Binance.")

            return open_positions

        except Exception as e:
            logger.error(f"Failed to fetch open positions from {endpoint}: {e}")
            return []

    def round_step_size(self, quantity, step_size):
        try:
            step_size = float(step_size)
            if step_size <= 0:
                logger.warning(f"Invalid step_size {step_size}. Returning quantity without rounding.")
                return quantity

            precision = int(round(-log(step_size, 10), 0))
            return floor(quantity * 10 ** precision) / 10 ** precision
        except Exception as e:
            logger.error(f"Error in round_step_size: {e}")
            return quantity

    def sync_open_positions(self):
        base_url = "https://fapi.binance.com" if self.futures else "https://api.binance.com"
        endpoint = "/fapi/v2/positionRisk" if self.futures else "/api/v3/account"
        url = base_url + endpoint
        timestamp = int(time.time() * 1000) + getattr(self, '_time_offset', 0)
        params = {"timestamp": timestamp}
        query_string = urlencode(params)

        mac = hmac.new(bytearray(self._secret_key.encode('utf-8')), digestmod=hashlib.sha256)
        mac.update(bytearray(query_string.encode('utf-8')))

        # 3. THE FIX: Assign the completed hash directly using mac.hexdigest()
        params["signature"] = mac.hexdigest()

        headers = {"X-MBX-APIKEY": self._public_key}

        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

            # Filter for only *active* positions
            if self.futures:
                active_positions = [p for p in data if float(p.get('positionAmt', 0)) != 0.0]
            else:
                active_positions = [b for b in data.get('balances', []) if
                                    float(b.get('free', 0)) > 0 or float(b.get('locked', 0)) > 0]

            logger.info(f"BinanceClient: Synced {len(active_positions)} active positions via REST.")
            return active_positions

        except Exception as e:
            logger.error(f"BinanceClient: REST Sync Failed - {e}")
            return None

    def _keep_alive_listen_key(self):
        """Pings the listenKey every 30 minutes to keep the User Data Stream alive."""
        while True:
            time.sleep(1800)  # Wait 30 minutes first
            if not self.ws_connected:
                continue

            endpoint = "/fapi/v1/listenKey" if self.futures else "/api/v3/listenKey"
            res = self._make_request("PUT", endpoint, dict())

            if res is not None:
                logger.info(f"Binance {self.platform} listenKey extended successfully.")
            else:
                logger.error(f"Critical: Binance {self.platform} listenKey extension FAILED. "
                             f"WebSocket data flow may stop.")

    def start_strategy(self, params: dict, b_index: int):
        symbol = params.get('contract') or params.get('contract_str')
        tf = params.get('timeframe')
        strat_type = params.get('strategy_type')

        if not all([symbol, tf, strat_type]):
            logger.error(f"BinanceClient: Missing required parameters. Symbol: {symbol}, TF: {tf}, Type: {strat_type}")
            return

        # 2. Contract Lookup
        contract = self.contracts.get(symbol)
        if not contract:
            logger.error(f"BinanceClient: Contract {symbol} not found in exchange info.")
            return

        strat_id = f"{symbol}_{tf}_{strat_type}"
        logger.info(f"BinanceClient: Fetching 1000 candles for {strat_id}...")
        try:
            # We fetch these manually so we can pass them to the strategy on birth
            historical_candles = self.get_historical_candles(contract, tf)
            if not historical_candles or len(historical_candles) < 100:
                logger.error(f"BinanceClient: Insufficient data for {strat_id}. Strategy aborted.")
                return
        except Exception as e:
            logger.error(f"BinanceClient: Failed to fetch candles for {strat_id}: {e}")
            return

        if 'extra_params' not in params:
            params['extra_params'] = {}
        params['extra_params']['row_index'] = b_index

        try:
            if strat_type == "Ichimoku":
                new_strat = IchimokuStrategy(self, contract, self.platform, tf,
                                             params['balance_pct'], params['take_profit'],
                                             params['stop_loss'], params['extra_params'],
                                             self.root.ui_update_queue.put)
            elif strat_type == "Technical":
                new_strat = TechnicalStrategy(self, contract, self.platform, tf,
                                              params['balance_pct'], params['take_profit'],
                                              params['stop_loss'], params['extra_params'],
                                              self.root.ui_update_queue.put)
            elif strat_type == "Breakout":
                new_strat = BreakoutStrategy(self, contract, self.platform, tf,
                                             params['balance_pct'], params['take_profit'],
                                             params['stop_loss'], params['extra_params'],
                                             self.root.ui_update_queue.put)
            else:
                logger.error(f"BinanceClient: Unknown strategy type: {strat_type}")
                return

            # Attach the candles we downloaded in Step 4
            new_strat.candles = historical_candles

            # --- ADD THIS BLOCK TO SYNC STRATEGY MEMORY WITH BINANCE ---
            # Make sure the strategy knows about any pre-existing positions so it tracks PnL
            # and, more importantly, prevents duplicate entries!
            if hasattr(self, 'open_positions') and self.open_positions:
                for pos in self.open_positions:
                    if pos['symbol'] == contract.symbol:
                        # Reconstruct the Trade object for the strategy's internal math
                        adopted_trade = Trade({
                            "time": int(time.time() * 1000),
                            "entry_price": pos['entry_price'],
                            "contract": contract,
                            "strategy": new_strat.strat_name,
                            "symbol": contract.symbol,
                            "side": pos['side'],
                            "status": "open",
                            "pnl": pos['pnl'],
                            "quantity": pos['size'],
                            "entry_id": int(time.time() * 1000)
                        })

                        # Inject it straight into the strategy
                        new_strat.trades.append(adopted_trade)
                        new_strat.ongoing_position = True
                        logger.info(f"BinanceClient: Synced live {pos['side'].upper()} trade into {strat_id} memory.")
            # -----------------------------------------------------------

        except Exception as e:
            logger.error(f"BinanceClient: Strategy instantiation error: {e}")
            return

        # 7. Thread-Safe Storage (INSIDE THE LOCK - very brief)
        with self.strategies_lock:
            self.strategies[strat_id] = new_strat

        # Trigger the UI update IMMEDIATELY
        self.root.ui_update_queue.put(("STRATEGY_ON", b_index))

        # 8. Start Real-time Data Subscriptions
        if self._twm:
            symbol_low = contract.symbol.lower()
            streams = [
                f"{symbol_low}@bookTicker",
                f"{symbol_low}@aggTrade",
                f"{symbol_low}@kline_{tf}"
            ]

            if self.futures:
                self._twm.start_futures_multiplex_socket(callback=self._on_message, streams=streams)
            else:
                self._twm.start_multiplex_socket(callback=self._on_message, streams=streams)

            self.ws_start_time = time.time()

            logger.info(f"Binance WS: Multiplex started for {contract.symbol} ({streams})")
        else:
            logger.error("Binance WS: ThreadedWebsocketManager not initialized.")

    def remove_strategy(self, symbol: str, tf: str, strat_type: str):
        strat_id = f"{symbol}_{tf}_{strat_type}"

        with self.strategies_lock:
            if strat_id in self.strategies:
                # 1. Stop the strategy's internal logic immediately
                self.strategies[strat_id].dead = True

                # 2. Delete from dictionary
                del self.strategies[strat_id]

                logger.info(f"BinanceClient: Strategy {strat_id} successfully removed.")
            else:
                logger.warning(f"BinanceClient: Attempted to remove non-existent strategy {strat_id}")

    def _sync_server_time(self):
        endpoint = "/fapi/v1/time" if self.futures else "/api/v3/time"

        try:
            response = requests.get(self._base_url + endpoint).json()
            server_time = response['serverTime']
            local_time = int(time.time() * 1000)
            self._time_offset = server_time - local_time
            logger.info(f"Binance Time Sync: Local clock is off by {self._time_offset}ms. Offset applied.")

        except Exception as e:
            logger.error(f"Failed to sync time with Binance: {e}")
            self._time_offset = 0

        # Automatically re-sync server time every 1 hour (3600s) in a background thread
        if not hasattr(self, '_time_sync_thread_started'):
            self._time_sync_thread_started = True

            def auto_resync_loop():
                while True:
                    time.sleep(3600)
                    self._sync_server_time()

            threading.Thread(target=auto_resync_loop, daemon=True, name="TimeSyncThread").start()

    def get_account_equity(self) -> float:
        """Fetches the total margin balance (Wallet + Floating PnL)"""
        if not self.futures:
            return 0.0

        data = {
            'timestamp': int(time.time() * 1000) + getattr(self, '_time_offset', 0),
            'recvWindow': 60000
        }
        data['signature'] = self._generate_signature(data)

        try:
            account_data = self._make_request("GET", "/fapi/v2/account", data)
            if account_data and 'totalMarginBalance' in account_data:
                return float(account_data['totalMarginBalance'])
        except Exception as e:
            logger.error(f"Failed to fetch account equity: {e}")

        return 0.0

    def round_price(self, contract: Contract, price: float) -> float:
        """Rounds price to match the exchange's required tick size."""
        try:
            num_ticks = int(price / contract.tick_size)
            rounded_price = round(num_ticks * contract.tick_size, 8)
            precision = str(contract.tick_size)[::-1].find('.')
            if precision < 0: precision = 0
            return round(rounded_price, precision)
        except Exception as e:
            logger.error(f"Error rounding price: {e}")
            return price