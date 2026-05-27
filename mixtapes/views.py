from __future__ import annotations

from collections import Counter

from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Window
from django.db.models.functions import Coalesce, RowNumber
from django.shortcuts import get_object_or_404, render

from .models import Mixtape, Track, TrackMetadata


def latest(request):
    latest_by_series = (
        Mixtape.objects.exclude(series__isnull=True)
        .exclude(series="")
        .annotate(sort_date=Coalesce("release_date", "month"))
        .annotate(
            series_rank=Window(
                expression=RowNumber(),
                partition_by=[F("series")],
                order_by=[F("sort_date").desc(nulls_last=True), F("id").desc()],
            )
        )
        .filter(series_rank=1)
        .annotate(track_count=Count("tracks"))
        .order_by("-sort_date", "series")
        .prefetch_related("tracks__metadata")
    )
    recent = (
        Mixtape.objects.annotate(sort_date=Coalesce("release_date", "month"), track_count=Count("tracks"))
        .order_by("-sort_date", "-id")
        .prefetch_related("tracks__metadata")[:20]
    )
    return render(
        request,
        "mixtapes/latest.html",
        {
            "latest_by_series": latest_by_series,
            "recent": recent,
            "series": series_list(),
        },
    )


def mixtape_list(request):
    query = request.GET.get("q", "").strip()
    series = request.GET.get("series", "").strip()
    mixes = Mixtape.objects.annotate(sort_date=Coalesce("release_date", "month"), track_count=Count("tracks")).order_by(
        "-sort_date", "-id"
    )
    if query:
        mixes = mixes.filter(
            Q(title__icontains=query)
            | Q(uploader__icontains=query)
            | Q(description__icontains=query)
            | Q(tracks__title__icontains=query)
            | Q(tracks__artist__icontains=query)
        ).distinct()
    if series:
        mixes = mixes.filter(series=series)
    page = Paginator(mixes, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "mixtapes/list.html",
        {"page": page, "query": query, "selected_series": series, "series": series_list()},
    )


def mixtape_detail(request, pk):
    mixtape = get_object_or_404(
        Mixtape.objects.prefetch_related("tracks__metadata"),
        pk=pk,
    )
    tracks = list(mixtape.tracks.all())
    return render(
        request,
        "mixtapes/detail.html",
        {
            "mixtape": mixtape,
            "tracks": tracks,
            "metadata_sentence": mixtape_metadata_sentence(tracks),
        },
    )


def track_search(request):
    query = request.GET.get("q", "").strip()
    series = request.GET.get("series", "").strip()
    genre = request.GET.get("genre", "").strip()
    label = request.GET.get("label", "").strip()
    key = request.GET.get("key", "").strip()
    bpm = request.GET.get("bpm", "").strip()
    tracks = (
        Track.objects.select_related("mixtape")
        .annotate(mixtape_sort_date=Coalesce("mixtape__release_date", "mixtape__month"))
        .prefetch_related("metadata")
        .order_by("-mixtape_sort_date", "position")
    )
    if query:
        tracks = tracks.filter(
            Q(title__icontains=query)
            | Q(artist__icontains=query)
            | Q(mixtape__title__icontains=query)
            | Q(metadata__genre__icontains=query)
            | Q(metadata__label__icontains=query)
        ).distinct()
    if series:
        tracks = tracks.filter(mixtape__series=series)
    if genre:
        tracks = tracks.filter(metadata__source="beatport", metadata__genre=genre)
    if label:
        tracks = tracks.filter(metadata__source="beatport", metadata__label=label)
    if key:
        tracks = tracks.filter(metadata__source="beatport", metadata__musical_key=key)
    if bpm:
        tracks = tracks.filter(metadata__source="beatport", metadata__bpm=bpm)
    page = Paginator(tracks, 100).get_page(request.GET.get("page"))
    return render(
        request,
        "mixtapes/tracks.html",
        {
            "page": page,
            "query": query,
            "selected_series": series,
            "selected_genre": genre,
            "selected_label": label,
            "selected_key": key,
            "selected_bpm": bpm,
            "series": series_list(),
        },
    )


def series_list() -> list[str]:
    return list(
        Mixtape.objects.exclude(series__isnull=True)
        .exclude(series="")
        .order_by("series")
        .values_list("series", flat=True)
        .distinct()
    )


def beatport_metadata(track: Track) -> TrackMetadata | None:
    return next((item for item in track.metadata.all() if item.source == "beatport"), None)


def track_metadata_parts(track: Track) -> list[str]:
    metadata = beatport_metadata(track)
    if not metadata:
        return []
    return [value for value in [metadata.bpm and f"{metadata.bpm} BPM", metadata.musical_key, metadata.genre, metadata.label] if value]


def track_metadata_chips(track: Track) -> list[dict[str, str]]:
    metadata = beatport_metadata(track)
    if not metadata:
        return []
    chips = []
    if metadata.bpm:
        chips.append({"label": f"{metadata.bpm} BPM", "filter": "bpm", "value": metadata.bpm})
    if metadata.musical_key:
        chips.append({"label": metadata.musical_key, "filter": "key", "value": metadata.musical_key})
    if metadata.genre:
        chips.append({"label": metadata.genre, "filter": "genre", "value": metadata.genre})
    if metadata.label:
        chips.append({"label": metadata.label, "filter": "label", "value": metadata.label})
    return chips


def beatport_url(track: Track) -> str:
    metadata = beatport_metadata(track)
    if metadata and metadata.source_url:
        return metadata.source_url
    query = f"{track.artist or ''} {track.title}".strip().replace(" ", "+")
    return f"https://www.beatport.com/search?q={query}"


def mixtape_metadata_sentence(tracks: list[Track]) -> str:
    metadata = [beatport_metadata(track) for track in tracks]
    metadata = [item for item in metadata if item]
    bpms = [int(item.bpm) for item in metadata if item.bpm and item.bpm.isdigit()]
    keys = [item.musical_key for item in metadata if item.musical_key]
    genres = [item.genre for item in metadata if item.genre]
    phrases = []
    if bpms:
        phrases.append(f"{tempo_description(bpms)} {min(bpms)}-{max(bpms)} BPM")
    if genres:
        phrases.append(f"leaning toward {dominant_value(genres)}")
    if keys:
        phrases.append(f"often in {dominant_value(keys)}")
    if not phrases:
        return ""
    return f"A {', '.join(phrases)} mix."


def tempo_description(bpms: list[int]) -> str:
    average = sum(bpms) / len(bpms)
    if average < 118:
        return "downtempo"
    if average < 124:
        return "groovy"
    if average < 132:
        return "driving"
    return "high-energy"


def dominant_value(values: list[str]) -> str:
    return Counter(values).most_common(1)[0][0]
