import pandas as pd
import numpy as np
import warnings
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

warnings.filterwarnings("ignore")


# --------------------------------------------------------------------------
# 1. DATA LOADER (SCALING FIX)
# --------------------------------------------------------------------------
def get_crypto_data(symbol='BTCUSDT'):
    file_path = f"{symbol}_1h_history.csv"
    data = pd.read_csv(file_path, header=0, index_col='open_time')
    data.index = pd.to_datetime(data.index, unit='ms')
    data.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'},
                inplace=True)
    data = data[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    data[['Open', 'High', 'Low', 'Close']] = data[['Open', 'High', 'Low', 'Close']] / 1000
    return data


# --------------------------------------------------------------------------
# 2. STRATEGIES
# --------------------------------------------------------------------------
class BreakoutStrategy(Strategy):
    window = 30

    def init(self):
        self.high_roll = self.I(lambda x: pd.Series(x).rolling(self.window).max(), self.data.High)
        self.low_roll = self.I(lambda x: pd.Series(x).rolling(self.window).min(), self.data.Low)

    def next(self):
        if self.data.Close[-1] > self.high_roll[-2]:
            self.buy(size=0.8)
        elif self.data.Close[-1] < self.low_roll[-2]:
            self.position.close()


class TechnicalStrategy(Strategy):
    rsi_window = 14

    def init(self):
        # Simplified RSI for stability
        delta = pd.Series(self.data.Close).diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_window).mean()
        rs = gain / (loss + 1e-10)
        self.rsi = self.I(lambda: 100 - (100 / (1 + rs)))

    def next(self):
        if self.rsi < 30:
            self.buy(size=0.8)
        elif self.rsi > 70:
            self.position.close()


# --------------------------------------------------------------------------
# 3. MANUAL STABLE RUNNER (NO CRASH)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    df = get_crypto_data()

    # We will test the most likely winners based on your previous logs
    test_scenarios = [
        {"name": "Breakout (Win=20)", "class": BreakoutStrategy, "p": {"window": 20}},
        {"name": "Breakout (Win=30)", "class": BreakoutStrategy, "p": {"window": 30}},
        {"name": "Breakout (Win=40)", "class": BreakoutStrategy, "p": {"window": 40}},
        {"name": "Technical (RSI=14)", "class": TechnicalStrategy, "p": {"rsi_window": 14}},
    ]

    print(f"{'STRATEGY':<25} | {'RETURN':<10} | {'WIN RATE':<10} | {'TRADES'}")
    print("-" * 60)

    for scenario in test_scenarios:
        # Create the backtest
        bt = Backtest(df, scenario['class'], cash=10000, commission=.001)

        # Run manually with specific parameters (bt.run, NOT bt.optimize)
        stats = bt.run(**scenario['p'])

        print(
            f"{scenario['name']:<25} | {stats['Return [%]']:>8.2f}% | {stats['Win Rate [%]']:>8.2f}% | {int(stats['# Trades'])}")