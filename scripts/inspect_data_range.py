"""Quick: print 15m feather data ranges. To be uploaded to VPS and exec'd."""
import pandas as pd, glob

for f in sorted(glob.glob("/freqtrade/user_data/data/okx/futures/*15m-futures.feather")):
    df = pd.read_feather(f)
    pair = f.split("/")[-1].replace("-15m-futures.feather", "").replace("_USDT_USDT", "/USDT:USDT")
    print(f"{pair:<22} {str(df['date'].min())[:19]} ~ {str(df['date'].max())[:19]} ({len(df):>6} candles)")
