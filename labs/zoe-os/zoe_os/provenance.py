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
