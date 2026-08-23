"""
Calendar/seasonal effect testing over a daily underlying price series.

IMPORTANT: seasonality needs a LONG history to mean anything (20-30+
years across many repeated cycles) -- this module will happily run on
a 2-month CSV and produce numbers, but with n this small every t-stat
here is noise. See run_seasonality.py's printed caveat and the README.

Effects implemented, each a function that tags every trading day as
in/out of the window, so returns can be split into "in-window" vs
"rest of history" and compared:
  - month_of_year: which calendar month
  - day_of_week: Mon-Fri
  - turn_of_month: last trading day of month + first 3 trading days of next month
  - santa_claus: last 5 trading days of Dec + first 2 trading days of Jan
  - opex_week: the week containing the 3rd Friday of the month (monthly OPEX)
  - sell_in_may: May-Oct vs Nov-Apr ("Sell in May and go away")
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable


@dataclass
class DailyReturn:
    d: date
    ret: float   # log return from prior close


def to_returns(price_series: list[tuple[date, float]]) -> list[DailyReturn]:
    out = []
    for i in range(1, len(price_series)):
        d0, p0 = price_series[i - 1]
        d1, p1 = price_series[i]
        if p0 and p1 and p0 > 0:
            out.append(DailyReturn(d=d1, ret=math.log(p1 / p0)))
    return out


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    fridays = 0
    while True:
        if d.weekday() == 4:  # Friday
            fridays += 1
            if fridays == 3:
                return d
        d += timedelta(days=1)


def tag_month_of_year(dr: DailyReturn, month: int) -> bool:
    return dr.d.month == month


def tag_day_of_week(dr: DailyReturn, weekday: int) -> bool:
    return dr.d.weekday() == weekday   # 0=Mon .. 4=Fri


def tag_sell_in_may(dr: DailyReturn) -> bool:
    """True if date falls in the seasonally weak May-Oct window."""
    return dr.d.month in (5, 6, 7, 8, 9, 10)


def tag_santa_claus(dr: DailyReturn) -> bool:
    """
    Approximate: last 5 calendar days of December or first 2 of January.
    (A precise "last 5 TRADING days" needs the actual trading calendar;
    this calendar-day approximation is a reasonable first pass and is
    clearly documented as such.)
    """
    if dr.d.month == 12 and dr.d.day >= 27:
        return True
    if dr.d.month == 1 and dr.d.day <= 2:
        return True
    return False


def tag_turn_of_month(dr: DailyReturn) -> bool:
    """Approximate: last calendar day of month or first 3 calendar days of next month."""
    if dr.d.day <= 3:
        return True
    next_day = dr.d + timedelta(days=1)
    if next_day.month != dr.d.month:
        return True
    return False


def tag_opex_week(dr: DailyReturn) -> bool:
    tf = _third_friday(dr.d.year, dr.d.month)
    week_start = tf - timedelta(days=tf.weekday())  # Monday of that week
    week_end = week_start + timedelta(days=4)
    return week_start <= dr.d <= week_end


EFFECTS: dict[str, Callable[[DailyReturn], bool]] = {
    "Sell-in-May (May-Oct)": tag_sell_in_may,
    "Santa Claus rally (approx)": tag_santa_claus,
    "Turn-of-month (approx, calendar days)": tag_turn_of_month,
    "OPEX week (3rd Friday week)": tag_opex_week,
    "Monday": lambda dr: tag_day_of_week(dr, 0),
    "Tuesday": lambda dr: tag_day_of_week(dr, 1),
    "Wednesday": lambda dr: tag_day_of_week(dr, 2),
    "Thursday": lambda dr: tag_day_of_week(dr, 3),
    "Friday": lambda dr: tag_day_of_week(dr, 4),
}
for m, name in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1):
    EFFECTS[f"Month: {name}"] = (lambda dr, m=m: tag_month_of_year(dr, m))
