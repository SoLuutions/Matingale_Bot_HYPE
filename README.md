# HYPE Martingale Short Bot

A Hyperliquid perpetuals trading bot that shorts HYPE using:
- **RSI overbought signal** (RSI > 70 on 15m candles)
- **1.5x martingale** position sizing on losers
- **Take Profit / Stop Loss / Max Drawdown** circuit breakers

---

## ⚠️ Risk Warning

Martingale strategies can wipe your account in a sustained uptrend.
Each losing layer increases your size by 1.5x — five layers means ~7.6x
your initial size. Only use with capital you can afford to lose entirely.

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure the bot

Edit `CONFIG` in `hype_martingale_bot.py`:

| Key | Description |
|-----|-------------|
| `private_key` | Your Hyperliquid wallet private key |
| `wallet_address` | Your public wallet address |
| `use_mainnet` | `False` = testnet, `True` = real money |
| `base_size_usd` | Initial short size in USD (e.g. $10) |
| `martingale_multiplier` | 1.5 = each layer is 1.5x the previous |
| `max_layers` | Hard cap on martingale layers (default: 5) |
| `rsi_period` | RSI lookback period (default: 14) |
| `rsi_overbought` | RSI threshold to trigger short (default: 70) |
| `candle_interval` | Candle timeframe: `1m`, `5m`, `15m`, `1h` |
| `take_profit_pct` | % below avg entry to take profit |
| `stop_loss_pct` | % above avg entry to stop loss |
| `max_drawdown_usd` | Max session loss before bot halts |
| `poll_interval_seconds` | How often the bot checks (seconds) |

### 3. Test on testnet first!
```bash
# Set use_mainnet = False, then:
python hype_martingale_bot.py
```

### 4. Run
```bash
python hype_martingale_bot.py
```

Stop with `Ctrl+C` — the bot will close open positions before exiting.

---

## How it Works

```
RSI > 70?
   └─ No position → Open Layer 1 short ($base_size)
   └─ In position → Add Layer N short ($prev_size × 1.5)

Each poll:
   ├─ Mark price ≤ TP price  → Close all, reset, start cooldown
   ├─ Mark price ≥ SL price  → Close all, reset
   └─ Session loss ≥ max_drawdown → Close all, HALT
```

### Layer sizing example (base=$10, 1.5x)
| Layer | Size | Cumulative |
|-------|------|------------|
| 1 | $10.00 | $10.00 |
| 2 | $15.00 | $25.00 |
| 3 | $22.50 | $47.50 |
| 4 | $33.75 | $81.25 |
| 5 | $50.63 | $131.88 |

---

## Logs

All activity is logged to `hype_bot.log` and printed to the console.

---

## Getting a Hyperliquid API Key

1. Go to https://app.hyperliquid.xyz
2. Connect your wallet
3. Settings → API → Generate API key
4. Use the private key from your generated API wallet

For testnet: https://app.hyperliquid-testnet.xyz
