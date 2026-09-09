def format_alert(alert_type: str, username: str, score: float | None, confidence: float | None,
                 classification: str | None, rank: int | None) -> str:
    lines = [f"WHALE ALERT: {alert_type}", f"Trader: @{username}"]
    if score is not None:
        lines.append(f"Whale Score: {score:.1f}")
    if confidence is not None:
        lines.append(f"Confidence: {confidence:.0f}")
    if classification:
        lines.append(f"Classification: {classification}")
    if rank is not None:
        lines.append(f"Current Rank: #{rank}")
    return "\n".join(lines)
