from django.contrib import admin

from .models import Mixtape, Track, TrackMetadata


class TrackInline(admin.TabularInline):
    model = Track
    extra = 0
    fields = ("position", "cue_seconds", "artist", "title", "raw_text")
    show_change_link = True


class TrackMetadataInline(admin.TabularInline):
    model = TrackMetadata
    extra = 0
    fields = ("source", "source_url", "bpm", "musical_key", "camelot_key", "genre", "label", "release_date", "confidence")


@admin.register(Mixtape)
class MixtapeAdmin(admin.ModelAdmin):
    list_display = ("title", "series", "release_date", "month", "uploader", "track_count")
    list_filter = ("series", "uploader")
    search_fields = ("title", "uploader", "description", "soundcloud_url", "tracklist_url")
    fieldsets = (
        (None, {"fields": ("title", "series", "uploader", "month", "release_date")}),
        ("SoundCloud", {"fields": ("soundcloud_url", "artwork_url", "soundcloud_embed_html")}),
        ("Tracklist", {"fields": ("tracklist_url", "description", "created_at")}),
    )
    readonly_fields = ("created_at",)
    inlines = [TrackInline]

    def track_count(self, obj: Mixtape) -> int:
        return obj.tracks.count()


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ("__str__", "mixtape", "position")
    list_filter = ("mixtape__series",)
    search_fields = ("artist", "title", "raw_text", "mixtape__title")
    inlines = [TrackMetadataInline]


@admin.register(TrackMetadata)
class TrackMetadataAdmin(admin.ModelAdmin):
    list_display = ("track", "source", "bpm", "musical_key", "camelot_key", "genre", "label", "release_date")
    list_filter = ("source", "camelot_key", "genre", "label")
    search_fields = ("track__artist", "track__title", "source_url", "source_track_id", "genre", "label")
