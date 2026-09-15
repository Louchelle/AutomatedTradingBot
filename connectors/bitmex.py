import logging
import requests
import time
import typing
import collections

from urllib.parse import urlencode

import hmac
import hashlib

import websocket
import json

import dateutil.parser

import threading

from models.models import *

from strategies import TechnicalStrategy, BreakoutStrategy

logger = logging.getLogger()


class BitmexClient:
    def __init__(self, public_key: str, secret_key: str, testnet: bool):
        """
                See comments in the Binance connector.
                :param public_key:
                :param secret_key:
                :param testnet:
                """

        self.futures = True
        self.platform = "bitmex"

        if testnet:
            self._base_url = "https://testnet.bitmex.com"
            self._wss_url = "wss://testnet.bitmex.com/realtime"
        else:
            self._base_url = "https://www.bitmex.com"
            self._wss_url = "wss://www.bitmex.com/realtime"

        self._public_key = public_key
        self._secret_key = secret_key

        # REMOVED: self._time_offset_seconds and self._server_time_s

        self.ws: websocket.WebSocketApp
        self.reconnect = True

        # These methods now rely purely on local time for signing
        self.contracts = self.get_contracts()
        self.balances = self.get_balances()

        self.prices = dict()
        self.strategies: typing.Dict[int, typing.Union[TechnicalStrategy, BreakoutStrategy]] = dict()

        self.logs = []

        self.strategies_lock = threading.Lock()

        self.last_update_time = time.time()

        t = threading.Thread(target=self._start_ws)
        t.start()

        logger.info("Bitmex Client successfully initialized")


    def _add_log(self, msg: str):
        logger.info("%s", msg)
        self.logs.append({"log": msg, "displayed": False})

    def _generate_signature(self, method: str, endpoint: str, expires: str, data: typing.Dict) -> str:

        path = endpoint
        data_to_sign = ''

        if method == "GET":
            if len(data) > 0:
                path = endpoint + "?" + urlencode(data)
                data_to_sign = ''

        elif method == "POST" or method == "DELETE":
            if len(data) > 0:
                # CRITICAL FIX (Kept): Add sort_keys=True to ensure canonical JSON
                data_to_sign = json.dumps(data, separators=(',', ':'), sort_keys=True)
            else:
                # IMPORTANT: For an empty body, BitMEX requires an empty string for the signature, NOT '{}'
                data_to_sign = ''

        message = method + path + expires + data_to_sign

        return hmac.new(self._secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()

    def _make_request(self, method: str, endpoint: str, data: typing.Dict):

        url = self._base_url + endpoint
        # Standard Binance Header
        headers = {'X-MBX-APIKEY': self._public_key}
        # Timeout: (Connect timeout, Read timeout)
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
                logger.error(f"Error: Unsupported HTTP method: {method}")
                return None
        except requests.exceptions.Timeout:
            logger.error(f"Binance API Timeout: {method} {endpoint} took too long.")
            return None
        except Exception as e:
            logger.error(f"Connection error while making {method} request to {endpoint}: {e}")
            return None

        if response.status_code == 200:
            return response.json()
        else:
            # Handle non-200 codes as you were doing...
            logger.error(f"Error {response.status_code} on {endpoint}: {response.text}")
            return None

    def _make_public_request(self, endpoint: str, data: typing.Dict):
        url = self._base_url + endpoint
        try:
            response = requests.get(url, params=data)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error("Error while making public GET request to %s: %s (error code %s)",
                             endpoint, response.text, response.status_code)
                return None
        except Exception as e:
            logger.error("Connection error while making public GET request to %s: %s", endpoint, e)
            return None

    def get_contracts(self) -> typing.Dict[str, Contract]:

        # FIXED: Calling the _make_public_request with 2 data arguments (endpoint, data)
        instruments = self._make_public_request("/api/v1/instrument/active", dict())

        contracts = dict()

        if instruments is not None:
            for s in instruments:
                contracts[s['symbol']] = Contract(s, "bitmex")

        return collections.OrderedDict(sorted(contracts.items()))  # Sort keys of the dictionary alphabetically

    def get_balances(self) -> typing.Dict[str, Balance]:
        data = dict()
        data['currency'] = "all"

        margin_data = self._make_request("GET", "/api/v1/user/margin", data)

        balances = dict()

        if margin_data is not None:
            # SAFETY CHECK: Ensure we actually got a list back
            if isinstance(margin_data, list):
                for a in margin_data:
                    balances[a['currency']] = Balance(a, "bitmex")
            else:
                logger.error("Bitmex balance response was not a list: %s", margin_data)

        return balances  # Returns empty dict instead of None if it fails

    def get_historical_candles(self, contract: Contract, timeframe: str) -> typing.List[Candle]:
        # REMOVED: self.fetch_server_time_offset()
        data = dict()

        data['symbol'] = contract.symbol
        data['partial'] = True
        data['binSize'] = timeframe
        data['count'] = 500
        data['reverse'] = True

        raw_candles = self._make_request("GET", "/api/v1/trade/bucketed", data)

        candles = []

        if raw_candles is not None:
            for c in reversed(raw_candles):
                if c['open'] is None or c['close'] is None:  # Some candles returned by Bitmex miss data
                    continue
                candles.append(Candle(c, timeframe, "bitmex"))

        return candles

    def place_order(self, contract: Contract, order_type: str, quantity: int, side: str, price=None,
                    tif=None) -> OrderStatus:

        data = dict()
        data['symbol'] = contract.symbol
        data['side'] = side.capitalize()
        data['orderQty'] = int(quantity)
        data['ordType'] = order_type.capitalize()

        if price is not None:
            data['price'] = round(round(price / contract.tick_size) * contract.tick_size, 8)

        if tif is not None:
            data['timeInForce'] = tif

        # Execute the request
        response = self._make_request("POST", "/api/v1/order", data)

        # CRITICAL SAFETY CHECK
        if response is not None:
            if isinstance(response, dict) and 'orderID' in response:
                # Only create OrderStatus if we have an actual orderID
                return OrderStatus(response, "bitmex")
            else:
                logger.error("Bitmex order placement failed or malformed response: %s", response)

        return None

    def cancel_order(self, order_id: str) -> OrderStatus:
        data = dict()
        data['orderID'] = order_id

        order_status = self._make_request("DELETE", "/api/v1/order", data)

        if order_status is not None:
            order_status = OrderStatus(order_status[0], "bitmex")

        return order_status

    def get_order_status(self, contract: Contract, order_id: str) -> OrderStatus:

        data = dict()
        data['symbol'] = contract.symbol
        data['reverse'] = True

        order_status = self._make_request("GET", "/api/v1/order", data)

        if order_status is not None:
            for order in order_status:
                if order['orderID'] == order_id:
                    return OrderStatus(order, "bitmex")

    def _start_ws(self):
        self.ws = websocket.WebSocketApp(self._wss_url, on_open=self._on_open, on_close=self._on_close,
                                         on_error=self._on_error, on_message=self._on_message)

        ping_interval = 30,
        ping_timeout = 10

        while True:
            try:
                if self.reconnect:
                    self.ws.run_forever(ping_interval=30, ping_timeout=10)
                else:
                    break
            except Exception as e:
                logger.error("Bitmex error in run_forever() method: %s", e)
            time.sleep(2)

    def _on_open(self, ws):
        logger.info("Bitmex connection opened")

        # Removed: self.fetch_server_time_offset()

        self.subscribe_channel("instrument")
        self.subscribe_channel("trade")

    def _on_close(self, ws, close_code, reason):
        # The method MUST accept these three arguments: ws, close_code, and reason.

        # Log the detailed reason for connection loss
        logger.error("Bitmex connection error: Connection to remote host was lost. Code: %s, Reason: %s", close_code,
                     reason)

        # Keep your original log line, now with an ERROR severity to stand out
        logger.warning("Bitmex Websocket connection closed")

    def _on_error(self, ws, msg: str):
        logger.error("Bitmex connection error: %s", msg)

    def _on_message(self, ws, msg: str):
        self.last_update_time = time.time()

        data = json.loads(msg)

        if "table" in data:
            if data['table'] == "instrument":

                for d in data['data']:

                    symbol = d['symbol']

                    if symbol not in self.prices:
                        self.prices[symbol] = {'bid': None, 'ask': None}

                    if 'bidPrice' in d:
                        self.prices[symbol]['bid'] = d['bidPrice']
                    if 'askPrice' in d:
                        self.prices[symbol]['ask'] = d['askPrice']

                    # --- THREAD-SAFE PNL CALCULATION ---
                    with self.strategies_lock:
                        # We iterate safely under the lock
                        for b_index, strat in self.strategies.items():
                            if strat.contract.symbol == symbol:
                                for trade in strat.trades:
                                    if trade.status == "open" and trade.entry_price is not None:
                                        # (Your existing PnL math here...)
                                        if trade.side == "long":
                                            price = self.prices[symbol]['bid']
                                        else:
                                            price = self.prices[symbol]['ask']

                                        multiplier = trade.contract.multiplier
                                        if trade.contract.inverse:
                                            if trade.side == "long":
                                                trade.pnl = (
                                                                        1 / trade.entry_price - 1 / price) * multiplier * trade.quantity
                                            elif trade.side == "short":
                                                trade.pnl = (
                                                                        1 / price - 1 / trade.entry_price) * multiplier * trade.quantity
                                        else:
                                            if trade.side == "long":
                                                trade.pnl = (
                                                                        price - trade.entry_price) * multiplier * trade.quantity
                                            elif trade.side == "short":
                                                trade.pnl = (
                                                                        trade.entry_price - price) * multiplier * trade.quantity

            if data['table'] == "trade":
                for d in data['data']:
                    symbol = d['symbol']
                    ts = int(dateutil.parser.isoparse(d['timestamp']).timestamp() * 1000)

                    # --- THREAD-SAFE TRADE PROCESSING ---
                    with self.strategies_lock:
                        for key, strat in self.strategies.items():
                            if strat.contract.symbol == symbol:
                                res = strat.parse_trades(float(d['price']), float(d['size']), ts)
                                strat.check_trade(res)

    def subscribe_channel(self, topic: str):
        data = dict()
        data['op'] = "subscribe"
        data['args'] = []
        data['args'].append(topic)

        try:
            self.ws.send(json.dumps(data))
        except Exception as e:
            logger.error("Websocket error while subscribing to %s: %s", topic, e)

    def get_trade_size(self, contract: Contract, price: float, balance_pct: float):

        """
        Compute the trade size for the strategy module based on the percentage of the balance to use
        that was defined in the strategy component and the type of contract.
        :param contract:
        :param price: Used to convert the amount to invest into an amount to buy/sell
        :param balance_pct:
        :return:
        """

        balance = self.get_balances()
        if balance is not None:
            if 'XBt' in balance:
                balance = balance['XBt'].wallet_balance
            else:
                return None
        else:
            return None

        xbt_size = balance * balance_pct / 100

        # The trade size calculation depends on the type of contract
        # https://www.bitmex.com/app/perpetualContractsGuide

        if contract.inverse:
            raw_contracts_number = xbt_size / (contract.multiplier / price)
        elif contract.quanto:
            raw_contracts_number = xbt_size / (contract.multiplier * price)
        else:
            raw_contracts_number = xbt_size / (contract.multiplier * price)

        final_contracts_number = self.round_quantity(contract, raw_contracts_number)

        if final_contracts_number <= 0.0:
            logger.warning(
                "WARNING: Calculated raw trade size (%.2f contracts) resulted in zero after rounding for %s. Cannot place trade.",
                raw_contracts_number, contract.symbol)
            return None

        logger.info("Bitmex current XBT balance = %s, contracts number = %.0f", balance, final_contracts_number)

        # Return the final rounded contracts number
        return final_contracts_number

    def round_quantity(self, contract: Contract, quantity: float) -> float:
        """
        Rounds the calculated quantity down to the nearest whole contract (integer)
        for BitMEX, as quantities must be integers.
        :param contract: The contract being traded.
        :param quantity: The raw calculated float quantity.
        :return: The rounded quantity (as a float for consistency). Returns 0.0 if too small.
        """

        # BitMEX quantity must be an integer (number of contracts). We round down.
        rounded_quantity = int(quantity)

        if rounded_quantity <= 0:
            logger.warning(
                "CRITICAL: Quantity %f is less than 1 contract for %s. Trade aborted.",
        quantity, contract.symbol)
            return 0.0

        return float(rounded_quantity)

    def check_connection(self):
        """Monitor the health of the Bitmex WebSocket."""
        # Only check if we actually have active strategies
        if len(self.strategies) > 0:
            time_since_last_update = time.time() - self.last_update_time

            if time_since_last_update > 30:
                logger.warning(f"Bitmex heartbeat lost ({int(time_since_last_update)}s). Resetting connection...")
                try:
                    self.ws.close()  # Forcing close triggers the reconnect loop in _start_ws
                except Exception as e:
                    logger.error(f"Error closing Bitmex WS for reconnect: {e}")

                # Reset timer so we don't spam close calls
                self.last_update_time = time.time()
