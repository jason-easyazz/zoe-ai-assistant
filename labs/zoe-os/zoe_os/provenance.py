"""How a subscriber reads a FORGET notice: the ONE path, for every cache outside the spine.

A whole-person notice carries no subject (only a keyed hash); a scoped notice names it.
``erased`` resolves the subject itself, through the consumer's context, so no handler reads
a notice's subject or has to remember to resolve it:

* the explicit erased-request list reaches every owner's state;
* a whole-person notice ALSO reaches every state of the resolved member up to its watermark,
  including state whose source event retention had already removed (it is in no list);
* a whole-person notice the consumer cannot resolve (a remote ``Client``, an unknown hash)
  may be about anyone: only state whose source is still readable is kept (``still_live``).

``subject_of`` is the one place a notice's subject is read (kernel-side resolution included).
"""
from __future__ import annotations

from typing import Callable, Iterable, Hashable

from .spine import forget_affects


def subject_of(notice, people: Iterable[str] = (), owner: Callable[[str], bytes] | None = None):
    """The member a notice is about: named on a scoped notice, else the person among ``people``
    whose keyed hash matches (kernel-side only: ``owner`` needs the kernel's key), else None."""
    named = notice.payload.get('subject')
    if named or owner is None:
        return named or None
    wanted = notice.payload.get('subject_hash')
    return next((p for p in people if owner(p).hex() == wanted), None) if wanted else None


def _resolve(notice, ctx):
    named = subject_of(notice)
    if named:
        return named
    resolve = getattr(ctx, 'forgotten_subject', None)
    return resolve(notice) if resolve is not None else None


def _listed(notice, seq: int) -> bool:
    """Is ``seq`` in the notice's explicit erased list (or, for a legacy notice, its old scope)?"""
    p = notice.payload
    if p.get('scope') == 'person' or (p.get('scope') in {'seqs', 'entity', 'topic'} and 'seqs' in p):
        return seq in p.get('seqs', ())      # a person's watermark is for their own state only
    return forget_affects(notice, seq)


def unresolved_person(notice, ctx) -> bool:
    """A whole-person notice whose subject this consumer cannot name."""
    return notice.payload.get('scope') == 'person' and _resolve(notice, ctx) is None


def erased(notice, ctx, items: Iterable[tuple[Hashable, str | None, Iterable[int]]], *,
           kinds) -> set:
    """The keys of the state a forget notice reaches. ``items`` are (key, owner, source seqs)
    for everything the consumer holds; ``kinds`` are what it reads (for the unresolved rule)."""
    items = [(key, owner, tuple(s for s in seqs if isinstance(s, int))) for key, owner, seqs in items]
    whole = notice.payload.get('scope') == 'person'
    subject = _resolve(notice, ctx) if whole else None
    upto = notice.payload.get('upto', float('inf'))
    gone = {key for key, owner, seqs in items
            if any(_listed(notice, s) for s in seqs) or
            (subject is not None and owner == subject and any(s <= upto for s in seqs))}
    if whole and subject is None:
        rest = [(key, seqs) for key, _owner, seqs in items if key not in gone]
        keep = still_live(ctx, kinds, {s for _key, seqs in rest for s in seqs})
        gone |= {key for key, seqs in rest if not set(seqs) & keep}
    return gone


def still_live(ctx, kinds, held) -> set[int]:
    """Of the source sequence numbers a consumer holds state for, the ones it can still read.

    After an unresolved whole-person notice, state whose source is still live cannot be the
    forgotten member's (their events are already erased) and is kept; everything else is
    dropped, whoever it belonged to. One replay of the consumer's own reads from its oldest
    held source, so the work follows what it holds, not the whole log. A consumer that cannot
    replay keeps nothing (fail closed)."""
    held = set(held)
    live = getattr(ctx, 'live_seqs', None)
    if not held or live is None:
        return set()
    return live(kinds, min(held) - 1) & held
