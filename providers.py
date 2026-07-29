from abc import ABC, abstractmethod
from models import Course


class CourseProvider(ABC):
    """A source of course data for one university.

    Every data source (a future NYU scraper, Schedge, another school)
    implements this same interface, so nothing downstream needs to know
    where the data came from.
    """

    @abstractmethod
    def get_courses(self, term: str, course_codes: list[str]) -> list[Course]:
        """Return Course objects for the given term and course codes."""
        ...


class FixtureProvider(CourseProvider):
    """Serves hand-built course data. No network, no scraping, fully
    deterministic, which is exactly what we want for building and testing."""

    def __init__(self, courses: list[Course]):
        self._by_code = {c.code: c for c in courses}

    def get_courses(self, term: str, course_codes: list[str]) -> list[Course]:
        return [self._by_code[c] for c in course_codes if c in self._by_code]