"""Register the TrueType fonts used by the reference invoice (Trebuchet MS, Arial).

Search order: $QB_FONT_DIR, <project>/fonts, C:\\Windows\\Fonts, common Linux/mac dirs.
If a font file is missing the built-in Helvetica equivalent is used, so the PDF
still renders (metrics differ slightly).
"""
import os
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .layout import FONT_FILES

_resolved = {}


def _font_dirs():
    dirs = []
    if os.environ.get('QB_FONT_DIR'):
        dirs.append(Path(os.environ['QB_FONT_DIR']))
    dirs.append(Path(__file__).resolve().parents[2] / 'fonts')
    windir = os.environ.get('WINDIR', r'C:\Windows')
    dirs += [Path(windir) / 'Fonts', Path('/usr/share/fonts/truetype/msttcorefonts'),
             Path('/Library/Fonts'), Path('/System/Library/Fonts/Supplemental')]
    return dirs


def _find(filename):
    for d in _font_dirs():
        for cand in (filename, filename.capitalize(), filename.upper()):
            p = d / cand
            if p.is_file():
                return p
    return None


def font(name):
    """Return the ReportLab font name to use for a logical font name."""
    if name in _resolved:
        return _resolved[name]
    filename, fallback = FONT_FILES[name]
    rl_name = 'QB-' + name
    path = _find(filename)
    if path:
        try:
            pdfmetrics.registerFont(TTFont(rl_name, str(path)))
            _resolved[name] = rl_name
            return rl_name
        except Exception:  # corrupt / unsupported font file
            pass
    _resolved[name] = fallback
    return fallback


def using_fallbacks():
    return [n for n in FONT_FILES if font(n) == FONT_FILES[n][1]]
