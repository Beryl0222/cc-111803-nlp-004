import json
from pathlib import Path

import pytest

from nlp_newsroom_correction_chain.clips import Clip, Wire
from nlp_newsroom_correction_chain.corrections import (
    CorrectionNotice, compare_editions, map_span, recheck, trimming_issue,
)
from nlp_newsroom_correction_chain.desk_cli import desk_from_data, load_desk
from nlp_newsroom_correction_chain.review import Claim, CopyReview, Statement

FIXTURES = Path(__file__).parents[1] / 'fixtures'


@pytest.fixture
def base():
    return json.loads((FIXTURES / 'corrections.json').read_text(encoding='utf-8'))


@pytest.fixture
def desk(base):
    return desk_from_data(base)


def with_notices(base, notices):
    data = json.loads(json.dumps(base))
    data['notices'] = notices
    return desk_from_data(data)


# ---- 定位：改的是哪次事件、哪条陈述 ---------------------------------

def test_vague_notice_lists_candidates_instead_of_invalidating_a_whole_wire(desk):
    result = desk.resolution('notice-vague')
    assert result['status'] == 'ambiguous'
    assert result['target'] is None
    assert len(result['candidates']) > 1
    assert any('需确认归属' in doubt for doubt in result['doubts'])
    # 同一条稿的两个版次都作为候选出现，没有把整份旧稿判作失效。
    assert {item['edition'] for item in result['candidates']} >= {1, 2}


def test_quote_matching_two_speakers_is_ambiguous_not_a_match(base):
    notice = {'id': 'n-speaker', 'kind': 'correction',
              'text': '受访者的说法有误', 'quote': '我当晚在海州内港。'}
    result = with_notices(base, [notice]).resolution('n-speaker')
    assert result['status'] == 'ambiguous'
    speakers = {item['speaker'] for item in result['candidates']}
    assert speakers == {'林岳', '乔骁'}
    assert any('说话者不同' in doubt for doubt in result['doubts'])


def test_editor_confirmation_records_the_chosen_edition_and_offsets(desk):
    target = desk.assign('notice-vague', 'dock-17', 1, 13, 20)
    assert (target.edition, target.start, target.end) == (1, 13, 20)
    result = desk.resolution('notice-vague')
    assert result['status'] == 'confirmed'
    assert result['target'] == {
        'wire_id': 'dock-17', 'edition': 1, 'start': 13, 'end': 20,
        'text': '暂停只涉及北案', 'confirmed': True, 'automatic': False,
    }


def test_confirmation_must_match_the_quoted_text(desk):
    with pytest.raises(ValueError, match='引文不符'):
        desk.assign('notice-typo', 'dock-17', 1, 0, 9)


def test_confirmation_rejects_wrong_speaker_and_unknown_notice(desk):
    desk.ledger.add(CorrectionNotice('n-speaker', text='受访者有误',
                                     quote='我当晚在海州内港。', speaker='林岳'))
    target = desk.assign('n-speaker', 'conversation-9', 1, 0, 9)
    assert target.text == '我当晚在海州内港。'
    with pytest.raises(ValueError, match='说话人与更正通知不符'):
        desk.assign('n-speaker', 'press-21', 1, 5, 14)
    with pytest.raises(ValueError, match='没有该更正通知'):
        desk.assign('n-missing', 'dock-17', 1, 0, 9)


def test_indirect_quote_in_the_notice_text_is_used_as_a_clue(base):
    notice = {'id': 'n-indirect', 'kind': 'correction',
              'text': '来函称“暂停只涉及北案”一句有误'}
    result = with_notices(base, [notice]).resolution('n-indirect')
    assert result['status'] == 'resolved'
    assert result['target']['text'] == '暂停只涉及北案'
    assert result['target']['automatic'] is False


def test_relative_time_and_place_are_clues_not_whole_wire_verdicts(base):
    notice = {'id': 'n-rel', 'kind': 'correction',
              'text': '上一条稿件的时间有误', 'time': '12日'}
    result = with_notices(base, [notice]).resolution('n-rel')
    assert result['status'] == 'ambiguous'
    assert all('12日' in item['text'] for item in result['candidates'])


