"""Invoice Memo/Description rendering service (single source of truth).

Template text may use only these placeholders::

    {start_date} {end_date} {month} {year} {project_name} {client_name}

Parsing uses ``string.Formatter().parse`` (no eval, no attribute access, no
indexing, no format specs). ``{{`` / ``}}`` produce literal braces.

Date style matches the existing QB020 sample / ``billing.build_description``:
``Support - October 01 - October 31, 2026`` (start gets the year only when the
period spans two years, or when there is no end date).
"""
import re
from string import Formatter

ALLOWED_PLACEHOLDERS = ('start_date', 'end_date', 'month', 'year', 'project_name', 'client_name')

TYPE_TEMPLATE, TYPE_CUSTOM, TYPE_BLANK = 'template', 'custom', 'blank'


class TemplateSyntaxError(ValueError):
    pass


def parse_fields(text):
    """Return the placeholder names used in ``text``; raise TemplateSyntaxError with a clear message."""
    names = []
    try:
        parsed = list(Formatter().parse(text or ''))
    except ValueError as exc:
        raise TemplateSyntaxError(
            f'Unbalanced braces ({exc}). Use {{placeholder}} for values or {{{{ }}}} for a literal brace.') from None
    allowed = ', '.join('{%s}' % n for n in ALLOWED_PLACEHOLDERS)
    for _literal, field, spec, conv in parsed:
        if field is None:
            continue
        if field == '':
            raise TemplateSyntaxError(f'Empty placeholder "{{}}". Allowed placeholders: {allowed}.')
        if conv or spec or any(ch in field for ch in '.[]'):
            raise TemplateSyntaxError(
                f'Placeholder "{{{field}{"!" + conv if conv else ""}{":" + spec if spec else ""}}}" is not allowed: '
                f'formatting, attributes and indexing are not supported. Allowed placeholders: {allowed}.')
        if field not in ALLOWED_PLACEHOLDERS:
            raise TemplateSyntaxError(f'Unsupported placeholder "{{{field}}}". Allowed placeholders: {allowed}.')
        names.append(field)
    return names


def validate_template_text(text):
    """Django-style validator."""
    from django.core.exceptions import ValidationError
    try:
        parse_fields(text)
    except TemplateSyntaxError as exc:
        raise ValidationError(str(exc)) from None


def _fmt_dates(start, end):
    s = e = ''
    if end:
        e = f'{end:%B %d, %Y}'
    if start:
        s = f'{start:%B %d}' if (end and start.year == end.year) else f'{start:%B %d, %Y}'
    return s, e


_SEP = re.compile(r'\s+[-\u2013|/](?=\s|$)')


def _tidy(text):
    """Drop separators left dangling by empty values: 'Support -  - ' -> 'Support'."""
    parts = [p.lstrip() for p in _SEP.split(text)]
    seps = _SEP.findall(text)
    out = ''
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        out = f'{out}{seps[i - 1]} {part}' if out else part
    out = re.sub(r'[ \t]{2,}', ' ', out)
    return re.sub(r'^[\s\-\u2013|/,:]+|[\s\-\u2013|/,:]+$', '', out)


def render_text(text, *, start_date=None, end_date=None, billing_month=None, project_name='', client_name=''):
    """Render template/custom text. Unknown or invalid syntax never leaks: it raises at save time
    and, defensively, renders as plain text with braces removed here."""
    month_src = billing_month or end_date or start_date
    s, e = _fmt_dates(start_date, end_date)
    values = {'start_date': s, 'end_date': e,
              'month': f'{month_src:%B}' if month_src else '', 'year': str(month_src.year) if month_src else '',
              'project_name': project_name or '', 'client_name': client_name or ''}
    try:
        parsed = list(Formatter().parse(text or ''))
    except ValueError:
        return (text or '').replace('{', '').replace('}', '').strip()
    out, empty = [], False
    for literal, field, _spec, _conv in parsed:
        out.append(literal)
        if field is not None:
            v = values.get(field, '')
            empty = empty or v == ''
            out.append(v)
    result = ''.join(out)
    return _tidy(result) if empty else result.strip()


def project_default_description(project, *, start_date=None, end_date=None, billing_month=None):
    """The Memo/Description a new invoice line for ``project`` starts with."""
    if project is None:
        return ''
    kind = project.invoice_description_type
    if kind == TYPE_TEMPLATE and project.description_template_id:
        text = project.description_template.template_text
    elif kind == TYPE_CUSTOM:
        text = project.description
    else:
        return ''
    return render_text(text, start_date=start_date, end_date=end_date, billing_month=billing_month,
                       project_name=project.name, client_name=project.client.name if project.client_id else '')[:240]
