"""Tiny dependency-free inline SVG charts (no CDN, no JS)."""
from decimal import Decimal
from xml.sax.saxutils import escape

from .core import inr

PALETTE = ['#2F6FDE', '#E8743B', '#19A979', '#945ECF', '#ED4A7B', '#13A4B4', '#BF399E', '#F4B400']


def _short(v, precise=False):
    v = float(v)
    a = abs(v)
    s = f'{v / 1e7:.2f} Cr' if a >= 1e7 else f'{v / 1e5:.2f} L' if a >= 1e5 else f'{v / 1e3:.1f} K' if a >= 1e3 else f'{v:.0f}'
    if precise and a < 1e3:
        s = f'{v:,.2f}'.rstrip('0').rstrip('.')
    return s


def _value_label(value, money):
    if money:
        return inr(Decimal(str(round(float(value or 0), 2))))
    return f'{float(value or 0):,.2f}'.rstrip('0').rstrip('.')


def _empty(title):
    return (f'<svg viewBox="0 0 520 220" class="chart" role="img" aria-label="{escape(title)}">'
            f'<text x="260" y="110" text-anchor="middle" fill="#888" font-size="13">No data for this period</text></svg>')


def bars(labels, series, title='', w=520, h=240, legend=None, money=True):
    """Vertical grouped bars. series: list of lists (same length as labels). Supports negatives."""
    vals = [float(v or 0) for s in series for v in s]
    if not labels or not any(vals):
        return _empty(title)
    top, bot = max(vals + [0]), min(vals + [0])
    rng = (top - bot) or 1
    L, R, T, B = 58, 10, 14 + (16 if legend else 0), 36
    pw, ph = w - L - R, h - T - B
    y = lambda v: T + (top - v) / rng * ph
    gw = pw / len(labels)
    bw = max(2, gw * 0.75 / len(series))
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{escape(title)}">']
    for i in range(5):
        v = bot + rng * i / 4
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#eee"/>'
                   f'<text x="{L - 4}" y="{y(v) + 3:.1f}" text-anchor="end" font-size="9" fill="#666">{_short(v, precise=not money)}</text>')
    out.append(f'<line x1="{L}" x2="{w - R}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#999"/>')
    step = max(1, len(labels) // 12)
    for i, lab in enumerate(labels):
        x0 = L + i * gw + gw * 0.125
        for j, s in enumerate(series):
            v = float(s[i] or 0)
            y1, y2 = sorted((y(v), y(0)))
            col = PALETTE[j % 8] if v >= 0 or len(series) > 1 else '#D64545'
            out.append(f'<rect x="{x0 + j * bw:.1f}" y="{y1:.1f}" width="{bw - 1:.1f}" height="{max(y2 - y1, 0.5):.1f}" '
                       f'fill="{col}"><title>{escape(str(lab))}: {_value_label(v, money)}</title></rect>')
        if i % step == 0:
            out.append(f'<text x="{L + i * gw + gw / 2:.1f}" y="{h - B + 13}" text-anchor="middle" font-size="9" '
                       f'fill="#444">{escape(str(lab))}</text>')
    if legend:
        for j, name in enumerate(legend):
            out.append(f'<rect x="{L + j * 130}" y="2" width="10" height="10" fill="{PALETTE[j % 8]}"/>'
                       f'<text x="{L + j * 130 + 14}" y="11" font-size="10">{escape(name)}</text>')
    out.append('</svg>')
    return ''.join(out)


def line(labels, series, title='', w=520, h=240, legend=None, money=True):
    vals = [float(v or 0) for s in series for v in s]
    if not labels or not any(vals):
        return _empty(title)
    top, bot = max(vals + [0]), min(vals + [0])
    rng = (top - bot) or 1
    L, R, T, B = 58, 10, 14 + (16 if legend else 0), 36
    pw, ph = w - L - R, h - T - B
    y = lambda v: T + (top - v) / rng * ph
    x = lambda i: L + (pw * i / (len(labels) - 1) if len(labels) > 1 else pw / 2)
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{escape(title)}">']
    for i in range(5):
        v = bot + rng * i / 4
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#eee"/>'
                   f'<text x="{L - 4}" y="{y(v) + 3:.1f}" text-anchor="end" font-size="9" fill="#666">{_short(v, precise=not money)}</text>')
    for j, s in enumerate(series):
        pts = ' '.join(f'{x(i):.1f},{y(float(v or 0)):.1f}' for i, v in enumerate(s))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{PALETTE[j % 8]}" stroke-width="2"/>')
        for i, v in enumerate(s):
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(float(v or 0)):.1f}" r="2.5" fill="{PALETTE[j % 8]}">'
                       f'<title>{escape(str(labels[i]))}: {_value_label(v, money)}</title></circle>')
    step = max(1, len(labels) // 12)
    for i, lab in enumerate(labels):
        if i % step == 0:
            out.append(f'<text x="{x(i):.1f}" y="{h - B + 13}" text-anchor="middle" font-size="9" fill="#444">'
                       f'{escape(str(lab))}</text>')
    if legend:
        for j, name in enumerate(legend):
            out.append(f'<rect x="{L + j * 130}" y="2" width="10" height="10" fill="{PALETTE[j % 8]}"/>'
                       f'<text x="{L + j * 130 + 14}" y="11" font-size="10">{escape(name)}</text>')
    out.append('</svg>')
    return ''.join(out)


def hbars(items, title='', w=520, money=True):
    """items: [(label, value)] horizontal bars."""
    items = [(l, float(v or 0)) for l, v in items if v]
    if not items:
        return _empty(title)
    h = 20 + 22 * len(items)
    mx = max(abs(v) for _, v in items) or 1
    L = 150
    pw = w - L - 90
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{escape(title)}">']
    for i, (lab, v) in enumerate(items):
        yy = 10 + i * 22
        bw = abs(v) / mx * pw
        lab = str(lab)
        out.append(f'<text x="{L - 6}" y="{yy + 12}" text-anchor="end" font-size="10" fill="#333">'
                   f'{escape(lab[:24] + ("..." if len(lab) > 24 else ""))}</text>'
                   f'<rect x="{L}" y="{yy}" width="{max(bw, 1):.1f}" height="16" fill="{PALETTE[i % 8]}"><title>'
                   f'{escape(lab)}</title></rect><text x="{L + bw + 4:.1f}" y="{yy + 12}" font-size="10" fill="#333">'
                   f'{_short(v) if money else int(v)}</text>')
    out.append('</svg>')
    return ''.join(out)
