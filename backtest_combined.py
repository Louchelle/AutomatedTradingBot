import pandas as pd
import numpy as np
from backtesting import Backtest, Strategy
from backtesting.lib import crossover


# --------------------------------------------------------------------------
# 1. DATA ACQUISITION
# --------------------------------------------------------------------------

def get_crypto_data(symbol='BTCUSDT', start_date='2025-06-01', end_date='2025-12-01', interval='1h'):
    """
    Loads OHLCV data from a local CSV file, handling Unix timestamp index
    and renaming columns for backtesting.py compatibility.
    """
    try:
        file_path = f"{symbol}_{interval}_history.csv"
        print(f"Loading data from local file: {file_path}")

        # 1. LOAD DATA: Use the header (row 0) and set 'open_time' as the index.
        data = pd.read_csv(
            file_path,
            header=0,
            index_col='open_time',
        )

        # 2. CRITICAL FIX: Explicitly convert the Unix timestamp index to datetime.
        data.index = pd.to_datetime(data.index, unit='ms', errors='coerce')

        # 3. CRITICAL FIX: Rename the lowercase columns to the uppercase required by Backtesting.py
        data.rename(columns={
            'open': 'Open',
            'high': 'High',
            'low': 'Low',
            'close': 'Close',
            'volume': 'Volume'
        }, inplace=True)

        # 4. Select required columns and remove any rows with missing data
        data = data[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()

        # 5. Filter the loaded data by the extended date range (6 months)
        data = data.loc[start_date:end_date]
        data.sort_index(inplace=True)

        return data

    except FileNotFoundError:
        print(f"Error: CSV file not found at {file_path}. Please check the filename.")
        return pd.DataFrame()
    except Exception as e:
        print(f"Error processing CSV data: {e}")
        return pd.DataFrame()


# --------------------------------------------------------------------------
# 2. STRATEGY HELPER FUNCTIONS
# --------------------------------------------------------------------------

def calculate_rsi(series, length):
    """Calculates Relative Strength Index (RSI)."""
    delta = series.diff()
    up, down = delta.copy(), delta.copy().abs()
    up[up < 0] = 0
    down[down < 0] = 0

    avg_gain = up.ewm(com=(length - 1), min_periods=length).mean()
    avg_loss = down.ewm(com=(length - 1), min_periods=length).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_atr(df, length):
    """Calculates Average True Range (ATR)."""
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift(1)).abs()
    low_close = (df['Low'] - df['Close'].shift(1)).abs()

    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.ewm(span=length, min_periods=length).mean()
    return atr


def ichimoku_mid_price(high: np.ndarray, low: np.ndarray, period: int):
    """Calculates the mid-price (Highest High + Lowest Low) / 2 over a period."""
    high_series = pd.Series(high)
    low_series = pd.Series(low)

    highest_high = high_series.rolling(period).max()
    lowest_low = low_series.rolling(period).min()

    return (highest_high + lowest_low) / 2


# --------------------------------------------------------------------------
# 3. STRATEGY DEFINITIONS
# --------------------------------------------------------------------------

