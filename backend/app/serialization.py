import math
from datetime import date, datetime
from decimal import Decimal

import pandas as pd


def json_value(value):
    """Only finite JSON primitives; dates are ISO, never Python NaN/NaT objects."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
        return str(value)  # Preserve integer precision across JavaScript's JSON boundary.
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)
