from django.db import models
from django.urls import reverse


class Mixtape(models.Model):
    soundcloud_url = models.TextField(unique=True)
    title = models.TextField()
    uploader = models.TextField(blank=True, null=True)
    month = models.TextField(blank=True, null=True)
    release_date = models.TextField(blank=True, null=True)
    series = models.TextField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    tracklist_url = models.TextField(blank=True, null=True)
    artwork_url = models.TextField(blank=True, null=True)
    soundcloud_embed_html = models.TextField(blank=True, null=True)
    created_at = models.TextField()

    class Meta:
        managed = False
        db_table = "mixtapes"
        ordering = ["-release_date", "-month", "-id"]

    def __str__(self) -> str:
        return self.title

    @property
    def display_date(self) -> str:
        return self.release_date or self.month or ""

    def get_absolute_url(self) -> str:
        return reverse("mixtapes:detail", args=[self.id])


class Track(models.Model):
    mixtape = models.ForeignKey(Mixtape, related_name="tracks", on_delete=models.CASCADE)
    position = models.IntegerField(blank=True, null=True)
    cue_seconds = models.IntegerField(blank=True, null=True)
    artist = models.TextField(blank=True, null=True)
    title = models.TextField()
    raw_text = models.TextField()

    class Meta:
        managed = False
        db_table = "tracks"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        if self.artist:
            return f"{self.artist} - {self.title}"
        return self.title

    @property
    def cue(self) -> str:
        if self.cue_seconds is None:
            return ""
        minutes, seconds = divmod(self.cue_seconds, 60)
        return f"[{minutes:02d}:{seconds:02d}]"


class TrackMetadata(models.Model):
    track = models.ForeignKey(Track, related_name="metadata", on_delete=models.CASCADE)
    source = models.TextField()
    source_url = models.TextField(blank=True, null=True)
    source_track_id = models.TextField(blank=True, null=True)
    bpm = models.TextField(blank=True, null=True)
    musical_key = models.TextField(blank=True, null=True)
    genre = models.TextField(blank=True, null=True)
    label = models.TextField(blank=True, null=True)
    release_title = models.TextField(blank=True, null=True)
    release_date = models.TextField(blank=True, null=True)
    confidence = models.TextField(blank=True, null=True)
    raw_json = models.TextField(blank=True, null=True)
    fetched_at = models.TextField()

    class Meta:
        managed = False
        db_table = "track_metadata"
        verbose_name_plural = "track metadata"

    def __str__(self) -> str:
        return f"{self.source}: {self.track}"
