import time
from hyperliquid.info import Info
from hyperliquid.utils import constants

info = Info(constants.TESTNET_API_URL, skip_ws=True)
now_ms = int(time.time() * 1000)
start_ms = now_ms - 250 * 60_000
candles = info.candles_snapshot("HYPE", "1m", start_ms, now_ms)
print("TESTNET 1m candles returned:", len(candles))
if len(candles) > 0:
    print("first candle:", candles[0])
    print("last candle:", candles[-1])
    closes = [float(c["c"]) for c in candles]
    print("Closes set size:", len(set(closes)))

