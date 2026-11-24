import json
from pathlib import Path

import pytest

from nlp_newsroom_correction_chain.desk_cli import desk_from_data, load_desk, main

FIXTURES = Path(__file__).parents[1] / 'fixtures'


@pytest.fixture
def base():
    return json.loads((FIXTURES / 'corrections.json').read_text(encoding='utf-8'))


@pytest.fixture
def desk(base):
    return desk_from_data(base)


def with_notices(base, notices, **extra):
    data = json.loads(json.dumps(base))
    data['notices'] = notices
    data.update(extra)
    return desk_from_data(data)


CONFLICTING = [
    {'id': 'n-a', 'kind': 'correction', 'scope': 'fact',
     'wire_id': 'dock-17', 'edition': 1, 'start': 0, 'end': 9,
     'quote': '北岸码头暂停清点。', 'amended': '北岸码头暂停作业。'},
    {'id': 'n-b', 'kind': 'retraction', 'scope': 'fact',
     'wire_id': 'dock-17', 'edition': 1, 'start': 0, 'end': 9,
     'quote': '北岸码头暂停清点。'},
    {'id': 'n-c', 'kind': 'correction', 'scope': 'fact',
     'wire_id': 'dock-17', 'edition': 1, 'start': 13, 'end': 20,
     'quote': '暂停只涉及北案', 'amended': '暂停只涉及北岸'},
]


# ---- 已发稿进入更正工作 ---------------------------------------------

def test_published_receipt_keeps_the_text_that_was_actually_released():
    desk = with_notices(json.loads((FIXTURES / 'corrections.json').read_text()),
                        CONFLICTING)
    receipt = desk.receipt('20261118-flash')
    assert receipt['claims'][0]['clips'][0]['text'] == '北岸码头暂停清点。'
    assert receipt['claims'][0]['clips'][0]['edition'] == 1
    # 更正到达后，当前核签已经改变，但回执仍是刊发当时的内容。
    assert desk.assess('flash-41')[0]['approved'] is False


def test_conflicting_notices_on_one_fragment_block_only_that_statement():
    conflict = with_notices(json.loads((FIXTURES / 'corrections.json').read_text()),
                            CONFLICTING)
    assert conflict.conflicts()[0]['notices'] == ['n-a', 'n-b']
    deliverable = conflict.deliverable('flash-41')
    assert [item['statement'] for item in deliverable['ready']] == ['presence']
    assert [item['statement'] for item in deliverable['held']] == ['paused']
    # 同一原文不同片段的更正互不牵连，专题页仍按自己的判断继续。
    topic = conflict.assess('topic-16')[0]
    assert topic['approved'] is False
    assert '更正推翻原说法' in topic['issues'][0]


def test_worklist_marks_blocked_entries_without_stopping_others():
    conflict = with_notices(json.loads((FIXTURES / 'corrections.json').read_text()),
                            CONFLICTING)
    worklist = {item['slot']: item for item in conflict.correction_worklist()}
    assert worklist['flash-41']['blocked'] is True
    assert worklist['topic-16']['blocked'] is False
    assert worklist['topic-16']['notices'][0]['status'] == 'corrected'


def test_duty_editor_can_dismiss_one_conflicting_notice():
    conflict = with_notices(json.loads((FIXTURES / 'corrections.json').read_text()),
                            CONFLICTING)
    assert conflict.dismiss('n-b', '已确认系误发')['dismissed'] is True
    assert conflict.conflicts() == []
    issues = conflict.assess('flash-41')[0]['issues']
    assert issues == ['更正推翻原说法，需改写后再核：n-a']


def test_fragment_amendments_take_effect_separately():
    data = json.loads((FIXTURES / 'corrections.json').read_text())
    data['notices'] = CONFLICTING[:1] + CONFLICTING[2:]
    desk = desk_from_data(data)
    # n-a 改的是“暂停清点”，n-c 改的是“只涉及北案”，两者不互相覆盖。
    assert desk.assess('flash-41')[0]['approved'] is False
    assert desk.assess('topic-16')[0]['approved'] is False
    worklist = {item['slot']: item for item in desk.correction_worklist()}
    assert {notice['notice'] for notice in worklist['flash-41']['notices']} == {'n-a'}
    assert {notice['notice'] for notice in worklist['topic-16']['notices']} == {'n-c'}


def test_impact_lists_pending_and_published_targets():
    conflict = with_notices(json.loads((FIXTURES / 'corrections.json').read_text()),
                            CONFLICTING)
    impact = conflict.impact('n-a')
    assert [item['slot'] for item in impact['pending_slots']] == ['flash-41', 'feed-13']
    assert [item['release_id'] for item in impact['published']] == [
        '20261118-flash', '20261118-feed']
    assert impact['conflicts'][0]['notices'] == ['n-a', 'n-b']


