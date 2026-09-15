import time

import dateutil.parser
import datetime
from typing import Optional


BITMEX_MULTIPLIER = 0.00000001  # Converts satoshi numbers to Bitcoin on Bitmex
BITMEX_TF_MINUTES = {"1m": 1, "5m": 5, "1h": 60, "1d": 1440}

TF_EQUIV_MS = {"1m": 60000, "5m": 300000, "15m": 900000, "30m": 1800000, "1h": 3600000, "4h": 14400000}


class Balance:
    def __init__(self, info, exchange):
        if exchange == "binance_futures":
            self.initial_margin = float(info['initialMargin'])
            self.maintenance_margin = float(info['maintMargin'])
            self.margin_balance = float(info['marginBalance'])
            self.wallet_balance = float(info['walletBalance'])
            self.unrealized_pnl = float(info['unrealizedProfit'])

        elif exchange == "binance_spot":
            self.free = float(info['free'])
            self.locked = float(info['locked'])

        elif exchange == "bitmex":
            self.initial_margin = info['initMargin'] * BITMEX_MULTIPLIER
            self.maintenance_margin = info['maintMargin'] * BITMEX_MULTIPLIER
            self.margin_balance = info['marginBalance'] * BITMEX_MULTIPLIER
            self.wallet_balance = info['walletBalance'] * BITMEX_MULTIPLIER
            self.unrealized_pnl = info['unrealisedPnl'] * BITMEX_MULTIPLIER


class Candle:
    def __init__(self, candle_info, timeframe, exchange):
        if exchange in ["binance_futures", "binance_spot"]:
            self.timestamp = candle_info[0]
            self.open = float(candle_info[1])
            self.high = float(candle_info[2])
            self.low = float(candle_info[3])
            self.close = float(candle_info[4])
            self.volume = float(candle_info[5])

        elif exchange == "bitmex":
            self.timestamp = dateutil.parser.isoparse(candle_info['timestamp'])
            self.timestamp = self.timestamp - datetime.timedelta(minutes=BITMEX_TF_MINUTES[timeframe])
            self.timestamp = int(self.timestamp.timestamp() * 1000)
            self.open = candle_info['open']
            self.high = candle_info['high']
            self.low = candle_info['low']
            self.close = candle_info['close']
            self.volume = candle_info['volume']

        elif exchange == "parse_trade":
            self.timestamp = candle_info['ts']
            self.open = candle_info['open']
            self.high = candle_info['high']
            self.low = candle_info['low']
            self.close = candle_info['close']
            self.volume = candle_info['volume']

        if timeframe in TF_EQUIV_MS:
            tf_ms = TF_EQUIV_MS[timeframe]
            self.close_time = self.timestamp + tf_ms
        else:
            self.close_time = None  # Fallback if timeframe is not supported

    def as_dict(self):
        """Returns the candle data as a dictionary for DataFrame creation."""
        return {
            'timestamp': self.timestamp,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume
        }


def tick_to_decimals(tick_size: float) -> int:
    tick_size_str = "{0:.8f}".format(tick_size)
    while tick_size_str[-1] == "0":
        tick_size_str = tick_size_str[:-1]

    split_tick = tick_size_str.split(".")

    if len(split_tick) > 1:
        return len(split_tick[1])
    else:
        return 0


class Contract:
    def __init__(self, contract_info, exchange):
        if exchange == "binance_futures":
            self.symbol = contract_info['symbol']
            self.base_asset = contract_info['baseAsset']
            self.quote_asset = contract_info['quoteAsset']
            self.price_decimals = contract_info['pricePrecision']
            self.quantity_decimals = contract_info['quantityPrecision']

            # Extract high-precision filters for trading
            for f in contract_info['filters']:
                if f['filterType'] == 'PRICE_FILTER':
                    self.tick_size = float(f['tickSize'])
                elif f['filterType'] == 'LOT_SIZE':
                    self.step_size = float(f['stepSize'])

        elif exchange == "binance_spot":
            self.symbol = contract_info['symbol']
            self.base_asset = contract_info['baseAsset']
            self.quote_asset = contract_info['quoteAsset']

            # The actual lot size and tick size on Binance spot can be found in the 'filters' fields
            # contract_info['filters'] is a list
            for b_filter in contract_info['filters']:
                if b_filter['filterType'] == 'PRICE_FILTER':
                    self.tick_size = float(b_filter['tickSize'])
                    self.price_decimals = tick_to_decimals(float(b_filter['tickSize']))
                if b_filter['filterType'] == 'LOT_SIZE':
                    self.lot_size = float(b_filter['stepSize'])
                    self.quantity_decimals = tick_to_decimals(float(b_filter['stepSize']))

        elif exchange == "bitmex":
            self.symbol = contract_info['symbol']
            self.base_asset = contract_info['rootSymbol']
            self.quote_asset = contract_info['quoteCurrency']
            self.price_decimals = tick_to_decimals(contract_info['tickSize'])
            self.quantity_decimals = tick_to_decimals(contract_info['lotSize'])
            self.tick_size = contract_info['tickSize']
            self.lot_size = contract_info['lotSize']

            self.quanto = contract_info['isQuanto']
            self.inverse = contract_info['isInverse']

            self.multiplier = contract_info['multiplier'] * BITMEX_MULTIPLIER

            if self.inverse:
                self.multiplier *= -1

        self.exchange = exchange


