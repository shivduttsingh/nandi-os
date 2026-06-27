def risk_check(capital, max_risk_percent, stop_loss_points, lot_size=1):
    max_loss_allowed = capital * (max_risk_percent / 100)
    trade_risk = stop_loss_points * lot_size

    if trade_risk <= max_loss_allowed:
        status = "Safe"
    else:
        status = "Unsafe"

    return {
        "capital": capital,
        "max_loss_allowed": round(max_loss_allowed, 2),
        "trade_risk": round(trade_risk, 2),
        "status": status
    }
