class CombinedInstructors:
    """Merges instructor lookups from multiple department sources into one,
    since NYUProvider takes a single instructors object. Each source keys
    off its own subject codes, so there's no overlap to resolve in practice."""

    def __init__(self, *sources):
        self._sources = sources

    def instructors_for(self, term: str) -> dict[tuple[str, str], str]:
        merged: dict[tuple[str, str], str] = {}
        for source in self._sources:
            merged.update(source.instructors_for(term))
        return merged