def test_unmatched_notice_stays_unresolved(base):
    notice = {'id': 'n-none', 'kind': 'correction',
              'text': '之前那条关于隧道封闭的稿子有误', 'place': '海底隧道'}
    result = with_notices(base, [notice]).resolution('n-none')
    assert result['status'] == 'unresolved'
    assert result['candidates'] == []
    assert '需要编辑指明版次和位置' in result['doubts'][0]


def test_two_similar_stops_in_one_wire_are_separate_candidates(base):
    data = json.loads(json.dumps(base))
    data['wires'].append({'id': 'dock-30', 'edition': 1, 'desk': '港区记者',
                          'source': '港区通讯',
                          'text': '北岸码头暂停清点。同日下午，北岸码头暂停清点。'})
    notice = {'id': 'n-two', 'kind': 'correction',
              'text': '当天两次暂停中第一次的范围有误', 'wire_id': 'dock-30',
              'edition': 1, 'quote': '北岸码头暂停清点。'}
    result = with_notices(data, [notice]).resolution('n-two')
    assert result['status'] == 'ambiguous'
    spans = {(item['start'], item['end']) for item in result['candidates']}
    assert spans == {(0, 9), (14, 23)}


def test_retraction_of_one_fragment_leaves_other_fragments_intact(base):
    desk = with_notices(base, [{
        'id': 'n-one-fragment', 'kind': 'retraction', 'scope': 'fact',
        'wire_id': 'dock-17', 'edition': 1, 'start': 0, 'end': 9,
        'quote': '北岸码头暂停清点。'}])
    # 同一份稿后面关于“只涉及北案”的片段不在撤回范围，专题标题仍按原依据审。
    assert desk.assess('topic-16')[0]['approved'] is True
    assert desk.assess('flash-41')[0]['approved'] is True
    assert desk.assess('feed-13')[0]['approved'] is False


def test_headline_from_independent_interview_survives_a_wire_retraction(base):
    data = json.loads(json.dumps(base))
    # 快讯正文沿用通讯稿，标题引语来自独立采访。
    data['claims'].append({
        'id': 'headline', 'text': '我当晚在海州内港。',
        'clips': [{'wire_id': 'conversation-9', 'edition': 1, 'start': 0,
                   'end': 9, 'text': '我当晚在海州内港。', 'kind': 'quote',
                   'speaker': '林岳'}]})
    data['slots'].append({'id': 'flash-headline', 'channel': '网站快讯',
                          'claim_ids': ['headline']})
    notice = {'id': 'n-wire-retract', 'kind': 'retraction', 'scope': 'fact',
              'wire_id': 'dock-17', 'edition': 1, 'start': 0, 'end': 9,
              'quote': '北岸码头暂停清点。'}
    desk = with_notices(data, [notice])
    assert desk.assess('flash-headline')[0]['approved'] is True
    assert desk.assess('flash-headline')[0]['issues'] == []


# ---- 条件、否定与摘句边界 -------------------------------------------

def test_conditional_clause_flagged_when_the_condition_is_cut():
    wire = Wire('interview-20', 1, '驻点采访',
                '林岳说：“如果北岸码头停运，我当晚会在海州内港。”', speaker='林岳')
    cut = Clip('interview-20', 1, 14, 24, '我当晚会在海州内港。', 'quote', '林岳')
    assert '条件词' in trimming_issue(wire.text, cut)
    whole = Clip('interview-20', 1, 5, 24, '如果北岸码头停运，我当晚会在海州内港。',
                 'quote', '林岳')
    assert trimming_issue(wire.text, whole) is None


def test_negation_at_the_clip_edge_is_not_trimmed_into_affirmative():
    wire = Wire('south-19', 1, '南岸通讯员', '南岸码头当日未停运。负责人乔骁称清点照常。')
    for text, start, end in [('停运', 7, 9), ('码头当日', 2, 6)]:
        clip = Clip('south-19', 1, start, end, text, 'report')
        assert trimming_issue(wire.text, clip) is not None
    # 摘句保留了否定词，就没有被裁成相反意思。
    assert trimming_issue(wire.text, Clip(
        'south-19', 1, 0, 10, '南岸码头当日未停运。', 'report')) is None


