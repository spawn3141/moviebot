"""Minimum age from TMDB certifications: German FSK if present, else the US rating converted.

TMDB certifications are community-maintained; FSK is often missing for streaming originals.
"""

FSK_AGES = (0, 6, 12, 16, 18)

# US ratings -> closest FSK age
US_MOVIE = {"G": 0, "PG": 6, "PG-13": 12, "R": 16, "NC-17": 18}
US_TV = {"TV-Y": 0, "TV-G": 0, "TV-Y7": 6, "TV-Y7-FV": 6, "TV-PG": 6, "TV-14": 12, "TV-MA": 16}


def _fsk(value: str) -> int | None:
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits and int(digits) in FSK_AGES else None


def _certifications(details: dict, media_type: str, country: str) -> list[str]:
    if media_type == "movie":
        for r in details.get("release_dates", {}).get("results", []):
            if r.get("iso_3166_1") == country:
                return [d["certification"].strip() for d in r.get("release_dates", [])
                        if d.get("certification", "").strip()]
        return []
    return [r["rating"].strip() for r in details.get("content_ratings", {}).get("results", [])
            if r.get("iso_3166_1") == country and r.get("rating", "").strip()]


def age_rating(details: dict, media_type: str) -> tuple[int, str, str] | None:
    """(age, source, raw) or None. Several entries (cinema, digital, …): the strictest wins."""
    fsk = [(age, raw) for raw in _certifications(details, media_type, "DE")
           if (age := _fsk(raw)) is not None]
    if fsk:
        age, raw = max(fsk)
        return age, "fsk", raw
    table = US_MOVIE if media_type == "movie" else US_TV
    us = [(table[raw.upper()], raw) for raw in _certifications(details, media_type, "US")
          if raw.upper() in table]
    if us:
        age, raw = max(us)
        return age, "us", raw
    return None
