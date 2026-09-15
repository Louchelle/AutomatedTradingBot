import sys
import logging
import queue
import os
from dotenv import load_dotenv

from connectors.binance_client import BinanceClient
from interface.root_component import Root
from models.worker import Worker

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# --- Logger Configuration ---
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s INFO :: %(message)s',
    handlers=[
        logging.FileHandler("info.log", mode='a', encoding='utf-8'),
        logging.StreamHandler() # Keeps printing to your PyCharm console
    ]
)

# ENABLE DIAGNOSTIC NETWORK LOGGING
logging.getLogger("websockets").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("binance").setLevel(logging.CRITICAL)
logging.getLogger("binance.websockets").setLevel(logging.CRITICAL)

if __name__ == '__main__':
    load_dotenv()

    # 1. Communication Queues
    ui_update_queue = queue.Queue()
    order_status_queue = queue.Queue()
    pnl_update_queue = queue.Queue()

    # 2. Initialize Binance Futures Client
    # Note: We pass the pnl_update_queue directly so it can talk to the UI later
    binance_futures_client = BinanceClient(
        public_key=os.getenv("BINANCE_FUTURES_PUBLIC_KEY"),
        secret_key=os.getenv("BINANCE_FUTURES_SECRET_KEY"),
        testnet=True,
        futures=True
    )

    # 3. Initialize Worker
    # The worker will handle the .connect() and contract fetching in its own thread
    worker = Worker(
        binance_futures_client=binance_futures_client,
        binance_spot_client=None,
        bitmex_client=None,
        order_status_queue=order_status_queue,
        ui_update_queue=ui_update_queue,
        pnl_update_queue=pnl_update_queue
    )

    # Link the worker back to the client if the client needs to trigger background tasks
    binance_futures_client.worker = worker

    # 4. Initialize GUI (Root)
    root = Root(
        binance_futures_client,
        None,  # Bitmex Placeholder
        None,  # Binance Spot Placeholder
        worker,
        order_status_queue,
        ui_update_queue,
        pnl_update_queue
    )

    binance_futures_client.root = root
    binance_futures_client.pnl_update_callback = pnl_update_queue.put

    # 6. START THE ENGINE
    # Start the worker thread (This triggers _handle_db_recovery inside worker.py)
    worker.start()

    print("Application Initialized. Mainloop starting...")
    #send_notification("Test ping!")

    # The Root class's _check_ui_queue will now automatically catch
    # the "CONNECTIONS_READY" signal from the worker and populate the UI.
    root.mainloop()