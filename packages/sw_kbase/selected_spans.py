"""Exact selected subspans shared by extraction consumers. No inference."""
import json


def selected_spans(paragraph, maximum=6000):
    from .document_units import fragments
    if paragraph['status'] not in {'reference', 'add', 'add_uncertain'}:
        return
    result = json.loads(paragraph.get('result_json') or '{}')
    votes = result.get('votes', [])
    if paragraph['status'] == 'reference' or not votes:
        spans = [paragraph]
    else:
        spans = []
        previous = paragraph['offset_start']
        for vote in sorted(votes, key=lambda v: v.get('fragment', {}).get('offset_start', -1)):
            fragment = vote.get('fragment', {})
            start, end = fragment.get('offset_start'), fragment.get('offset_end')
            if (type(start) is not int or type(end) is not int or start != previous
                    or not start < end <= paragraph['offset_end']
                    or paragraph['body'][start-paragraph['offset_start']:end-paragraph['offset_start']] != fragment.get('body')):
                raise ValueError('Selected fragment offsets/body are inconsistent')
            previous = end
            if vote.get('decision') != 'redundant':
                spans.append({**paragraph, 'body': fragment['body'], 'offset_start': start, 'offset_end': end,
                    'status': 'add' if vote.get('decision') == 'useful' else 'add_uncertain', 'span_vote': vote})
        if previous != paragraph['offset_end']:
            raise ValueError('Votes do not cover the complete paragraph')
    for span in spans:
        for fragment in fragments(span, maximum):
            yield {**span, **fragment}
