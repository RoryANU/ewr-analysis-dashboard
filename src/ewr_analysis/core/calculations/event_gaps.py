from typing import Any


def _mrip_days(max_inter_event_years: Any) -> int:
    """Maximum inter-event period (MRIP) in days; 0 when the EWR has none (blank, NaN or not positive)."""
    return int(round(float(max_inter_event_years) * 365)) if max_inter_event_years > 0 else 0
