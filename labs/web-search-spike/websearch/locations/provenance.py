"""Store attribution — the sentence a price must be able to say about itself.

`chain.FetchResult.provenance` already answers *how* a page was read (which
tier, what refused, how long). It cannot answer *who the price is for*, and on
a retail price that is the load-bearing half: BWS's national number and BWS
Geraldton's shelf number are both "a price from bws.com.au read by httpx".

`StoreAttribution` is that missing half. It is attached to every located read,
including — especially — the ones where no store could be selected, because the
only dangerous outcome is a store-less price that does not admit it.

WHY `confident` IS COMPUTED, NOT PASSED IN
------------------------------------------
An earlier shape let the caller pass `confidence="high"`. That is the model
grading its own homework in miniature: whichever code path was most eager would
declare itself confident. So confidence is DERIVED from the method, in one
place, and `store-less` can never be confident no matter who constructs it.
`test_store_less_is_never_confident` pins that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The price was fetched from an endpoint that took an explicit store id.
#: Strongest attribution available without standing in the shop.
METHOD_API = "api"

#: A store was selected by driving the site's own picker; the price came from a
#: session carrying that selection.
METHOD_PICKER = "picker-session"

#: NO store was selected. The price is whatever the site serves an anonymous,
#: location-less client. LOW CONFIDENCE, always.
METHOD_NONE = "store-less"

_METHODS = (METHOD_API, METHOD_PICKER, METHOD_NONE)


@dataclass(frozen=True, slots=True)
class StoreContext:
    """Where the operator is asking about. The INPUT to a recipe."""

    suburb: str
    postcode: str
    state: str = ""
    country: str = "AU"

    def label(self) -> str:
        bits = [self.suburb, self.state, self.postcode]
        return " ".join(b for b in bits if b)

    def matches(self, text: str) -> bool:
        """Does `text` mention this locality at all?

        Used as a corroboration check on a page we believe is store-scoped: if
        a selection succeeded, the suburb or postcode is almost always printed
        somewhere in the chrome. Absence is a WARNING, not a verdict — some
        sites show only the store's trading name.
        """
        low = (text or "").lower()
        return self.suburb.lower() in low or self.postcode in low


#: The operator's town. This spike exists for exactly this locality.
GERALDTON = StoreContext(suburb="Geraldton", postcode="6530", state="WA")


@dataclass(slots=True)
class StoreAttribution:
    """Which store a price is for, and how sure we are allowed to be."""

    #: `api` | `picker-session` | `store-less`
    method: str
    #: Human label for the store the price belongs to, when known.
    store: str = ""
    #: The retailer's own id for that store, when we have one.
    store_id: str = ""
    #: Locality the caller ASKED for (may differ from `store` if we fell back).
    asked_for: str = ""
    #: Free-text: which endpoint, which cookie, which caveat.
    detail: str = ""
    #: Corroboration: did the fetched text actually mention the locality?
    locality_in_text: bool | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.method not in _METHODS:
            raise ValueError(f"unknown attribution method {self.method!r}; expected one of {_METHODS}")
        if self.method == METHOD_NONE:
            # Belt and braces: the flag is derived (see `confident`), but the
            # WARNING is the thing a human reads, so it is materialised here
            # rather than left to a caller who might not render it.
            msg = (
                "LOW CONFIDENCE: no store was selected — this is the retailer's "
                "store-less/national price and the Geraldton shelf price may differ"
            )
            if msg not in self.warnings:
                self.warnings.insert(0, msg)

    @property
    def confident(self) -> bool:
        """Never True for a store-less read. Derived, never assigned."""
        return self.method in (METHOD_API, METHOD_PICKER)

    def line(self) -> str:
        if self.method == METHOD_NONE:
            return f"store-less ({self.detail or 'no store context available'}) — LOW CONFIDENCE"
        who = self.store or self.asked_for or "unknown store"
        ident = f" [{self.store_id}]" if self.store_id else ""
        return f"{who}{ident} via {self.method}" + (f" ({self.detail})" if self.detail else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "store": self.store,
            "store_id": self.store_id,
            "asked_for": self.asked_for,
            "detail": self.detail,
            "confident": self.confident,
            "locality_in_text": self.locality_in_text,
            "warnings": list(self.warnings),
        }


def store_less(detail: str = "", *, asked_for: str = "") -> StoreAttribution:
    """The honest default. Every path that could not pin a store returns this."""
    return StoreAttribution(method=METHOD_NONE, detail=detail, asked_for=asked_for)
