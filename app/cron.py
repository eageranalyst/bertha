"""Standard five-field cron expressions, evaluated in a time zone.

    minute  hour  day-of-month  month  day-of-week
    0       2     *             *      *            -> 02:00 every day

Each field takes *, a number, a range (1-5), a list (1,3,5), a step (*/15,
0-30/10), and month or day names (jan, mon). Day-of-week 0 and 7 are Sunday.
As in standard cron, when both day fields are restricted, a day matches if
either does.

Times are worked out in the schedule's own time zone, so "02:00 nightly"
stays at 02:00 across daylight-saving changes, and returned in UTC for
storage. A wall-clock time skipped by a spring-forward change doesn't run
that day; one repeated by a fall-back change runs once.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
DAYS = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
FIELDS = (("minute", 0, 59, {}), ("hour", 0, 23, {}), ("day of month", 1, 31, {}),
          ("month", 1, 12, MONTHS), ("day of week", 0, 7, DAYS))
SEARCH_DAYS = 366 * 5


class CronError(ValueError):
    pass


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    days_restricted: bool
    weekdays_restricted: bool

    def day_matches(self, day: datetime) -> bool:
        if day.month not in self.months:
            return False
        dom = day.day in self.days
        dow = (day.isoweekday() % 7) in self.weekdays
        if self.days_restricted and self.weekdays_restricted:
            return dom or dow
        if self.days_restricted:
            return dom
        if self.weekdays_restricted:
            return dow
        return True


def _value(token: str, names: dict[str, int], label: str) -> int:
    token = token.strip().lower()
    if token in names:
        return names[token]
    if not token.isdigit():
        raise CronError(f"{label}: '{token}' isn't a number")
    return int(token)


def _field(text: str, label: str, low: int, high: int, names: dict[str, int]) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"{label}: step '{step_text}' must be a positive number")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _value(a, names, label), _value(b, names, label)
        else:
            start = _value(part, names, label)
            end = high if step > 1 else start
        if not (low <= start <= high and low <= end <= high) or start > end:
            raise CronError(f"{label}: '{part}' is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


def parse(expression: str) -> Cron:
    parts = expression.split()
    if len(parts) != 5:
        raise CronError("a cron expression has five fields: minute hour day-of-month month day-of-week")
    parsed = [_field(text, *spec) for text, spec in zip(parts, FIELDS)]
    weekdays = frozenset(0 if d == 7 else d for d in parsed[4])
    return Cron(parsed[0], parsed[1], parsed[2], parsed[3], weekdays,
                days_restricted=parts[2] != "*", weekdays_restricted=parts[4] != "*")


def next_run(expression: str, tz_name: str, after: datetime) -> datetime:
    """The first time strictly after `after` (any time zone) that the
    expression matches in `tz_name`, as an aware UTC datetime."""
    cron = parse(expression)
    zone = ZoneInfo(tz_name)
    local = after.astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = local.replace(hour=0, minute=0, tzinfo=None)
    first_day = True
    for _ in range(SEARCH_DAYS):
        if cron.day_matches(day):
            for hour in sorted(cron.hours):
                for minute in sorted(cron.minutes):
                    wall = day.replace(hour=hour, minute=minute)
                    if first_day and wall < local.replace(tzinfo=None):
                        continue
                    candidate = wall.replace(tzinfo=zone)
                    # A wall time that doesn't exist (spring forward) doesn't
                    # survive the round trip through UTC; skip it.
                    if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != wall:
                        continue
                    return candidate.astimezone(timezone.utc)
        day += timedelta(days=1)
        first_day = False
    raise CronError("this expression never matches")


def upcoming(expression: str, tz_name: str, after: datetime, count: int = 5) -> list[datetime]:
    times = []
    for _ in range(count):
        after = next_run(expression, tz_name, after)
        times.append(after)
    return times
