# interface/styling.py

# --- NEW DESIGN SYSTEM TOKENS ---
BG_DARK = "#0b1329"        # Main Window Background
CARD_BG = "#111c3a"        # Container Card Panels
CARD_BORDER = "#1f2d5a"    # Subtle Card Outline
INPUT_BG = "#080d1d"       # Text Fields / Dropdowns

FG_PRIMARY = "#ffffff"     # Headers and Active Text
FG_MUTED = "#8fa0da"       # Secondary Labels / Timestamps

CYAN_ACCENT = "#00f2fe"    # Primary Highlights
GREEN_NEON = "#00e676"     # LIVE / Buy / Positive P&L
RED_NEON = "#ff2a6d"       # OFF / Sell / Negative P&L

GLOBAL_FONT = ("Inter", 11)
BOLD_FONT = ("Inter", 11, "bold")
HEADER_FONT = ("Inter", 13, "bold")

# --- BACKWARD COMPATIBILITY ALIASES ---
# Maps old legacy variable names to the new dark theme colors
BG_COLOR = CARD_BG
BG_COLOR_2 = INPUT_BG
FG_COLOR = FG_PRIMARY
FG_COLOR_2 = FG_MUTED
GREEN = GREEN_NEON
RED = RED_NEON