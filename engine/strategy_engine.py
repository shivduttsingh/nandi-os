import pandas as pd

def calculate_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def rsi_24_78_signal(df):
    df = df.copy()

    if "Close" not in df.columns:
        return {
            "signal": "No Signal",
            "reason": "Close column missing."
        }

    df["RSI"] = calculate_rsi(df["Close"])

    latest_rsi = df["RSI"].iloc[-1]

    if latest_rsi <= 24:
        signal = "Buy"
        reason = "RSI is near oversold 24 zone."
    elif latest_rsi >= 78:
        signal = "Exit"
        reason = "RSI is near overbought 78 zone."
    else:
        signal = "No Signal"
        reason = "RSI is between 24 and 78."

    return {
        "signal": signal,
        "latest_rsi": round(float(latest_rsi), 2),
        "reason": reason
    }
