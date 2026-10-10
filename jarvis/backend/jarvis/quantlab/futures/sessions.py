"""Exchange sessions from a versioned calendar library — holidays, early closes, DST.

Two calendars matter for CME equity index futures:

* ``CMES`` — the Globex session (Sunday–Friday, 17:00 → 16:00 Chicago time),
  used to tell legitimate no-trade intervals from missing data.
* ``XNYS`` — the US cash-equity session (09:30–16:00 New York), the usual
  "RTH" reference for index futures strategies. Its early closes (e.g. 13:00
  on the day after Thanksgiving) shorten a strategy's window.

All times are UTC nanoseconds internally; local wall-clock rules (09:30
America/New_York) are converted per date, so daylight-saving changes move the
UTC times, never the local ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

NS = 1_000_000_000
LIBRARY = "exchange_calendars"


def library_version() -> str:
    import exchange_calendars

    return str(exchange_calendars.__version__)


@dataclass(frozen=True)
class Session:
    label: date
    open_ns: int
    close_ns: int
    early_close: bool


@lru_cache(maxsize=16)
def _schedule(name: str, first_year: int, last_year: int) -> tuple[Session, ...]:
    import exchange_calendars as xc

    cal = xc.get_calendar(name, start=f"{first_year}-01-01", end=f"{last_year}-12-31")
    early = {ts.date() for ts in cal.early_closes}
    return tuple(
        Session(
            label=label.date(),
            open_ns=int(row["open"].value),
            close_ns=int(row["close"].value),
            early_close=label.date() in early,
        )
        for label, row in cal.schedule.iterrows()
    )


def sessions(name: str, start: date, end: date) -> list[Session]:
    """Sessions whose label date is in [start, end)."""
    first, last = start.year - 1, max(end.year, start.year) + 1
    return [s for s in _schedule(name, first, last) if start <= s.label < end]


def local_ns(day: date, clock: time, zone: str) -> int:
    moment = datetime.combine(day, clock, tzinfo=ZoneInfo(zone))
    return int(moment.timestamp()) * NS


@dataclass(frozen=True)
class Window:
    """A strategy's trading window on one session, in UTC ns."""

    label: date
    start_ns: int
    end_ns: int
    flatten_ns: int
    entry_cutoff_ns: int
    session_close_ns: int
    early_close: bool


def windows(
    calendar: str,
    zone: str,
    start: time,
    end: time,
    flatten: time,
    entry_cutoff: time | None,
    first: date,
    last: date,
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4),
) -> list[Window]:
    """The strategy's local-time window on every calendar session.

    On an early close the window ends at the actual close and the flatten time
    keeps its distance to the end (15:55 for a 16:00 end becomes 12:55 on a
    13:00 close). Sessions the calendar doesn't have (holidays) don't exist.
    """
    out: list[Window] = []
    for session in sessions(calendar, first, last):
        if session.label.weekday() not in weekdays:
            continue
        w_start = max(local_ns(session.label, start, zone), session.open_ns)
        planned_end = local_ns(session.label, end, zone)
        w_end = min(planned_end, session.close_ns)
        lead = planned_end - local_ns(session.label, flatten, zone)
        w_flatten = min(local_ns(session.label, flatten, zone), w_end - lead)
        cutoff = local_ns(session.label, entry_cutoff, zone) if entry_cutoff else w_flatten
        if w_end <= w_start:
            continue
        out.append(
            Window(
                label=session.label,
                start_ns=w_start,
                end_ns=w_end,
                flatten_ns=max(w_start, w_flatten),
                entry_cutoff_ns=min(cutoff, w_flatten),
                session_close_ns=session.close_ns,
                early_close=session.early_close or planned_end > session.close_ns,
            )
        )
    return out


def label_of(ts_ns: int, zone: str) -> date:
    return datetime.fromtimestamp(ts_ns / NS, ZoneInfo(zone)).date()


def minutes(start_ns: int, end_ns: int) -> range:
    return range(start_ns, end_ns, 60 * NS)


def utc_day(ts_ns: int) -> date:
    return date(1970, 1, 1) + timedelta(days=ts_ns // (86_400 * NS))
