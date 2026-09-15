import datetime
import logging
import sqlite3
import threading
import typing
from typing import List, Dict
import json

logger = logging.getLogger()

class WorkspaceData:
    def __init__(self):
        self.conn = sqlite3.connect("database.db", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.lock = threading.Lock()

        with self.lock:
            self._create_tables()
            self.conn.commit()

    def _create_tables(self):
        self.cursor.execute("CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT, exchange TEXT)")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                strategy_type TEXT, 
                contract TEXT, 
                timeframe TEXT, 
                balance_pct REAL, 
                take_profit REAL, 
                stop_loss REAL, 
                extra_params TEXT, 
                is_active INTEGER DEFAULT 0,
                UNIQUE(strategy_type, contract, timeframe)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                time INTEGER, strategy TEXT, symbol TEXT, exchange TEXT,
                side TEXT, entry_price REAL, status TEXT, pnl REAL, 
                quantity REAL, entry_id TEXT PRIMARY KEY, exit_id TEXT, 
                exit_price REAL, tp_order_id TEXT, sl_order_id TEXT
            )
        """)

    def save_strategy_resilient(self, data: dict):
        query = """
            INSERT OR REPLACE INTO strategies (
                strategy_type, contract, timeframe, balance_pct, 
                take_profit, stop_loss, extra_params, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """

        # --- THE JSON SAFETY CHECK ---
        # We only want to dumps() if it is currently a Dictionary.
        # If it's already a string (from a previous DB fetch), leave it as is.
        extra_params = data.get('extra_params')
        if isinstance(extra_params, dict):
            extra_params_json = json.dumps(extra_params)
        else:
            # It's either already a JSON string or None
            extra_params_json = extra_params

        params = (
            data.get('strategy_type'),
            data.get('contract'),
            data.get('timeframe'),
            data.get('balance_pct'),
            data.get('take_profit'),
            data.get('stop_loss'),
            extra_params_json, # Use the checked version here
            data.get('is_active')
        )

        with self.lock:
            try:
                self.cursor.execute(query, params)
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Upsert Error: {e}")

    def update_strategy_status(self, contract: str, timeframe: str, active: int):
        with self.lock: # Added lock
            try:
                self.cursor.execute(
                    "UPDATE strategies SET is_active = ? WHERE contract = ? AND timeframe = ?",
                    (active, contract, timeframe)
                )
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Status Update Error: {e}")

    def save(self, table: str, data: typing.List[typing.Tuple]):
        # Attempt to acquire the lock with a 5-second timeout for write operations
        locked = self.lock.acquire(timeout=5)
        if not locked:
            logger.error(f"Database 'save' timed out for table {table}. Operation aborted to keep UI responsive.")
            return

        try:
            # 1. Fetch columns dynamically
            self.cursor.execute(f"SELECT * FROM {table} LIMIT 0")
            columns = [description[0] for description in self.cursor.description]

            # --- THE FIX: Clear the table first ---
            self.cursor.execute(f"DELETE FROM {table}")

            # 2. Construct and execute the SQL statement
            sql_statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"

            self.cursor.executemany(sql_statement, data)
            self.conn.commit()
            logger.info(f"Database: {table} table updated and synchronized.")

        except sqlite3.Error as e:
            # Rollback the transaction to restore the deleted data if the insert fails!
            self.conn.rollback()
            logger.error(f"Database Save Error for table {table}: {e}")
        finally:
            # Only release if we actually acquired it
            if locked:
                self.lock.release()

    def get(self, table: str) -> List[Dict]:
        # Attempt to acquire the lock with a 2-second timeout to prevent UI freeze
        locked = self.lock.acquire(timeout=2)
        if not locked:
            logger.warning(f"Database 'get' timed out for table {table}. UI protected from freeze.")
            return []

        try:
            self.cursor.execute(f"SELECT * FROM {table}")
            rows = self.cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Database Get Error for table {table}: {e}")
            return []
        finally:
            # Only release if we actually acquired it
            if locked:
                self.lock.release()

    def get_open_trades(self, strategy_name: str, symbol: str):
        with self.lock: # Added lock
            self.cursor.execute(
                "SELECT * FROM trades WHERE strategy = ? AND symbol = ? AND status = 'open'",
                (strategy_name, symbol)
            )
            return self.cursor.fetchall()

    def delete_strategy(self, contract: str, timeframe: str):
        with self.lock:
            try:
                self.cursor.execute(
                    "DELETE FROM strategies WHERE contract = ? AND timeframe = ?",
                    (contract, timeframe)
                )
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Database Deletion Error: {e}")

    def insert_trade(self, trade_data: dict):
        query = """
                    INSERT OR REPLACE INTO trades (
                        time, strategy, symbol, exchange, side, 
                        entry_price, status, pnl, quantity, entry_id,
                        tp_order_id, sl_order_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
        params = (
            trade_data.get('time'),
            trade_data.get('strategy', 'Unknown'),
            trade_data.get('symbol'),
            trade_data.get('exchange', 'Binance'),
            trade_data.get('side'),
            trade_data.get('entry_price'),
            trade_data.get('status', 'open'),
            trade_data.get('pnl', 0.0),
            trade_data.get('quantity'),
            trade_data.get('entry_id'),
            trade_data.get('tp_order_id', 'None'),
            trade_data.get('sl_order_id', 'None')
        )

        with self.lock:
            try:
                self.cursor.execute(query, params)
                self.conn.commit()
                logger.info(f"Database: Saved new trade {trade_data.get('symbol')} to trades table.")
            except sqlite3.Error as e:
                logger.error(f"Trade Insert Error: {e}")

    def update_trade_status(self, entry_id, status: str):
        with self.lock:
            try:
                self.cursor.execute("UPDATE trades SET status = ? WHERE entry_id = ?", (status, entry_id))
                self.conn.commit()
                logger.info(f"Database: Trade {entry_id} marked as {status}.")
            except sqlite3.Error as e:
                logger.error(f"Trade Update Error: {e}")
