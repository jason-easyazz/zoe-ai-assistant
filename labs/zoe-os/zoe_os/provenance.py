"""Owner-aware purge selection for request-derived caches outside the spine."""
from .spine import forget_affects


def erased_request(notice, seq, owner):
    if notice.payload.get('scope') in {'person', 'seqs', 'entity', 'topic'} and \
            'count' in notice.payload and 'seqs' in notice.payload:
        return seq in notice.payload['seqs']
    # A whole-person notice's watermark applies only to that person's own rows.
    # Cross-owner effects must match the explicit, chunked causal closure instead.
    if notice.payload.get('scope') == 'person' and owner != notice.payload.get('subject'):
        return seq in notice.payload.get('seqs', ())
    return forget_affects(notice, seq)


def unresolved_person(notice, ctx) -> bool:
    """A whole-person notice whose subject this consumer cannot name.

    The notice omits the subject by design. In process, ``Context.forgotten_subject``
    resolves its keyed hash; a remote ``Client`` cannot. Such a notice must be treated as
    "it may be anyone", never as "nobody": see ``still_live``."""
    if notice.payload.get('scope') != 'person' or notice.payload.get('subject'):
        return False
    resolve = getattr(ctx, 'forgotten_subject', None)
    return resolve is None or resolve(notice) is None


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
