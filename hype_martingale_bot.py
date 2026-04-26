"""
HYPE Martingale Short Bot — Hyperliquid Perpetuals
Strategy: RSI overbought (>70) triggers short entry
         1.5x martingale on each losing re-entry
         Take profit / Stop loss / Circuit breaker included

⚠️  WARNING: Martingale strategies carry extreme risk.
    Losses compound exponentially. Never risk more than you can afford to lose.
"""

import time
import json
import logging
import math
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field

# --- Install: pip install hyperliquid-python-sdk ---
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
import eth_account

# ──────────────────────────────────────────────
#  CONFIGURATION  (edit these before running)
# ──────────────────────────────────────────────
CONFIG = {
    # Wallet
    "private_key": "0xead56bf56b1cb7876e479061421a938cebea012d480efb3e523545dcf1121960",        # ⚠️  Never share this
    "wallet_address": "0xead56bf56b1cb7876e479061421a938cebea012d480efb3e523545dcf1121960",

    # Network
    "use_mainnet": False,                              # Set True for real money!

    # Asset
    "symbol": "HYPE",
    "leverage": 3,                                     # Cross leverage

    # Initial position size (in USD notional)
    "base_size_usd": 5.0,

    # Martingale
    "martingale_multiplier": 1.5,
    "max_layers": 5,                                   # Max doubling layers before halt

    # Indicators
    "rsi_period": 14,
    "rsi_overbought": 70,
    "bb_period": 20,
    "bb_std": 2.0,
    "ema_period": 200,
    "use_bb_filter": True,                             # 🛡️ Only short if price >= Upper Bollinger Band
    "use_ema_filter": False,                           # 🛡️ Only short if price < EMA (Warning: reduces trades)
    "candle_interval": "15m",                          # 1m, 5m, 15m, 1h, 4h

    # Risk management
    "take_profit_pct": 2.0,                            # % below avg entry price
    "stop_loss_pct": 6.0,                              # % above avg entry (circuit breaker)
    "max_drawdown_usd": 100.0,                         # Hard stop: halt if session loss exceeds this

    # Timing
    "poll_interval_seconds": 60,                       # How often to check RSI
    "cooldown_after_win_seconds": 300,                 # Wait after TP before re-entering
}

# ──────────────────────────────────────────────
#  LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("hype_bot.log"),
    ],
)
log = logging.getLogger("HYPEBot")


# ──────────────────────────────────────────────
#  STATE
# ──────────────────────────────────────────────
@dataclass
class BotState:
    running: bool = True
    layer: int = 0                        # Current martingale layer (0 = no open position)
    total_size_usd: float = 0.0           # Total USD notional currently shorted
    avg_entry_price: float = 0.0          # Weighted average entry price
    session_pnl: float = 0.0             # Realized PnL this session
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    last_win_time: Optional[float] = None
    open_orders: list = field(default_factory=list)

    def win_rate(self) -> str:
        if self.total_trades == 0:
            return "N/A"
        return f"{(self.wins / self.total_trades) * 100:.1f}%"

    def next_size_usd(self) -> float:
        """Size for the next martingale layer."""
        base = CONFIG["base_size_usd"]
        mult = CONFIG["martingale_multiplier"]
        return round(base * (mult ** self.layer), 2)

    def in_position(self) -> bool:
        return self.layer > 0


# ──────────────────────────────────────────────
#  INDICATOR CALCULATIONS
# ──────────────────────────────────────────────
def compute_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0  # Not enough data → neutral
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_bollinger_bands(closes: list[float], period: int = 20, num_std: float = 2.0) -> tuple[float, float]:
    """Returns (upper_band, lower_band)"""
    if len(closes) < period:
        return 0.0, 0.0
    sma = sum(closes[-period:]) / period
    variance = sum((x - sma) ** 2 for x in closes[-period:]) / period
    std_dev = math.sqrt(variance)
    return sma + (std_dev * num_std), sma - (std_dev * num_std)


def compute_ema(closes: list[float], period: int = 200) -> float:
    if len(closes) < period:
        return 0.0
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period  # Start with SMA
    for price in closes[period:]:
        ema = (price - ema) * k + ema
    return ema


# ──────────────────────────────────────────────
#  HYPERLIQUID HELPERS
# ──────────────────────────────────────────────
def get_candle_closes(info: Info, symbol: str, interval: str, limit: int = 250) -> list[float]:
    """Fetch recent candle close prices."""
    try:
        now_ms = int(time.time() * 1000)
        interval_ms = {
            "1m": 60_000, "5m": 300_000, "15m": 900_000,
            "1h": 3_600_000, "4h": 14_400_000,
        }.get(interval, 900_000)
        start_ms = now_ms - limit * interval_ms
        candles = info.candles_snapshot(symbol, interval, start_ms, now_ms)
        closes = [float(c["c"]) for c in candles]
        return closes
    except Exception as e:
        log.error(f"Failed to fetch candles: {e}")
        return []


