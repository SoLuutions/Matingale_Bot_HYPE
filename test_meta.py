from hyperliquid.info import Info
from hyperliquid.utils import constants

for env_url in [constants.TESTNET_API_URL, constants.MAINNET_API_URL]:
    info = Info(env_url, skip_ws=True)
    meta = info.meta()
    for asset in meta["universe"]:
        if asset["name"] == "HYPE":
            print(f"{env_url} HYPE szDecimals:", asset["szDecimals"])