class TechnicalStrategyBacktest(Strategy):
    # Default parameters - to be optimized later
    ema_fast = 12
    ema_slow = 26
    ema_signal = 9
    rsi_length = 14
    atr_length = 14
    atr_multiplier = 2.0
    take_profit = 10.0
    stop_loss = 5.0

    def init(self):
        # Helper lambda to calculate EMA
        def ema_calc(data_array, span):
            return pd.Series(data_array).ewm(span=span).mean()

        # 1. MACD
        fast_ma = self.I(ema_calc, self.data.Close, self.ema_fast)
        slow_ma = self.I(ema_calc, self.data.Close, self.ema_slow)
        self.macd_line = fast_ma - slow_ma
        self.macd_signal = self.I(ema_calc, self.macd_line, self.ema_signal)

        # 2. RSI
        self.rsi = self.I(calculate_rsi, pd.Series(self.data.Close), self.rsi_length, name='RSI', overlay=False)

        # 3. ATR
        data_df = pd.DataFrame({
            'Open': self.data.Open, 'High': self.data.High,
            'Low': self.data.Low, 'Close': self.data.Close
        })
        self.atr = self.I(calculate_atr, data_df, self.atr_length, name='ATR')

        self.close = self.data.Close

    def next(self):
        macd_line = self.macd_line[-1]
        macd_signal = self.macd_signal[-1]
        rsi = self.rsi[-1]
        current_price = self.close[-1]

        signal = 0
        if rsi < 30 and macd_line > macd_signal:
            signal = 1
        elif rsi > 70 and macd_line < macd_signal:
            signal = -1

        sl_price = None
        atr_value = self.atr[-1]
        if atr_value > 0 and self.atr_multiplier > 0:
            sl_distance = atr_value * self.atr_multiplier

            if self.position.is_long:
                sl_price = current_price - sl_distance
            elif self.position.is_short:
                sl_price = current_price + sl_distance

        if signal == 1 and not self.position:
            self.buy(size=0.99, limit=current_price, sl=sl_price,
                     tp=current_price * (1 + self.take_profit / 100) if self.take_profit else None)
        elif signal == -1 and not self.position:
            self.sell(size=0.99, limit=current_price, sl=sl_price,
                      tp=current_price * (1 - self.take_profit / 100) if self.take_profit else None)


class BreakoutStrategyBacktest(Strategy):
    # Default parameters - to be optimized later
    min_volume = 1000
    take_profit = 10.0
    stop_loss = 5.0

    def init(self):
        self.close = self.data.Close
        self.high = self.data.High
        self.low = self.data.Low
        self.volume = self.data.Volume

    def next(self):
        if len(self.data) < 2: return

        current_close = self.close[-1]
        prev_high = self.high[-2]
        prev_low = self.low[-2]
        current_volume = self.volume[-1]

        signal = 0
        if current_close > prev_high and current_volume > self.min_volume:
            signal = 1
        elif current_close < prev_low and current_volume > self.min_volume:
            signal = -1

        if signal == 1 and not self.position:
            entry_price = current_close
            long_sl = entry_price * (1 - self.stop_loss / 100) if self.stop_loss else None
            long_tp = entry_price * (1 + self.take_profit / 100) if self.take_profit else None
            self.buy(
                size=0.99,
                limit=entry_price,
                sl=long_sl,
                tp=long_tp
            )
        elif signal == -1 and not self.position:
            entry_price = current_close
            short_sl = entry_price * (1 + self.stop_loss / 100) if self.stop_loss else None
            short_tp = entry_price * (1 - self.take_profit / 100) if self.take_profit else None
            self.sell(
                size=0.99,
                limit=entry_price,
                sl=short_sl,
                tp=short_tp
            )


class IchimokuStrategyBacktest(Strategy):
    # --- OPTIMAL PARAMETERS FROM OPTIMIZATION ---
    tenkan = 11  # Optimized
    kijun = 35  # Optimized
    senkou_span_b = 52  # Not used in strategy logic, but standard
    take_profit = 10.0
    stop_loss = 5.0

    def init(self):
        # Use the helper function defined above
        self.tenkan_line = self.I(ichimoku_mid_price, self.data.High, self.data.Low, self.tenkan, name='Tenkan')
        self.kijun_line = self.I(ichimoku_mid_price, self.data.High, self.data.Low, self.kijun, name='Kijun')

        self.close = self.data.Close

    def next(self):
        # We need enough data for calculation
        if len(self.data) < max(self.tenkan, self.kijun) + 3: return

        tenkan_prev = self.tenkan_line[-2]
        kijun_prev = self.kijun_line[-2]

        tenkan_prev_prev = self.tenkan_line[-3]
        kijun_prev_prev = self.kijun_line[-3]

        current_price = self.close[-1]

        signal = 0
        # Check for Tenkan crossing Kijun on the previous bar
        if (tenkan_prev_prev < kijun_prev_prev) and (tenkan_prev > kijun_prev):
            signal = 1  # Long Signal
        elif (tenkan_prev_prev > kijun_prev_prev) and (tenkan_prev < kijun_prev):
            signal = -1  # Short Signal

        if signal == 1 and not self.position:
            self.buy(
                size=0.99,
                limit=current_price,
                sl=current_price * (1 - self.stop_loss / 100) if self.stop_loss else None,
                tp=current_price * (1 + self.take_profit / 100) if self.take_profit else None
            )
        elif signal == -1 and not self.position:
            self.sell(
                size=0.99,
                limit=current_price,
                sl=current_price * (1 + self.stop_loss / 100) if self.stop_loss else None,
                tp=current_price * (1 - self.take_profit / 100) if self.take_profit else None
            )


