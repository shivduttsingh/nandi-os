def market_bias_engine(price, support, resistance):
    if price > resistance:
        bias = "Bullish"
        reason = "Price is trading above resistance breakout level."
    elif price < support:
        bias = "Bearish"
        reason = "Price is trading below support breakdown level."
    else:
        bias = "Neutral"
        reason = "Price is trading inside support-resistance range."

    return {
        "bias": bias,
        "price": price,
        "support": support,
        "resistance": resistance,
        "reason": reason
    }
