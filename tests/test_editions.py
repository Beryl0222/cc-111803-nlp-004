from dataclasses import replace
from pathlib import Path

import pytest

from nlp_newsroom_correction_chain.desk_cli import load_desk


@pytest.fixture
def desk():
    return load_desk(Path(__file__).parents[1] / 'fixtures' / 'desk.json')


def test_requiring_two_independent_news_desks_blocks_single_quote(desk):
    assert desk.assess('feed-13')[0]['approved']
    assert not desk.assess('feed-14')[0]['approved']
    with pytest.raises(ValueError, match='尚未全部核签'):
        desk.publish('feed-14', 'draft')


def test_quote_must_match_the_speaker_and_exact_edition(desk):
    claim = desk.claims['presence']
    clip = replace(claim.clips[0], speaker='乔骁')
    desk.claims['presence'] = replace(claim, clips=(clip,))
    result = desk.assess('flash-41')[1]
    assert not result['approved']
    assert '引语说话人与原稿不符' in result['issues']
    assert desk.review.archive.latest('dock-17').edition == 2
    assert desk.receipt('20261118-flash')['claims'][0]['clips'][0]['edition'] == 1


def test_previously_published_receipt_survives_a_rejected_live_claim(desk):
    desk.claims['paused'] = replace(desk.claims['paused'], clips=())
    assert not desk.assess('feed-13')[0]['approved']
    receipt = desk.receipt('20261118-feed')
    assert receipt['reviews'][0]['approved']
    receipt['reviews'][0]['desks'].clear()
    assert len(desk.receipt('20261118-feed')['reviews'][0]['desks']) == 2