# --------------------------------------------------------------------------
# 4. BACKTEST EXECUTION FUNCTIONS
# --------------------------------------------------------------------------

def run_optimization():
    # Load 6 months data (using the same successful time frame)
    data = get_crypto_data(symbol='BTCUSDT', start_date='2025-06-01', end_date='2025-12-01', interval='1h')

    if data.empty:
        print("Could not run optimization due to data fetch failure.")
        return

    print(f"\nDIAGNOSTIC PASSED: Data loaded successfully with {len(data)} rows for 6 months.")
    print("Starting Ichimoku Optimization...")
    print("------------------------------------------")

    # ... [Optimization logic is omitted in the final file when running single backtest] ...
    # This function is kept here only for reference or if you decide to run it later.

    bt = Backtest(
        data,
        IchimokuStrategyBacktest,
        cash=500_000,
        commission=0.001,
        exclusive_orders=True
    )

    stats, heatmap = bt.optimize(
        tenkan=range(5, 15, 2),
        kijun=range(20, 40, 5),
        maximize='Return [%]',
        return_heatmap=True,
        stop_loss=5.0,
        take_profit=10.0,
    )

    print("\n" + "=" * 50)
    print("OPTIMIZATION COMPLETE: BEST PARAMETERS")
    print("-" * 50)
    print(stats)

    bt.plot(filename='IchimokuOptimizationPlot.html')
    print("\nPlot for the best Ichimoku strategy generated as IchimokuOptimizationPlot.html.")
    print("=" * 50)


def run_single_backtest():
    """Runs a single backtest for the Ichimoku strategy using optimized parameters."""
    data = get_crypto_data(symbol='BTCUSDT', start_date='2025-06-01', end_date='2025-12-01', interval='1h')

    if data.empty:
        print("Could not run backtest due to data fetch failure.")
        return

    print(f"\nDIAGNOSTIC PASSED: Data loaded successfully with {len(data)} rows for 6 months.")
    print("Starting single backtest for Optimized Ichimoku Strategy (Tenkan 11, Kijun 35)...")
    print("------------------------------------------")

    bt = Backtest(
        data,
        IchimokuStrategyBacktest,
        cash=500_000,
        commission=0.001,
        exclusive_orders=True,
        finalize_trades=True
    )

    stats = bt.run()

    print("\n" + "=" * 50)
    print("OPTIMIZED ICHIMOKU STRATEGY FINAL STATS")
    print("-" * 50)
    print(stats)

    # Plot the results
    bt.plot(filename='IchimokuSingleBacktestPlot.html')
    print("\nPlot for the optimized Ichimoku strategy generated as IchimokuSingleBacktestPlot.html.")
    print("=" * 50)


# --------------------------------------------------------------------------
# 5. MAIN EXECUTION
# --------------------------------------------------------------------------

if __name__ == '__main__':
    # Running the single backtest of the optimized Ichimoku strategy (11, 35)
    run_single_backtest()