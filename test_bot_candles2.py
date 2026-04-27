import time
from hyperliquid.info import Info
from hyperliquid.utils import constants

def get_candle_closes(info: Info, symbol: str, interval: str, limit: int = 250) -> list[float]:
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
        print(f"Failed to fetch candles: {e}")
        return []

info = Info(constants.TESTNET_API_URL, skip_ws=True)
closes = get_candle_closes(info, "HYPE", "1m", limit=250)
period = 14

gains, losses = [], []
for i in range(1, len(closes)):
    diff = closes[i] - closes[i - 1]
    gains.append(max(diff, 0))
    losses.append(max(-diff, 0))
avg_gain = sum(gains[-period:]) / period
avg_loss = sum(losses[-period:]) / period

print("len(closes):", len(closes))
print("last 14 gains:", gains[-period:])
print("last 14 losses:", losses[-period:])
print("avg_gain:", avg_gain)
print("avg_loss:", avg_loss)
if avg_loss == 0:
    print("rsi: 100")
else:
    rs = avg_gain / avg_loss
    print("rsi:", round(100 - (100 / (1 + rs)), 2))

