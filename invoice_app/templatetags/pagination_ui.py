"""{% paginate rows as page_rows %} ... {% pager page_rows %} - shared table pager."""
from django import template
from django.core.paginator import Page
from django.template.loader import render_to_string

from invoice_app.pagination import PAGE_SIZES, paginate as _paginate

register = template.Library()


class PaginateNode(template.Node):
    def __init__(self, expr, var):
        self.expr, self.var = expr, var

    def render(self, context):
        rows = self.expr.resolve(context, ignore_failures=True)
        request = context.get('request')
        if rows is None:
            rows = []
        if request is None:  # PDFs / emails / plain render_to_string: never paginate
            context[self.var] = rows
            return ''
        if isinstance(rows, Page):  # view already paginated with the shared helper
            if not hasattr(rows, 'qb_prefix'):
                rows.qb_prefix = ''
            context[self.var] = rows
            return ''
        n = getattr(request, '_qb_pager_count', 0) + 1
        request._qb_pager_count = n
        prefix = '' if n == 1 else f't{n}_'
        try:
            context[self.var] = _paginate(request, rows, prefix)
        except TypeError:
            context[self.var] = rows
        return ''


@register.tag
def paginate(parser, token):
    bits = token.split_contents()
    if len(bits) != 4 or bits[2] != 'as':
        raise template.TemplateSyntaxError('Usage: {% paginate rows as name %}')
    return PaginateNode(parser.compile_filter(bits[1]), bits[3])


@register.simple_tag(takes_context=True)
def pager(context, page):
    request = context.get('request')
    if request is None or not isinstance(page, Page):
        return ''
    prefix = getattr(page, 'qb_prefix', '')
    pk, sk = f'{prefix}page', f'{prefix}page_size'
    size = getattr(page, 'qb_page_size', page.paginator.per_page)
    keep = [(k, v) for k in request.GET for v in request.GET.getlist(k) if k not in (pk, sk)]
    base = request.GET.copy()
    base.pop(pk, None)

    def url(n):
        q = base.copy()
        q[pk] = n
        return '?' + q.urlencode()

    num, last = page.number, page.paginator.num_pages
    window = sorted({1, last, *range(max(1, num - 2), min(last, num + 2) + 1)})
    links, prev = [], 0
    for n in window:
        if prev and n - prev > 1:
            links.append(None)
        links.append((n, url(n)))
        prev = n
    return render_to_string('invoice_app/_pager.html', dict(
        page=page, pk=pk, sk=sk, size=size, sizes=PAGE_SIZES, keep=keep, links=links,
        prev_url=url(page.previous_page_number()) if page.has_previous() else '',
        next_url=url(page.next_page_number()) if page.has_next() else '',
        total=page.paginator.count))