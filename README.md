# 📈 Automated Quantitative Trading Bot (v0.5)

> A custom-engineered, multithreaded Python trading application built for live exchange integration, automated data ingestion, and local quantitative strategy execution.

---

## 🚀 What It Does
This project bridges the gap between raw exchange data and automated execution. Built with a focus on concurrency and performance, the bot connects directly to cryptocurrency exchanges, processes market feeds in real-time, executes trading strategies, and logs performance metrics—all controlled through a clean, custom graphical user interface.

## 🛠️ Core Architecture & Tech Stack

* **Language:** Python
* **Graphical User Interface (GUI):** Built with **Tkinter** for an interactive, lightweight local dashboard.
* **Concurrency:** Employs multi-threaded workers to stream data and execute tasks without blocking the UI thread.
* **Exchange Integrations:** Custom-built connector modules for **Binance** and **BitMEX** handling order execution and live websockets.
* **Database & Persistence:** **SQLite** integration for tracking trade history, win/loss ledgers, and local workspace data.
* **Backtesting & Analysis:** Tools and scripts for running historical strategy backtests and visual plotting.

---

## 📁 Project Structure

```text
TradingBot V0.5/
│
├── connectors/          # Custom API connectors (Binance, BitMEX)
├── interface/           # Tkinter UI components, widgets, and styling
├── models/              # Core data models, workers, and background threads
├── strategies.py        # Algorithmic trading strategies and indicators
├── database.py          # SQLite database management and query handling
├── main.py              # Application entry point
├── Dockerfile           # Containerization configuration for headless execution
└── requirements.txt     # Project dependencies
