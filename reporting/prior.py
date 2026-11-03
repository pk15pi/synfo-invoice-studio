"""Shared prior-period filter for every expense-based report, dashboard, builder dataset and the ledger P&L.

?prior=include (default) | exclude | only.  The mode is carried in Period.params (so drill-down links keep it) and
applied through a context variable that reports.build / builder.build / the dashboard view set for the duration of
one build, so every services.* query (company expenses, claims, vendor payments, reimbursements) honours it
without changing each report signature.  The period picker label is not affected."""
from contextlib import contextmanager
from contextvars import ContextVar

INCLUDE, EXCLUDE, ONLY = 'include', 'exclude', 'only'
MODES = [(INCLUDE, 'Include prior-period'), (EXCLUDE, 'Exclude prior-period'), (ONLY, 'Only prior-period')]
LABELS = dict(MODES)
_mode = ContextVar('qb_prior_mode', default=INCLUDE)


def mode(params):
    v = str((params or {}).get('prior') or '').strip().lower()
    return v if v in (EXCLUDE, ONLY) else INCLUDE


def current():
    return _mode.get()


@contextmanager
def scope(m):
    tok = _mode.set(m if m in (INCLUDE, EXCLUDE, ONLY) else INCLUDE)
    try:
        yield
    finally:
        _mode.reset(tok)


def apply(qs, prefix='', m=None):
    m = m or current()
    if m == EXCLUDE:
        return qs.filter(**{prefix + 'is_prior_period': False})
    if m == ONLY:
        return qs.filter(**{prefix + 'is_prior_period': True})
    return qs
