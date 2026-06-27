def nandi_decision(market_bias, strategy_signal, risk_status):
    score = 0
    reasons = []

    if market_bias == "Bullish":
        score += 30
        reasons.append("Market bias is bullish.")
    elif market_bias == "Bearish":
        score -= 30
        reasons.append("Market bias is bearish.")
    else:
        reasons.append("Market is neutral.")

    if strategy_signal == "Buy":
        score += 40
        reasons.append("Strategy gives buy confirmation.")
    elif strategy_signal == "Sell":
        score -= 40
        reasons.append("Strategy gives sell confirmation.")
    else:
        reasons.append("Strategy has no clear confirmation.")

    if risk_status == "Safe":
        score += 30
        reasons.append("Risk is acceptable.")
    else:
        score -= 30
        reasons.append("Risk is not acceptable.")

    if score >= 70:
        action = "BUY"
    elif score <= -40:
        action = "AVOID / BEARISH"
    else:
        action = "WAIT"

    return {
        "action": action,
        "confidence": max(0, min(100, score)),
        "reasons": reasons
    }
