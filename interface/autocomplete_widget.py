import tkinter as tk
from interface.styling import *


class Autocomplete(tk.Entry):
    def __init__(self, symbols, master, *args, **kwargs):
        super().__init__(master, *args, **kwargs)

        self._symbols = symbols
        self._listbox = None
        self._listbox_height = 5

        # Bindings
        self.bind("<KeyRelease>", self._update_suggestions)
        self.bind("<Down>", self._on_arrow_down)
        self.bind("<Up>", self._on_arrow_up)
        self.bind("<Return>", self._on_enter, add="+")  # add="+" keeps existing bindings in watchlist
        self.master.winfo_toplevel().bind("<Button-1>", lambda e: self._close_suggestions(), add="+")

    def _update_suggestions(self, event):
        value = self.get().strip().upper()

        if value == "":
            self._close_suggestions()
            return

        data = [s for s in self._symbols if value in s.upper()]

        if data:
            if not self._listbox:
                # 1. Attach to the "TopLevel" window so it floats OVER the layout boundaries
                self._listbox = tk.Listbox(self.winfo_toplevel(), bg=BG_COLOR_2, fg=FG_COLOR,
                                           exportselection=False, highlightthickness=0, bd=0)

                # 2. Calculate absolute screen coordinates to place it perfectly under the entry
                x = self.winfo_rootx() - self.winfo_toplevel().winfo_rootx()
                y = self.winfo_rooty() - self.winfo_toplevel().winfo_rooty() + self.winfo_height()

                self._listbox.place(x=x, y=y, width=self.winfo_width())

                self._listbox.bind("<Return>", self._on_enter)
                self._listbox.bind("<Double-1>", self._on_enter)
                self._listbox.lift()

            self._listbox.delete(0, tk.END)
            for item in data[:10]:  # Limit to 10 items for a clean UI
                self._listbox.insert(tk.END, item)
        else:
            self._close_suggestions()

    def _on_arrow_down(self, event):
        if self._listbox:
            self._listbox.focus_set()
            self._listbox.selection_set(0)

    def _on_arrow_up(self, event):
        if self._listbox:
            self._listbox.focus_set()
            self._listbox.selection_set(tk.END)

    def _on_enter(self, event):
        """Selects the item, closes the box, and triggers the Watchlist add."""
        if self._listbox and self._listbox.curselection():
            index = self._listbox.curselection()[0]
            selected_symbol = self._listbox.get(index)

            self.delete(0, tk.END)
            self.insert(0, selected_symbol)
            self._close_suggestions()

            # IMPORTANT: Manually trigger the <Return> event on the Entry widget
            # This ensures the Watchlist._add_binance_symbol method runs
            self.event_generate("<Return>")

        elif not self._listbox:
            # If the listbox is already closed, let the event pass through normally
            pass

    def _close_suggestions(self):
        if self._listbox:
            self._listbox.destroy()
            self._listbox = None

    def set_suggestions(self, symbols: list):
        """Updates the internal symbol list and sorts them for better UX."""
        self._symbols = sorted(symbols)