def test_notice_on_conditional_quote_reports_the_doubt(base):
    notice = {'id': 'n-cond', 'kind': 'correction',
              'text': '受访者的说法有误', 'quote': '我当晚会在海州内港。'}
    result = with_notices(base, [notice]).resolution('n-cond')
    assert result['status'] == 'resolved'
    assert any('不得裁成相反意思' in doubt for doubt in result['doubts'])


# ---- 转述中的两个事实各自继承依据 -----------------------------------

def test_each_statement_of_a_compound_claim_inherits_its_own_evidence(desk):
    compound = desk.claims['compound']
    whose, stopped = compound.statement_list()
    assert [clip.wire_id for clip in compound.clips_for(whose)] == ['conversation-9']
    assert [clip.wire_id for clip in compound.clips_for(stopped)] == ['station-4']
    review = desk.review.check(compound, '供稿')
    assert {item['statement'] for item in review['statements']} == {'whose', 'stopped'}
    assert review['statements'][0]['support'] == ['identity']


def test_identity_evidence_does_not_stand_in_for_the_fact(base):
    data = json.loads(json.dumps(base))
    data['claims'][3]['clips'] = [clip for clip in data['claims'][3]['clips']
                                  if clip['statement_id'] != 'stopped']
    desk = desk_from_data(data)
    reviewed = desk.review.check(desk.claims['compound'], '网站快讯')
    by_id = {item['statement']: item for item in reviewed['statements']}
    assert by_id['stopped']['approved'] is False
    assert '没有可用证据' in by_id['stopped']['issues']
    assert by_id['whose']['support'] == ['identity']


def test_claim_validation_rejects_orphan_statement_ids():
    statement = Statement('yes', '甲乙')
    clip = Clip('dock-17', 1, 0, 9, '北岸码头暂停清点。', 'report',
                statement_id='nope')
    claim = Claim('bad', '甲乙', (clip,), statements=(statement,))
    with pytest.raises(ValueError, match='不存在的陈述'):
        claim.validate()


# ---- 来源独立性 -----------------------------------------------------

def test_wire_agency_and_its_relay_count_as_one_source(desk):
    # relay-8 与 dock-17 使用不同稿号，但同属港区通讯。
    claim = Claim('relayed', '北岸码头暂停清点。', (
        Clip('dock-17', 1, 0, 9, '北岸码头暂停清点。', 'report'),
        Clip('relay-8', 1, 0, 9, '北岸码头暂停清点。', 'report'),
    ))
    result = CopyReview(desk.review.archive, {'供稿': 2}).check(claim, '供稿')
    assert result['desks'] == ['沿海合作站', '港区记者']
    assert result['origins'] == ['港区通讯']
    assert any('独立来源不足' in issue for issue in result['issues'])


def test_website_flash_accepts_one_source(desk):
    claim = Claim('one', '北岸码头暂停清点。', (
        Clip('relay-8', 1, 0, 9, '北岸码头暂停清点。', 'report'),))
    assert CopyReview(desk.review.archive, {'网站快讯': 1}).check(
        claim, '网站快讯')['approved']


# ---- 撤回后的剩余证据 -------------------------------------------------

def test_retraction_leaves_one_source_enough_for_web_not_for_feed(base):
    desk = with_notices(base, [{
        'id': 'n-retract', 'kind': 'retraction', 'scope': 'fact',
        'wire_id': 'station-4', 'edition': 1, 'start': 3, 'end': 12,
        'quote': '北岸码头暂停清点。'}])
    web = desk.assess('flash-41')[0]
    feed = desk.assess('feed-13')[0]
    assert web['approved'] is True
    assert web['statements'][0]['withdrawn'] == ['n-retract']
    assert feed['approved'] is False
    assert any('独立来源不足' in issue for issue in feed['issues'])


def test_remaining_evidence_that_only_supports_a_narrower_claim_is_reported(desk):
    claim = Claim('narrow', '北岸码头暂停清点，作业区稍后恢复。', (
        Clip('dock-17', 1, 0, 9, '北岸码头暂停清点。', 'report', supports='narrower'),
        Clip('station-4', 1, 3, 12, '北岸码头暂停清点。', 'report', supports='narrower'),
    ))
    result = CopyReview(desk.review.archive, {'网站快讯': 1}).check(claim, '网站快讯')
    assert result['approved'] is False
    assert '剩余证据只支持较窄的说法，需改写后再核' in result['issues']


