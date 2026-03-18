"""Traditional Chinese (zh-TW) translations for indicator and pattern names."""

INDICATOR_NAMES_ZH: dict[str, str] = {
    # Moving Averages
    "SMA_5": "簡單移動平均線(5)",
    "SMA_10": "簡單移動平均線(10)",
    "SMA_20": "簡單移動平均線(20)",
    "SMA_50": "簡單移動平均線(50)",
    "SMA_120": "簡單移動平均線(120)",
    "SMA_200": "簡單移動平均線(200)",
    "EMA_12": "指數移動平均線(12)",
    "EMA_26": "指數移動平均線(26)",
    "EMA_9": "指數移動平均線(9)",
    "EMA_21": "指數移動平均線(21)",
    "EMA_55": "指數移動平均線(55)",
    "EMA_200": "指數移動平均線(200)",
    # RSI
    "RSI_14": "相對強弱指標(14)",
    "RSI_7": "相對強弱指標(7)",
    # MACD
    "MACD": "MACD 指標(12,26,9)",
    "MACD_signal": "MACD 信號線",
    "MACD_hist": "MACD 柱狀圖",
    # Bollinger Bands
    "BB_upper": "布林通道上軌",
    "BB_mid": "布林通道中軌",
    "BB_lower": "布林通道下軌",
    # ATR
    "ATR_14": "平均真實波幅(14)",
    # Stochastic
    "STOCH_K": "隨機指標 %K",
    "STOCH_D": "隨機指標 %D",
    # ADX
    "ADX": "平均趨向指標",
    # Keltner Channel
    "Keltner_Upper": "肯特納通道上軌",
    "Keltner_Lower": "肯特納通道下軌",
    # StochRSI
    "StochRSI": "隨機RSI",
    # BB Squeeze
    "BB_Squeeze": "布林擠壓",
    # Volume
    "OBV": "能量潮指標",
    "VWAP": "成交量加權平均價",
}

PATTERN_NAMES_ZH: dict[str, str] = {
    # Bullish patterns
    "hammer": "鎚子線",
    "inverted_hammer": "倒鎚子線",
    "bullish_engulfing": "多頭吞噬",
    "morning_star": "晨星",
    "three_white_soldiers": "三白兵",
    "bullish_harami": "多頭孕線",
    "piercing_line": "貫穿線",
    "dragonfly_doji": "蜻蜓十字",
    # Bearish patterns
    "hanging_man": "吊人線",
    "shooting_star": "流星線",
    "bearish_engulfing": "空頭吞噬",
    "evening_star": "暮星",
    "three_black_crows": "三黑鴉",
    "bearish_harami": "空頭孕線",
    "dark_cloud_cover": "烏雲蓋頂",
    "gravestone_doji": "墓碑十字",
    # Neutral patterns
    "doji": "十字線",
    "spinning_top": "紡錘線",
    "marubozu": "光頭光腳線",
}

SIGNAL_TYPE_ZH: dict[str, str] = {
    "buy": "買入",
    "sell": "賣出",
    "hold": "觀望",
}

MARKET_NAMES_ZH: dict[str, str] = {
    "us": "美股",
    "tw": "台股",
    "crypto": "加密貨幣",
}

STRATEGY_NAMES_ZH: dict[str, str] = {
    "A_TREND": "趨勢跟隨策略",
    "B_SQUEEZE": "布林擠壓突破策略",
    "C_DIVERGENCE": "RSI背離策略",
    "D_SR_ZONES": "支撐壓力區交易策略",
}

MARKET_STATE_ZH: dict[str, str] = {
    "TRENDING_UP": "上升趨勢",
    "TRENDING_DOWN": "下降趨勢",
    "RANGING": "區間震盪",
}
