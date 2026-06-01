import ta
import pandas as pd

def apply_strategy(df):
    close = df['Close']

    # force 1D
    close = close.squeeze()
    close = pd.Series(close.values.flatten(), index=df.index)

    df['rsi'] = ta.momentum.RSIIndicator(close).rsi()

    macd = ta.trend.MACD(close)
    df['macd'] = macd.macd()
    df['signal'] = macd.macd_signal()
    
    # Bollinger Bands
    bb = ta.volatility.BollingerBands(close)
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_lower'] = bb.bollinger_lband()
    df['bb_mavg'] = bb.bollinger_mavg()
    
    # EMAs
    df['ema_20'] = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    df['ema_50'] = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    df['ema_200'] = ta.trend.EMAIndicator(close, window=200).ema_indicator()
    
    # Indicators requiring High/Low/Volume
    if all(col in df.columns for col in ['High', 'Low']):
        # ATR
        df['atr'] = ta.volatility.AverageTrueRange(df['High'], df['Low'], close).average_true_range()
        
        # CCI (14)
        df['cci'] = ta.trend.CCIIndicator(df['High'], df['Low'], close, window=14).cci()
        
        # ADX (14)
        adx_ind = ta.trend.ADXIndicator(df['High'], df['Low'], close, window=14)
        df['adx'] = adx_ind.adx()
        df['adx_pos'] = adx_ind.adx_pos()
        df['adx_neg'] = adx_ind.adx_neg()
        
        # MFI (needs Volume as well)
        if 'Volume' in df.columns:
            df['mfi'] = ta.volume.MFIIndicator(df['High'], df['Low'], close, df['Volume'], window=14).money_flow_index()

        # Pivot Points (using previous day's HLC)
        prev_high = df['High'].shift(1)
        prev_low = df['Low'].shift(1)
        prev_close = df['Close'].shift(1)
        
        df['pivot'] = (prev_high + prev_low + prev_close) / 3
        df['r1'] = (2 * df['pivot']) - prev_low
        df['s1'] = (2 * df['pivot']) - prev_high
        df['r2'] = df['pivot'] + (prev_high - prev_low)
        df['s2'] = df['pivot'] - (prev_high - prev_low)
        df['r3'] = prev_high + 2 * (df['pivot'] - prev_low)
        df['s3'] = prev_low - 2 * (prev_high - df['pivot'])

    return df


def generate_signal(df):
    latest = df.iloc[-1]

    # SAFE scalar extraction (NO warnings)
    rsi = latest['rsi'] if not hasattr(latest['rsi'], "iloc") else latest['rsi'].iloc[0]
    macd = latest['macd'] if not hasattr(latest['macd'], "iloc") else latest['macd'].iloc[0]
    signal = latest['signal'] if not hasattr(latest['signal'], "iloc") else latest['signal'].iloc[0]
    
    price = latest['Close'] if not hasattr(latest['Close'], "iloc") else latest['Close'].iloc[0]
    bb_upper = latest.get('bb_upper', float('inf'))
    bb_lower = latest.get('bb_lower', 0)
    ema_20 = latest.get('ema_20', price)
    
    if hasattr(bb_upper, "iloc"): bb_upper = bb_upper.iloc[0]
    if hasattr(bb_lower, "iloc"): bb_lower = bb_lower.iloc[0]
    if hasattr(ema_20, "iloc"): ema_20 = ema_20.iloc[0]

    # Combine RSI, MACD and BB/EMA
    if rsi >= 70 and price >= bb_upper:
        return "SELL 🔻"
    if rsi >= 75 and macd < signal:
        return "SELL 🔻"
    if rsi <= 30 and price <= bb_lower:
        return "BUY 🚀"
    if rsi <= 45 and macd > signal and price > ema_20:
        return "BUY 🚀"
    return "HOLD ⏳"