def test_notice_that_overturns_clearance_cannot_enter_the_channel():
    blocked = with_notices(json.loads((FIXTURES / 'corrections.json').read_text()),
                           CONFLICTING)
    with pytest.raises(ValueError, match='尚未全部核签'):
        blocked.publish('flash-41', 'late-flash')
    assert 'late-flash' not in blocked._published


def test_typo_correction_keeps_slot_clearable(desk):
    # notice-typo 只修手误，topic-16 仍可核签并刊发新回执。
    packet = desk.publish('topic-16', '20261119-topic')
    assert packet['reviews'][0]['approved'] is True
    assert packet['claims'][0]['clips'][0]['text'] == '暂停只涉及北案'


# ---- 合作方旧稿与更正回执 -------------------------------------------

def test_partner_keeps_old_copy_and_gets_the_correction_receipt(desk):
    desk.partner_notice('20261118-feed', '沿海合作站', 'notice-typo')
    receipt = desk.partner_receipt('20261118-feed')
    assert receipt['copy'][0]['text'] == ['北岸码头暂停清点。']
    assert receipt['copy'][0]['superseded_by'] == ['notice-typo']
    assert receipt['corrections'][0]['notice'] == 'notice-typo'
    assert receipt['corrections'][0]['amended'] == '暂停只涉及北岸'


def test_partner_receipt_requires_the_right_partner(desk):
    with pytest.raises(ValueError, match='未登记给合作方'):
        desk.partner_notice('20261118-feed', '另一家', 'notice-typo')
    with pytest.raises(ValueError, match='没有该更正通知'):
        desk.partner_notice('20261118-feed', '沿海合作站', 'n-missing')


def test_partner_log_keeps_copy_and_corrections_together(desk):
    desk.partner_notice('20261118-feed', '沿海合作站', 'notice-typo')
    desk.partner_ack('20261118-feed', 'notice-typo', '已按更正处理')
    log = desk.partner_corrections('沿海合作站')
    assert log['partner'] == '沿海合作站'
    assert log['releases'][0]['copy'][0]['text'] == ['北岸码头暂停清点。']
    assert log['releases'][0]['corrections'][0]['acknowledged'] is True
    assert desk.partner_corrections('未登记的合作站')['releases'] == []


def test_partner_ack_is_recorded_on_the_receipt(desk):
    desk.partner_notice('20261118-feed', '沿海合作站', 'notice-typo')
    entry = desk.partner_ack('20261118-feed', 'notice-typo', '已按更正处理')
    assert entry['acknowledged'] is True
    receipt = desk.partner_receipt('20261118-feed')
    assert receipt['acks'] == [{'notice': 'notice-typo', 'note': '已按更正处理'}]
    with pytest.raises(ValueError, match='尚未收到该更正回执'):
        desk.partner_ack('20261118-feed', 'notice-vague')


# ---- CLI ------------------------------------------------------------

def test_cli_resolve_and_confirm(capsys, tmp_path):
    path = str(FIXTURES / 'corrections.json')
    state = str(tmp_path / 'desk-state.json')
    main(['--desk', path, '--state', state, 'resolve', 'notice-vague'])
    output = json.loads(capsys.readouterr().out)
    assert output['status'] == 'ambiguous'
    main(['--desk', path, '--state', state, 'confirm', 'notice-vague',
          'dock-17', '1', '13', '20'])
    confirmed = json.loads(capsys.readouterr().out)
    assert confirmed['text'] == '暂停只涉及北案'
    main(['--desk', path, '--state', state, 'resolve', 'notice-vague'])
    assert json.loads(capsys.readouterr().out)['status'] == 'confirmed'
    # 确认结果写在状态文件里，换一个进程仍可回查同一坐标。
    saved = json.loads(Path(state).read_text(encoding='utf-8'))
    assert saved['assignments'][0]['start'] == 13


def test_cli_reports_unknown_notice_as_an_argument_error(capsys, tmp_path):
    with pytest.raises(SystemExit):
        main(['--desk', str(FIXTURES / 'corrections.json'),
              '--state', str(tmp_path / 'state.json'), 'resolve', 'n-missing'])
    assert '没有该更正通知' in capsys.readouterr().err


def test_cli_partner_receipt_and_worklist(capsys, tmp_path):
    path = str(FIXTURES / 'corrections.json')
    state = str(tmp_path / 'state.json')
    main(['--desk', path, '--state', state, 'worklist'])
    assert isinstance(json.loads(capsys.readouterr().out), list)
    main(['--desk', path, '--state', state, 'partner-receipt', '20261118-feed'])
    assert json.loads(capsys.readouterr().out)['partner'] == '沿海合作站'
