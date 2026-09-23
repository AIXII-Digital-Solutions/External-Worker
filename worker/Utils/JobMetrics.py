"""What a job actually cost in database round trips, attached to the line that times it.

THE PROBLEM THIS SOLVES. `@performance_timer` said how long a job took and nothing about why.
That is the wrong half: this worker is a network away from its database, so a slow job is usually
one that asked the database too many times, and the difference between one query and four hundred
is invisible from the outside. The FR24 coverage planner ran a SELECT per registration — a number
that only shows up as "this took a while".

HOW IT ATTACHES. A ContextVar holds one counter per job. SQLAlchemy's `before_cursor_execute` /
`after_cursor_execute` fire on the Engine CLASS, so they cover every engine this process opens,
including ones created after startup; each adds to whatever counter the current context holds, or
to nothing at all when there is none. The listeners are installed once, at import.

WHAT IT COSTS. Two `perf_counter()` calls and an integer add per statement.

Ported from core-api's `Utils/RequestMetrics.py`, which does the same thing per HTTP request.
"""
import time
from contextvars import ContextVar
from typing import Optional

from sqlalchemy import event
from sqlalchemy.engine import Engine

_current: ContextVar[Optional["JobCost"]] = ContextVar("job_cost", default=None)

# How much of a statement to keep when it turns out to be the slow one. Enough to recognise the
# query, short enough that a log line stays one line.
_STATEMENT_CHARS = 160


class JobCost:
    """Counters for one job. Plain attributes, mutated in place — this is on the hot path of every
    statement the job runs, and a job's statements run one after another on one task, so a
    dataclass or a lock would both be paying for nothing."""

    __slots__ = ("queries", "db_seconds", "slowest_seconds", "slowest_statement")

    def __init__(self):
        self.queries = 0
        self.db_seconds = 0.0
        self.slowest_seconds = 0.0
        self.slowest_statement = ""

    def record(self, seconds: float, statement: str) -> None:
        self.queries += 1
        self.db_seconds += seconds
        if seconds > self.slowest_seconds:
            self.slowest_seconds = seconds
            self.slowest_statement = " ".join(statement.split())[:_STATEMENT_CHARS]

    def summary(self) -> str:
        """`db=412/38.1s` — the two numbers that explain a slow job, in the order they matter:
        how many waits, and how long they took together."""
        return f"db={self.queries}/{self.db_seconds:.1f}s"


def begin() -> JobCost:
    """Start counting for this job. The context is per-task and dies with it."""
    cost = JobCost()
    _current.set(cost)
    return cost


def current() -> Optional[JobCost]:
    return _current.get()


@event.listens_for(Engine, "before_cursor_execute")
def _before(conn, cursor, statement, parameters, context, executemany):
    # Stamped on the execution context rather than in the ContextVar: `after` receives the same
    # context object, and nothing else is guaranteed to be the same by then.
    context._jobcost_started = time.perf_counter()


@event.listens_for(Engine, "after_cursor_execute")
def _after(conn, cursor, statement, parameters, context, executemany):
    cost = _current.get()
    if cost is None:
        return                      # not inside a timed job — nothing to attach to
    started = getattr(context, "_jobcost_started", None)
    if started is None:
        return
    cost.record(time.perf_counter() - started, statement)
