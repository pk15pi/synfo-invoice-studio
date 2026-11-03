"""Report container shared by HTML pages, Excel and PDF exports (so every report exports identically)."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

NT = 'Not tracked'


@dataclass
class Col:
    label: str
    kind: str = 'text'  # text | money | int | num | pct | date


@dataclass
class Table:
    title: str
    cols: list
    rows: list = field(default_factory=list)
    styles: list = field(default_factory=list)  # parallel to rows: '' | 'subtotal' | 'group'
    total: list = None
    note: str = ''
    paginate: bool = False

    def add(self, row, style=''):
        self.rows.append(row)
        self.styles.append(style)

    def sum_total(self, label='Total', start=1):
        tot = [label] + [''] * (len(self.cols) - 1)
        for i, c in enumerate(self.cols):
            if i >= start and c.kind in ('money', 'int'):
                vals = [r[i] for r, s in zip(self.rows, self.styles) if s == '' and isinstance(r[i], (int, Decimal))]
                tot[i] = sum(vals, Decimal('0.00') if c.kind == 'money' else 0)
        self.total = tot
        return tot

    def add_subtotals(self, key_idx, label='Subtotal'):
        """Re-order rows by key column and insert a subtotal row after each group."""
        groups = {}
        for r in self.rows:
            groups.setdefault(text(r[key_idx]), []).append(r)
        self.rows, self.styles = [], []
        for k, rs in groups.items():
            for r in rs:
                self.add(r)
            sub = [''] * len(self.cols)
            sub[0] = f'{label}: {k}'
            for i, c in enumerate(self.cols):
                if c.kind in ('money', 'int') and i != 0:
                    sub[i] = sum((r[i] for r in rs if isinstance(r[i], (int, Decimal))),
                                 Decimal('0.00') if c.kind == 'money' else 0)
            self.add(sub, 'subtotal')


@dataclass
class Report:
    key: str
    title: str
    section: str
    basis: str
    period: object
    cards: list = field(default_factory=list)  # dicts: label, value, kind, prev, change, note, link
    tables: list = field(default_factory=list)
    filters: list = field(default_factory=list)  # (label, value) shown on screen and exports
    notes: list = field(default_factory=list)
    limitations: list = field(default_factory=list)
    landscape: bool = False
    statement: str = ''

    def card(self, label, value, kind='money', prev='_', note='', link=''):
        from .periods import pct_change
        c = dict(label=label, value=value, kind=kind, note=note, link=link, prev=None, change='')
        if prev != '_':
            c['prev'] = prev
            if value is not None and prev is not None and not isinstance(value, str):
                c['change_abs'] = value - prev
                c['change'] = pct_change(value, prev)[1]
        self.cards.append(c)
        return c


def inr(v, blank=''):
    """Indian digit grouping: 12,34,567.89"""
    if v is None:
        return NT
    if v == '' or isinstance(v, str):
        return v if v != '' else blank
    v = Decimal(v).quantize(Decimal('0.01'))
    neg = v < 0
    s = f'{abs(v):.2f}'
    whole, frac = s.split('.')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ','.join(parts + [tail])
    return ('-' if neg else '') + whole + '.' + frac


def text(v, kind='text'):
    if isinstance(v, tuple):
        v = v[0]
    if v is None:
        return NT if kind in ('money', 'pct') else ''
    if kind == 'money':
        return inr(v)
    if kind == 'pct':
        return v if isinstance(v, str) else f'{v:.2f}%'
    if isinstance(v, date):
        return v.strftime('%d %b %Y')
    return str(v)
