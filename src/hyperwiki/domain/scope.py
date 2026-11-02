"""Query scope: which part of the library a request may draw from."""

from typing import Self

from pydantic import model_validator

from hyperwiki.domain.common import Contract, ZoteroKey


class Scope(Contract):
    """Filters that bound retrieval.

    An item is in scope when it matches every non-empty filter kind, and any value within a
    kind. Matching is implemented with retrieval in M1.
    """

    item_keys: frozenset[ZoteroKey] = frozenset()
    collection_keys: frozenset[ZoteroKey] = frozenset()
    tags: frozenset[str] = frozenset()
    year_from: int | None = None
    year_to: int | None = None

    @model_validator(mode="after")
    def _check_years(self) -> Self:
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from must not be after year_to")
        return self

    @property
    def is_whole_library(self) -> bool:
        return not (
            self.item_keys
            or self.collection_keys
            or self.tags
            or self.year_from is not None
            or self.year_to is not None
        )