def get_mark_price(info: Info, symbol: str) -> float:
    """Get current mark price for symbol."""
    try:
        meta = info.meta_and_asset_ctxs()
        universe = meta[0]["universe"]
        ctxs = meta[1]
        for i, asset in enumerate(universe):
            if asset["name"] == symbol:
                return float(ctxs[i]["markPx"])
    except Exception as e:
        log.error(f"Failed to get mark price: {e}")
    return 0.0


def get_position(info: Info, address: str, symbol: str) -> Optional[dict]:
    """Get current open position for symbol."""
    try:
        state = info.user_state(address)
        for pos in state.get("assetPositions", []):
            p = pos.get("position", {})
            if p.get("coin") == symbol:
                return p
    except Exception as e:
        log.error(f"Failed to get position: {e}")
    return None


def place_market_short(exchange: Exchange, symbol: str, size_usd: float, mark_price: float) -> bool:
    """Place a market short order. size_usd / mark_price = qty."""
    try:
        qty = round(size_usd / mark_price, 4)
        if qty <= 0:
            log.warning("Calculated qty is zero, skipping order.")
            return False
        log.info(f"📉  Placing SHORT: {qty} {symbol} @ ~${mark_price:.4f} (${size_usd:.2f} notional)")
        result = exchange.market_open(symbol, is_buy=False, sz=qty)
        log.info(f"Order result: {result}")
        return result.get("status") == "ok"
    except Exception as e:
        log.error(f"Failed to place short: {e}")
        return False


def close_position(exchange: Exchange, info: Info, address: str, symbol: str) -> bool:
    """Market close the full position."""
    try:
        pos = get_position(info, address, symbol)
        if not pos:
            log.warning("No position to close.")
            return False
        szi = float(pos.get("szi", 0))
        if szi == 0:
            return False
        # szi is negative for shorts; to close we buy abs(szi)
        qty = abs(szi)
        log.info(f"🔒  Closing position: buying {qty} {symbol}")
        result = exchange.market_close(symbol)
        log.info(f"Close result: {result}")
        return result.get("status") == "ok"
    except Exception as e:
        log.error(f"Failed to close position: {e}")
        return False


