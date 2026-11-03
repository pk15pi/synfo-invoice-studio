from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def in_list(value, seq):
    return value in seq
