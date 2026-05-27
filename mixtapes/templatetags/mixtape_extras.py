from django import template

from mixtapes import views

register = template.Library()


@register.simple_tag
def beatport_url(track):
    return views.beatport_url(track)


@register.simple_tag
def metadata_parts(track):
    return views.track_metadata_parts(track)


@register.simple_tag
def metadata_chips(track):
    return views.track_metadata_chips(track)