# ──────────────────────────────────────────────
#  MAIN BOT LOOP
# ──────────────────────────────────────────────
def run_bot():
    log.info("=" * 60)
    log.info("  HYPE Martingale Short Bot — Starting")
    log.info("=" * 60)
    log.info(f"  Symbol       : {CONFIG['symbol']}-PERP")
    log.info(f"  Network      : {'MAINNET ⚠️' if CONFIG['use_mainnet'] else 'TESTNET'}")
    log.info(f"  Base size    : ${CONFIG['base_size_usd']}")
    log.info(f"  Multiplier   : {CONFIG['martingale_multiplier']}x")
    log.info(f"  Max layers   : {CONFIG['max_layers']}")
    log.info(f"  RSI threshold: {CONFIG['rsi_overbought']}")
    log.info(f"  TP / SL      : {CONFIG['take_profit_pct']}% / {CONFIG['stop_loss_pct']}%")
    log.info("=" * 60)

    # ── Init SDK ──
    wallet = eth_account.Account.from_key(CONFIG["private_key"])
    base_url = constants.MAINNET_API_URL if CONFIG["use_mainnet"] else constants.TESTNET_API_URL
    info = Info(base_url, skip_ws=True)
    exchange = Exchange(wallet, base_url)
    address = CONFIG["wallet_address"]

    # ── Set leverage ──
    try:
        exchange.update_leverage(CONFIG["leverage"], CONFIG["symbol"], is_cross=True)
        log.info(f"✅  Leverage set to {CONFIG['leverage']}x cross")
    except Exception as e:
        log.warning(f"Could not set leverage: {e}")

    state = BotState()

    while state.running:
        try:
            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

            # ── Guard: max drawdown ──
            if state.session_pnl <= -CONFIG["max_drawdown_usd"]:
                log.error(f"🚨  MAX DRAWDOWN HIT (${state.session_pnl:.2f}). Bot halted.")
                if state.in_position():
                    close_position(exchange, info, address, CONFIG["symbol"])
                state.running = False
                break

            # ── Guard: max layers ──
            if state.layer >= CONFIG["max_layers"] and state.in_position():
                log.warning(f"⛔  Max martingale layers ({CONFIG['max_layers']}) reached. Waiting for TP/SL only.")

            # ── Get current price & Indicators ──
            mark_price = get_mark_price(info, CONFIG["symbol"])
            closes = get_candle_closes(info, CONFIG["symbol"], CONFIG["candle_interval"], limit=250)
            
            rsi = compute_rsi(closes, CONFIG["rsi_period"]) if closes else 50.0
            upper_bb, lower_bb = compute_bollinger_bands(closes, CONFIG["bb_period"], CONFIG["bb_std"]) if closes else (0.0, 0.0)
            ema_200 = compute_ema(closes, CONFIG["ema_period"]) if closes else 0.0

            # Evaluate entry conditions
            bb_condition = (mark_price >= upper_bb) if CONFIG["use_bb_filter"] else True
            ema_condition = (mark_price < ema_200) if CONFIG["use_ema_filter"] else True
            rsi_condition = (rsi >= CONFIG["rsi_overbought"])

            entry_signal = rsi_condition and bb_condition and ema_condition

            log.info(f"[{now_str}] Price: ${mark_price:.4f} | RSI: {rsi:.1f} | Up BB: ${upper_bb:.4f} | Layer: {state.layer} | PnL: ${state.session_pnl:.2f}")

            # ── Check existing position for TP / SL ──
            if state.in_position() and mark_price > 0:
                tp_price = state.avg_entry_price * (1 - CONFIG["take_profit_pct"] / 100)
                sl_price = state.avg_entry_price * (1 + CONFIG["stop_loss_pct"] / 100)

                log.info(f"         TP target: ${tp_price:.4f}  |  SL trigger: ${sl_price:.4f}  |  Avg entry: ${state.avg_entry_price:.4f}")

                # Take Profit
                if mark_price <= tp_price:
                    log.info(f"✅  TAKE PROFIT hit at ${mark_price:.4f}!")
                    if close_position(exchange, info, address, CONFIG["symbol"]):
                        pnl_est = state.total_size_usd * (CONFIG["take_profit_pct"] / 100)
                        state.session_pnl += pnl_est
                        state.wins += 1
                        state.total_trades += 1
                        state.layer = 0
                        state.total_size_usd = 0.0
                        state.avg_entry_price = 0.0
                        state.last_win_time = time.time()
                        log.info(f"🏆  Win! Est. PnL: +${pnl_est:.2f}  |  Session: ${state.session_pnl:.2f}  |  W/L: {state.wins}/{state.losses}")

                # Stop Loss
                elif mark_price >= sl_price:
                    log.info(f"🛑  STOP LOSS hit at ${mark_price:.4f}!")
                    if close_position(exchange, info, address, CONFIG["symbol"]):
                        pnl_est = -state.total_size_usd * (CONFIG["stop_loss_pct"] / 100)
                        state.session_pnl += pnl_est
                        state.losses += 1
                        state.total_trades += 1
                        state.layer = 0
                        state.total_size_usd = 0.0
                        state.avg_entry_price = 0.0
                        log.info(f"💸  Loss! Est. PnL: ${pnl_est:.2f}  |  Session: ${state.session_pnl:.2f}  |  W/L: {state.wins}/{state.losses}")

                # Martingale re-entry: if RSI still overbought AND above upper BB (if filter is on)
                elif rsi >= CONFIG["rsi_overbought"] and bb_condition and state.layer < CONFIG["max_layers"]:
                    next_size = state.next_size_usd()
                    log.info(f"📊  Conditions met for re-entry. Adding martingale layer {state.layer + 1} (${next_size:.2f})")
                    if place_market_short(exchange, CONFIG["symbol"], next_size, mark_price):
                        # Update weighted average entry
                        total_before = state.total_size_usd
                        state.avg_entry_price = (
                            (state.avg_entry_price * total_before + mark_price * next_size)
                            / (total_before + next_size)
                        )
                        state.total_size_usd += next_size
                        state.layer += 1
                        log.info(f"↗️   Layer {state.layer} added. Total: ${state.total_size_usd:.2f} | Avg entry: ${state.avg_entry_price:.4f}")

            # ── No open position: look for fresh entry ──
            elif not state.in_position() and mark_price > 0:
                # Cooldown after win
                if state.last_win_time and (time.time() - state.last_win_time) < CONFIG["cooldown_after_win_seconds"]:
                    remaining = CONFIG["cooldown_after_win_seconds"] - (time.time() - state.last_win_time)
                    log.info(f"⏳  Cooldown after win: {remaining:.0f}s remaining")
                elif entry_signal:
                    base_size = CONFIG["base_size_usd"]
                    log.info(f"🚀  ENTRY SIGNAL MET (RSI={rsi:.1f}, BB_Ok={bb_condition}, EMA_Ok={ema_condition}) — Opening initial short (${base_size:.2f})")
                    if place_market_short(exchange, CONFIG["symbol"], base_size, mark_price):
                        state.avg_entry_price = mark_price
                        state.total_size_usd = base_size
                        state.layer = 1
                        log.info(f"📉  Short opened. Layer 1. Entry: ${mark_price:.4f}")
                else:
                    log.info(f"         Watching... (RSI: {rsi:.1f}, Entry Signal: {entry_signal})")

        except KeyboardInterrupt:
            log.info("\n⛔  Interrupted by user. Closing any open positions...")
            if state.in_position():
                close_position(exchange, info, address, CONFIG["symbol"])
            state.running = False
            break
        except Exception as e:
            log.error(f"Unexpected error: {e}", exc_info=True)

        time.sleep(CONFIG["poll_interval_seconds"])

    log.info("=" * 60)
    log.info(f"  Bot stopped. Session PnL: ${state.session_pnl:.2f}")
    log.info(f"  Trades: {state.total_trades}  |  Win rate: {state.win_rate()}")
    log.info("=" * 60)


if __name__ == "__main__":
    run_bot()
