"""One genre vocabulary for movies and series.

TMDB uses different genre lists for movies and TV (e.g. "Action" vs "Action & Adventure").
Titles keep the raw TMDB names; filtering and display use these unified names.
"""

# raw TMDB name (de-DE) -> unified names; names not listed map to themselves
RAW_TO_UNIFIED: dict[str, list[str]] = {
    "Action & Adventure": ["Action", "Abenteuer"],
    "Sci-Fi & Fantasy": ["Science Fiction", "Fantasy"],
    "War & Politics": ["Krieg & Politik"],
    "Kriegsfilm": ["Krieg & Politik"],
    "Liebesfilm": ["Romantik"],
    "Dokumentarfilm": ["Dokumentation"],
    "Kids": ["Kinder"],
    "Reality": ["Reality-TV"],
    "Talk": ["Talkshow"],
    "News": ["Nachrichten"],
}


def unify(raw_genres: list[str]) -> list[str]:
    result: list[str] = []
    for raw in raw_genres:
        for name in RAW_TO_UNIFIED.get(raw, [raw]):
            if name not in result:
                result.append(name)
    return result


def raw_names_for(unified: str) -> list[str]:
    """All raw TMDB genre names that count as `unified`."""
    raws = [raw for raw, names in RAW_TO_UNIFIED.items() if unified in names]
    if unified not in RAW_TO_UNIFIED:  # the name itself is also a raw TMDB genre
        raws.append(unified)
    return raws