def test_remaining_evidence_that_only_supports_speaker_identity_is_reported(desk):
    claim = Claim('identity', '林岳当晚在海州内港。', (
        Clip('conversation-9', 1, 0, 9, '我当晚在海州内港。', 'quote', '林岳',
             supports='identity'),))
    result = CopyReview(desk.review.archive, {'网站快讯': 1}).check(claim, '网站快讯')
    assert result['approved'] is False
    assert '剩余证据只支持说话者身份，不足以支撑原说法' in result['issues']


# ---- 修订回查 -------------------------------------------------------

def test_typo_revision_keeps_verification_for_one_fragment_only(desk):
    old = desk.review.archive.get('dock-17', 1)
    new = desk.review.archive.get('dock-17', 2)
    # 两个片段在同一原文的不同位置，分别生效。
    scope = recheck(old, new, 13, 20, CorrectionNotice('t', revision_kind='typo'))
    south = recheck(old, new, 21, 35, CorrectionNotice('t', revision_kind='typo'))
    assert scope['reusable'] is True
    assert scope['old']['text'] == '暂停只涉及北案'
    assert scope['new']['text'] == '暂停只涉及北岸'
    assert south['reusable'] is False
    assert south['new']['text'] == '12日南岸码头并未停运。'


def test_revision_that_flips_meaning_is_not_reusable():
    old = Wire('w', 1, '台', '南岸码头当日未停运。')
    new = Wire('w', 2, '台', '南岸码头当日停运。')
    assert recheck(old, new, 0, 10,
                   CorrectionNotice('t', revision_kind='typo'))['reusable'] is False


def test_typo_revision_cannot_smuggle_a_direction_change():
    old = Wire('w', 1, '台', '暂停只涉及北岸。')
    new = Wire('w', 2, '台', '暂停只涉及南岸。')
    # 通知声称只是手误，但改了方向词，不能沿用原核实结论。
    assert recheck(old, new, 0, 7,
                   CorrectionNotice('t', revision_kind='typo'))['reusable'] is False


def test_changed_speaker_in_revision_blocks_reuse():
    old = Wire('w', 1, '台', '我当晚在海州内港。', speaker='林岳')
    new = Wire('w', 2, '台', '我当晚在海州内港。', speaker='乔骁')
    assert recheck(old, new, 0, 9)['reusable'] is False


def test_map_span_expands_over_a_replacement():
    old = Wire('w', 1, '台', '甲乙丙丁戊')
    new = Wire('w', 2, '台', '甲乙X戊')
    assert map_span(old, new, 2, 4) == (2, 3)
    assert map_span(old, new, 0, 5) == (0, 4)


def test_compare_editions_classifies_punctuation_and_substantive_changes():
    old = Wire('w', 1, '台', '北岸码头暂停清点。')
    assert compare_editions(old, Wire('w', 2, '台', '北岸码头暂停清点！'))[
        'changes'][0]['kind'] == 'punctuation'
    assert compare_editions(old, Wire('w', 2, '台', '北岸码头暂停作业。'))[
        'changes'][0]['kind'] == 'substantive'


def test_typo_notice_reuses_verification_and_keeps_old_receipt(desk):
    result = desk.assess('topic-16')[0]
    statement = result['statements'][0]
    assert statement['reused_verification'] is True
    update = statement['text_updates'][0]
    assert update['notice'] == 'notice-typo'
    # 新旧坐标都保留，编辑可以回到具体版次和字符位置核对。
    assert update['from_location'] == ['dock-17', 1, 13, 20]
    assert update['to_location'] == ['dock-17', 2, 13, 20]
    assert statement['approved'] is True
    receipt = desk.receipt('20261118-topic')
    assert receipt['claims'][0]['clips'][0]['text'] == '暂停只涉及北案'
    assert receipt['claims'][0]['clips'][0]['edition'] == 1
