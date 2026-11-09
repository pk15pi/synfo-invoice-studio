"""Tiny inline-SVG icon set (no CDN, works offline). Usage: {% load ui_icons %}{% icon 'edit' %}"""
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# 24x24 stroke paths (Feather-style, drawn with currentColor).
PATHS = {
    'edit': '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    'trash': '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/>',
    'deactivate': '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    'activate': '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.8"/>',
    'mail': '<path d="M4 6h16v12H4z"/><path d="M4 6l8 7 8-7"/>',
    'view': '<path d="M1.5 12S5.5 5 12 5s10.5 7 10.5 7-4 7-10.5 7S1.5 12 1.5 12Z"/><circle cx="12" cy="12" r="3"/>',
    'download': '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 21h16"/>',
    'pdf': '<path d="M14 3H6v18h12V7Z"/><path d="M14 3v4h4"/><path d="M9 13h6M9 17h4"/>',
    'replace': '<path d="M20 7H8a4 4 0 0 0 0 8"/><path d="M16 3l4 4-4 4"/><path d="M4 17h12a4 4 0 0 0 0-8" opacity=".0"/><path d="M8 21l-4-4 4-4"/><path d="M4 17h12"/>',
    'history': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'remove': '<path d="M6 6l12 12M18 6L6 18"/>',
    'update': '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 3v5h5"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 21v-5h-5"/>',
    'adjust': '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
}


@register.simple_tag
def icon(name, size=16):
    body = PATHS.get(name, PATHS['view'])
    return mark_safe(
        f'<svg class="ico ico-{name}" width="{int(size)}" height="{int(size)}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true" focusable="false">{body}</svg>')
