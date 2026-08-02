"""The one part of retrieval that depends on somebody else's contract.

`LoogleSource` reads a JSON shape Hardy does not control: `hits`, each with a
`name` and a `type` that carries the binders and proposition *without* the name.
Getting that wrong is not a crash -- it is a plausible-looking signature with
two colons in it, handed to a model as if Lean had printed it. Unit tests pin
the parser against a recorded shape, which cannot notice the day the shape
changes; this one asks the real service.

Off by default, because the hermetic suite must not depend on a network or on a
service being up: set HARDY_LOOGLE_LIVE=1 to run it.
"""

from __future__ import annotations

import os

import pytest

from hardy.retrieval import LoogleSource, RetrievalTransportError


@pytest.mark.live
def test_the_public_loogle_still_answers_the_shape_the_parser_reads() -> None:
    if not os.environ.get('HARDY_LOOGLE_LIVE'):
        pytest.skip('set HARDY_LOOGLE_LIVE=1 to query the public Loogle instance')

    source = LoogleSource()
    try:
        found = source.search('Nat.add_comm', 5)
    except RetrievalTransportError as error:
        # The service being unreachable is not a defect. A response Hardy
        # cannot read is exactly the drift this test exists to catch, so that
        # one is deliberately *not* caught -- skipping on it would make the
        # test unable to fail for the only reason it was written.
        pytest.skip(f'Loogle did not answer: {error}')

    assert found, 'Loogle returned no usable hits for a name it certainly holds'
    first = next(record for record in found if record.name == 'Nat.add_comm')
    # Rendered the way Lean prints a `#check`, which is what the rest of Hardy's
    # declaration records look like -- not `name : type`.
    assert first.signature.startswith('Nat.add_comm (')
    assert not first.signature.startswith('Nat.add_comm : ')
    assert first.source_file
    assert not source.identity.pinned
