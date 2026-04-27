import time
from hyperliquid.info import Info
from hyperliquid.utils import constants

info = Info(constants.MAINNET_API_URL, skip_ws=True)
now_ms = int(time.time() * 1000)
start_ms = now_ms - 250 * 60_000
candles = info.candles_snapshot("HYPE", "1m", start_ms, now_ms)
print("1m candles returned:", len(candles))
if len(candles) > 0:
    print("first candle:", candles[0])
    print("last candle:", candles[-1])

try:
    print("15m candles returned:", len(info.candles_snapshot("HYPE", "15m", now_ms - 250*900_000, now_ms)))
except Exception as e:
    print(e)
