"""Shared server-side pagination for every record table.

Query params: ``page`` and ``page_size`` (secondary tables on the same page get a
prefix such as ``t2_page`` / ``t2_page_size``). Sizes are fixed; default 30.
"""
from django.core.paginator import Paginator

PAGE_SIZES = (5, 10, 20, 30, 50, 75, 100, 200, 350, 500)
DEFAULT_PAGE_SIZE = 30


def page_size_from(request, prefix=''):
    raw = (request.GET.get(f'{prefix}page_size') or '').strip() if request is not None else ''
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return n if n in PAGE_SIZES else DEFAULT_PAGE_SIZE


def paginate(request, rows, prefix=''):
    """Return a Django Page for ``rows`` (queryset sliced in SQL; lists sliced in Python).

    Bad page numbers fall back safely: non-numbers -> 1, too high -> last page.
    """
    if not hasattr(rows, '__getitem__') or isinstance(rows, dict):
        rows = list(rows.items() if isinstance(rows, dict) else rows)
    if getattr(rows, 'ordered', True) is False:  # unordered queryset -> stable pages
        rows = rows.order_by('pk')
    size = page_size_from(request, prefix)
    page = Paginator(rows, size, allow_empty_first_page=True).get_page(
        request.GET.get(f'{prefix}page') if request is not None else 1)
    page.qb_prefix = prefix
    if request is not None:
        request._qb_pager_count = getattr(request, '_qb_pager_count', 0) + 1
    page.qb_page_size = size
    return page