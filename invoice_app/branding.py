"""Business logo helpers: SVG safety check and the header logo URL."""
import re

_BAD = re.compile(rb'<\s*(script|foreignobject|iframe|object|embed)\b|\son[a-z]+\s*=|javascript:|<!entity|xlink:href\s*=\s*["\'](?!#)|\shref\s*=\s*["\'](?!#)', re.I)


def svg_is_safe(data):
    head = data[:4096].lower()
    if b'<svg' not in head:
        return False
    return not _BAD.search(data)


def logo_url(profile):
    """URL for the header logo, cache-busted by the file name, or '' when none is set / file is missing."""
    f = getattr(profile, 'business_logo', None)
    if not f:
        return ''
    try:
        if not f.storage.exists(f.name):
            return ''
    except Exception:
        return ''
    from django.urls import reverse
    import zlib
    return f"{reverse('business_logo')}?v={zlib.crc32(f.name.encode()):x}"