class OrderStatus:
    def __init__(self, order_info, exchange, from_ws=False):
        if exchange == "binance_futures":
            if from_ws:
                # Mapping WebSocket 'o' dictionary keys
                self.order_id = order_info['i']
                self.status = order_info['X'].lower()
                self.avg_price = float(order_info['ap'])
                self.executed_qty = float(order_info['l'])
            else:
                self.order_id = order_info.get('orderId') or order_info.get('algoId')
                self.status = order_info.get('status', order_info.get('algoStatus', 'NEW')).lower()
                self.avg_price = float(order_info.get('avgPrice', 0))
                self.executed_qty = float(order_info.get('executedQty', 0))

        elif exchange == "binance_spot":
            if from_ws:
                # Spot WS 'executionReport' keys
                self.order_id = order_info['i']
                self.status = order_info['X'].lower()
                self.avg_price = float(order_info['L'])  # 'L' is price of last fill in Spot
                self.executed_qty = float(order_info['l'])  # 'l' is qty of last fill
            else:
                self.order_id = order_info['orderId']
                self.status = order_info['status'].lower()
                self.avg_price = float(order_info.get('avgPrice', 0))
                self.executed_qty = float(order_info.get('executedQty', 0))

        elif exchange == "bitmex":
            self.order_id = order_info['orderID']
            self.status = order_info['ordStatus'].lower()
            self.avg_price = order_info['avgPx']
            self.executed_qty = order_info['cumQty']


class Trade:
    def __init__(self, trade_info, contract_obj=None):
        self.symbol: str = trade_info['symbol']
        self.side = trade_info.get('side', 'long').lower()
        self.entry_price: float = float(trade_info['entry_price'])

        # 1. Quantity Mapping
        self.quantity: float = float(trade_info.get('size', trade_info.get('qty', 0)))

        # 2. UI Identifier
        self.time: int = int(trade_info.get('time', int(time.time() * 1000)))

        # 3. Strategy & Status Tracking
        self.strategy: str = trade_info.get('strategy', "Manual")
        self.status: str = trade_info.get('status', "open")
        self.pnl: float = float(trade_info.get('pnl', 0.0))

        self.contract = contract_obj
        self.multiplier = 1.0

        # BITMEX_MULTIPLIER = 0.00000001
        if contract_obj and "bitmex" in getattr(contract_obj, 'exchange', '').lower():
            self.multiplier = 0.00000001

        # --- THE FIX: Robust Multiplier Logic ---
        if self.contract:
            self.multiplier = getattr(self.contract, 'multiplier', 1.0) or 1.0
            # Check if the contract belongs to Bitmex
            exchange = getattr(self.contract, 'exchange', "").lower()
        else:
            exchange = trade_info.get('exchange', "").lower()

        if "bitmex" in exchange and self.multiplier == 1.0:
            self.multiplier = BITMEX_MULTIPLIER

        # 4. ID Tracking
        self.entry_id = trade_info.get('entry_id', 0)
        self.exit_id = trade_info.get('exit_id', 0)
        self.exit_price: Optional[float] = trade_info.get('exit_price')

    def as_dict(self):
        """
        Serializes the trade object into a dictionary.
        This is required for sending data to the UI via the Queue.
        """
        return {
            'time': self.time,
            'symbol': self.symbol,
            'strategy': self.strategy,
            'side': self.side,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'status': self.status,
            'pnl': self.pnl,
            'quantity': self.quantity,
            'entry_id': self.entry_id,
            'exit_id': self.exit_id
        }








