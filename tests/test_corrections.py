import json
from pathlib import Path

import pytest

from nlp_newsroom_correction_chain.clips import Clip, Wire, WireArchive
from nlp_newsroom_correction_chain.corrections import CorrectionNotice, FragmentTarget
from nlp_newsroom_correction_chain.editions import EditionDesk, Slot
from nlp_newsroom_correction_chain.editorial import CorrectionDesk
from nlp_newsroom_correction_chain.review import Claim, CopyReview, Fact


SCOPE = '北岸码头清点作业于 12 日 9 时暂停。'
OTHER = '林岳当晚在海州内港。'


def build(scope_texts=(('dock-1', '港区记者', SCOPE, None),), presence=True, channels=None):
    """scope_texts 每项为（稿号, 编辑台, 原文, 来源归属）；归属相同即同一来源。"""
    wires = []
    for wire_id, desk, text, origin in scope_texts:
        wires.append(Wire(wire_id, 1, desk, text, origin=origin))
    facts = [Fact('scope', '北岸码头清点暂停。',
                  tuple(Clip(w.id, 1, 0, len(w.text), w.text, 'report') for w in wires))]
    if presence:
        wires.append(Wire('talk-1', 1, '驻点采访', OTHER, speaker='林岳'))
        wires.append(Wire('talk-2', 1, '电话回访', OTHER, speaker='林岳'))
        facts.append(Fact('presence', OTHER, (
            Clip('talk-1', 1, 0, len(OTHER), OTHER, 'quote', '林岳'),
            Clip('talk-2', 1, 0, len(OTHER), OTHER, 'quote', '林岳'),
        ), True, '林岳', 'fact'))
    archive = WireArchive(wires)
    claim = Claim('combined', '北岸码头清点暂停，林岳当晚在内港。', clips=(), facts=tuple(facts))
    review = CopyReview(archive, channels or {'网站快讯': 1, '供稿': 2})
    slots = [Slot('flash', '网站快讯', ('combined',))]
    if channels is None or '供稿' in (channels or {}):
        slots.append(Slot('feed', '供稿', ('combined',)))
    editions = EditionDesk(review, [claim], slots)
    desk = CorrectionDesk(archive, editions.claims, editions,
                          {'网站快讯': '快讯更正', '供稿': '供稿更正'})
    return archive, editions, desk


def scope_target(text=SCOPE, claim='combined', fact='scope', wire='dock-1'):
    return FragmentTarget(wire, 1, 0, len(text), text, claim, fact)


def test_correction_is_scoped_to_one_fact_and_leaves_the_other_reviewable():
    archive, editions, desk = build()
    notice = CorrectionNotice('n1', '上一条稿件关于停运范围的说法有误，暂停的是码头，不是整个港区。',
                              target=scope_target(), replacement='北岸码头清点作业于 12 日 9 时暂停清点。')
    desk.register(notice)
    outcome = desk.apply('n1')
    assert outcome['status'] == 'applied'
    assert outcome['fact_id'] == 'scope'
    row = desk.review_slot('flash')[0]
    assert row['held'] and row['held_fact'] == 'scope'
    assert row['reviewable_units'] == ['presence']
    units = {unit['id']: unit for unit in row['units']}
    assert units['scope']['approved'] is False
    assert '留待核查' in units['scope']['issues']
    assert units['presence']['approved'] is True
    # 旧版次与旧坐标仍可回查
    assert archive.get('dock-1', 1).text.startswith(SCOPE)
    coordinates = desk.coordinates('combined')
    assert coordinates[0]['wire_id'] == 'dock-1' and coordinates[0]['start'] == 0
    assert coordinates[0]['current']['edition'] == 2
    # 第二处事实的坐标仍指回原版次
    assert coordinates[-1]['edition'] == 1


def test_ambiguous_correction_returns_candidates_and_does_not_invalidate_the_whole_wire():
    archive, editions, desk = build(scope_texts=(
        ('dock-1', '港区记者', SCOPE, None),
        ('wire-2', '通讯社', '南岸码头清点作业于 12 日 9 时暂停。', None),
    ))
    notice = CorrectionNotice('n2', '上一条稿件说“清点作业于 12 日暂停”，应是 13 日。')
    result = desk.register(notice)
    assert result['status'] == 'needs_editor'
    assert result['resolved'] is None
    assert len(result['candidates']) >= 2
    texts = {candidate['text'] for candidate in result['candidates']}
    assert '北岸码头清点作业于 12 日 9 时暂停。' in texts
    assert any('多个' in doubt or '不同' in doubt for doubt in result['doubts'])
    # 未确认归属前不把原稿判为失效，也不改动原文
    assert archive.latest('dock-1').edition == 1
    assert desk.claims['combined'].facts[0].clips[0].retracted is False


def test_manual_coordinates_can_resolve_attribution():
    archive, editions, desk = build()
    notice = CorrectionNotice('n3', '上一条稿件的停运范围说法有误。')
    desk.register(notice)
    fixed = desk.attribute_manual('n3', 'dock-1', 1, 0, len(SCOPE), SCOPE)
    assert fixed['status'] == 'resolved'
    assert fixed['resolved']['wire_id'] == 'dock-1'
    outcome = desk.apply('n3', replacement='北岸码头清点作业于 12 日 9 时起暂停。')
    assert outcome['status'] == 'applied'
    assert archive.latest('dock-1').text.startswith('北岸码头清点作业于 12 日 9 时起暂停。')


def test_syndicated_copies_with_different_wire_ids_count_as_one_source():
    archive, editions, desk = build(scope_texts=(
        ('wire-2', '通讯社', SCOPE, '通讯社'),
        ('rep-2', '合作站', SCOPE, '通讯社'),
    ), presence=False)
    assert archive.check_clip(editions.claims['combined'].facts[0].clips[0]) == '通讯社'
    supply = editions.review.check_claim(editions.claims['combined'], '供稿')
    assert supply['desks'] == ['通讯社']
    assert supply['approved'] is False
    assert any('独立来源不足' in issue for issue in supply['issues'])
    assert editions.review.check_claim(editions.claims['combined'], '网站快讯')['approved'] is True


def test_retraction_can_be_enough_for_web_but_not_for_syndication():
    archive, editions, desk = build(scope_texts=(
        ('dock-1', '港区记者', SCOPE, None),
        ('wire-2', '通讯社', SCOPE, None),
    ))
    assert editions.review.check_claim(editions.claims['combined'], '供稿')['approved'] is True
    notice = CorrectionNotice('n4', '第一份稿件的停运说法撤回。', op='retraction',
                              target=scope_target())
    desk.register(notice)
    assert desk.apply('n4')['status'] == 'applied'
    flash = desk.review_slot('flash')[0]
    feed = desk.review_slot('feed')[0]
    assert flash['approved'] is True          # 网站快讯仍可由通讯社一稿支撑
    assert feed['approved'] is False          # 供稿需要两家
    assert flash['support'] == 'original'
    with pytest.raises(ValueError, match='留待核查'):
        desk.publish('feed', 'feed-x')
    desk.publish('flash', 'flash-x')


def test_published_piece_enters_correction_work_in_its_own_column():
    archive, editions, desk = build(scope_texts=(
        ('dock-1', '港区记者', SCOPE, None),
        ('wire-2', '通讯社', SCOPE, None),
    ))
    desk.publish('feed', 'feed-1')
    notice = CorrectionNotice('n5', '停运范围有误。', target=scope_target(),
                              replacement='北岸码头清点作业于 12 日 9 时暂停清点。')
    desk.register(notice)
    outcome = desk.apply('n5')
    works = outcome['impact']['works']
    assert len(works) == 1
    assert works[0]['channel'] == '供稿更正'
    assert works[0]['release_ids'] == ['feed-1']
    assert desk.corrections_queue('供稿更正')[0]['notice'] == 'n5'
    # 已发回执保持原样，合作方另存原稿与更正回执
    partner = desk.partner_receipts()
    assert partner and partner[0]['release_ids'] == ['feed-1']
    assert partner[0]['original'][0]['slot']['id'] == 'feed'
    assert partner[0]['correction_receipt']['channel'] == '供稿更正'


def test_typo_reuses_verification_and_keeps_old_and_new_coordinates():
    text = '北岸码关清点作业暂停。'
    fixed = '北岸码头清点作业暂停。'
    archive, editions, desk = build(scope_texts=(
        ('dock-1', '港区记者', text, None),
        ('wire-2', '通讯社', text, None),
    ))
    desk.publish('feed', 'feed-typo')
    notice = CorrectionNotice('n6', '“码关”系手误，应为“码头”。', op='typo',
                              replacement=fixed,
                              target=FragmentTarget('dock-1', 1, 0, len(text), text,
                                                    'combined', 'scope'))
    desk.register(notice)
    outcome = desk.apply('n6')
    assert outcome['status'] == 'applied'
    assert outcome['impact']['signoff_stands']
    recheck = desk.recheck()[0]
    assert recheck['meaning_unchanged'] and recheck['verification_reused']
    assert recheck['old']['text'] == text
    assert recheck['new']['text'] == fixed
    # 手误不推翻已发稿的核签，不必新开更正版面
    assert not outcome['impact']['works']
    assert desk.review_slot('flash')[0].get('held') is None


def test_numeric_or_negating_typo_is_treated_as_a_substantive_correction():
    text = '北岸码头清点作业于 12 日 9 时暂停。'
    archive, editions, desk = build(scope_texts=(
        ('dock-1', '港区记者', text, None),
        ('wire-2', '通讯社', text, None),
    ))
    notice = CorrectionNotice('n7', '手误：日期应为 13 日。', op='typo',
                              replacement='北岸码头清点作业于 13 日 9 时暂停。',
                              target=FragmentTarget('dock-1', 1, 0, len(text), text,
                                                    'combined', 'scope'))
    desk.register(notice)
    outcome = desk.apply('n7')
    assert outcome['impact']['signoff_stands'] == []
    assert outcome['impact']['held_slots']
    assert desk.held_facts('flash')


def test_two_fragments_of_one_original_revise_separately_and_conflicts_wait():
    first = '北岸码头清点作业暂停。'
    second = '林岳当晚在海州内港。'
    wire = Wire('dock-1', 1, '港区记者', first + second, speaker=None)
    archive = WireArchive([wire])
    claim = Claim('c', '清点暂停且林岳在内港。', clips=(), facts=(
        Fact('scope', '清点暂停。',
             (Clip('dock-1', 1, 0, len(first), first, 'report'),)),
        Fact('presence', second,
             (Clip('dock-1', 1, len(first), len(first) + len(second), second, 'report'),)),
    ))
    review = CopyReview(archive, {'网站快讯': 1})
    editions = EditionDesk(review, [claim], [Slot('flash', '网站快讯', ('c',))])
    desk = CorrectionDesk(archive, editions.claims, editions, {'网站快讯': '快讯更正'})
    one = CorrectionNotice('a', '停运范围更正', replacement='北岸码头清点作业于上午暂停。',
                           target=FragmentTarget('dock-1', 1, 0, len(first), first, 'c', 'scope'))
    two = CorrectionNotice('b', '受访者位置更正', replacement='林岳当晚在北岸码头。',
                           target=FragmentTarget('dock-1', 1, len(first),
                                                 len(first) + len(second), second, 'c',
                                                 'presence'))
    desk.register(one)
    desk.register(two)
    assert desk.apply('a')['status'] == 'applied'
    assert desk.apply('b')['status'] == 'applied'
    assert archive.latest('dock-1').text == '北岸码头清点作业于上午暂停。林岳当晚在北岸码头。'
    conflict = CorrectionNotice('c', '同一片段上的另一条更正',
                                replacement='北岸码头清点作业未暂停。',
                                target=FragmentTarget('dock-1', 1, 0, len(first), first,
                                                      'c', 'scope'))
    desk.register(conflict)
    waiting = desk.apply('c')
    assert waiting['status'] == 'awaiting_decision'
    assert waiting['conflict'] == 'a'
    assert desk.awaiting_decision()[0]['status_label'] == '等待决定'
    # 冲突只登记等待决定，不把同一版另一片段的记录改掉
    row = desk.review_slot('flash')[0]
    assert row['held_fact'] in ('scope', 'presence')
    assert row['reviewable_units']



def test_quote_attribution_is_not_transferred_to_someone_who_merely_shares_a_place():
    a = Wire('talk-1', 1, '驻点采访', '我当晚在海州内港。', speaker='林岳')
    b = Wire('talk-2', 1, '另一采访', '我当晚在海州内港。', speaker='乔骁')
    archive = WireArchive([a, b])
    claim = Claim('q', '两名受访者当晚在内港。', clips=(), facts=(
        Fact('lin', '林岳当晚在内港。',
             (Clip('talk-1', 1, 0, 9, '我当晚在海州内港。', 'quote', '林岳'),),
             True, '林岳', 'fact'),
        Fact('qiao', '乔骁当晚在内港。',
             (Clip('talk-2', 1, 0, 9, '我当晚在海州内港。', 'quote', '乔骁'),),
             True, '乔骁', 'fact'),
    ))
    review = CopyReview(archive, {'网站快讯': 1})
    editions = EditionDesk(review, [claim], [Slot('flash', '网站快讯', ('q',))])
    desk = CorrectionDesk(archive, editions.claims, editions)
    generic = CorrectionNotice('g', '上一条稿件引述“我当晚在海州内港”有误。')
    result = desk.register(generic)
    assert result['status'] == 'needs_editor'
    assert any('不同说话者' in doubt for doubt in result['doubts'])
    named = CorrectionNotice('n', '上一条稿件中林岳的说法有误。')
    named_result = desk.register(named)
    assert named_result['status'] == 'resolved'
    assert named_result['resolved']['speaker'] == '林岳'
    assert named_result['resolved']['dependents'][0]['fact_id'] == 'lin'


def test_conditional_or_negated_speech_cannot_be_clipped_into_its_opposite():
    wire = Wire('talk-9', 1, '驻点采访', '若水位继续上涨，北岸码头才会暂停清点。', speaker='林岳')
    archive = WireArchive([wire])
    text = wire.text
    start = text.index('北岸码头才会暂停清点。')
    clip = Clip('talk-9', 1, start, start + 11, '北岸码头才会暂停清点。', 'quote', '林岳')
    with pytest.raises(ValueError, match='条件'):
        archive.check_clip(clip)
    negated = Wire('talk-10', 1, '驻点采访', '北岸码头没有暂停清点。', speaker='林岳')
    archive = WireArchive([negated])
    start = negated.text.index('暂停清点。')
    with pytest.raises(ValueError, match='否定'):
        archive.check_clip(Clip('talk-10', 1, start, start + 5,
                                '暂停清点。', 'quote', '林岳'))
    # 完整保留条件的摘句可以通过
    archive = WireArchive([wire])
    assert archive.check_clip(Clip('talk-9', 1, 0, len(text), text,
                                   'quote', '林岳')) == '驻点采访'


def test_correction_reverberates_through_dependent_briefs_and_partner_slots():
    archive, editions, desk = build(scope_texts=(
        ('dock-1', '港区记者', SCOPE, None),
        ('wire-2', '通讯社', SCOPE, None),
    ))
    brief = Claim('brief', '北岸码头清点暂停。',
                  clips=(Clip('dock-1', 1, 0, len(SCOPE), SCOPE, 'report'),))
    editions.claims['brief'] = brief
    editions.slots['brief'] = Slot('brief', '网站快讯', ('brief',))
    desk.claims['brief'] = brief
    desk.publish('brief', 'brief-1')
    notice = CorrectionNotice('dep', '停运范围说法有误。', target=scope_target(),
                              replacement='北岸码头清点作业于 12 日 9 时暂停清点。')
    desk.register(notice)
    outcome = desk.apply('dep')
    assert 'brief' in outcome['impact']['dependent_claims']
    columns = {work['channel'] for work in outcome['impact']['works']}
    assert '快讯更正' in columns
    assert desk.receipt('brief-1')['slot']['id'] == 'brief'


def test_operation_aliases_and_missing_edition_are_tolerated():
    text = '北岸码头清点作业于 12 日 9 时暂停。'
    notice = CorrectionNotice.from_dict({
        'id': 'alias', 'text': '日期有误。', 'op': '更正',
        'target': {'wire_id': 'dock-1', 'start': 0, 'end': len(text), 'text': text},
        'replacement': '北岸码头清点作业于 13 日 9 时暂停。',
    })
    assert notice.op == 'correction'
    assert notice.target.edition == 1
    wire = Wire.from_dict({'id': 'w', 'edition': 1, 'desk': '甲',
                           'text': text, 'source': '通讯社'})
    assert wire.origin == '通讯社'
    assert wire.source_key == '通讯社'


def test_revised_manuscript_applies_only_the_changed_fragment():
    tail = '林岳说：“我当晚在海州内港。”'
    wire = Wire('dock-1', 1, '港区记者', SCOPE + tail)
    other = Wire('wire-2', 1, '通讯社', SCOPE)
    archive = WireArchive([wire, other])
    claim = Claim('c', '北岸码头清点暂停。', clips=(
        Clip('dock-1', 1, 0, len(SCOPE), SCOPE, 'report'),
        Clip('wire-2', 1, 0, len(SCOPE), SCOPE, 'report'),
    ))
    review = CopyReview(archive, {'网站快讯': 1})
    editions = EditionDesk(review, [claim], [Slot('flash', '网站快讯', ('c',))])
    desk = CorrectionDesk(archive, editions.claims, editions)
    notice = CorrectionNotice('rev', '收到修订稿，暂停时间应为 13 日。',
                              revision_wire_id='dock-1', revision_edition=1,
                              revision_text='北岸码头清点作业于 13 日 9 时暂停。' + tail,
                              target=scope_target(claim='c', fact=None))
    desk.register(notice)
    outcome = desk.apply('rev')
    assert outcome['status'] == 'applied'
    assert archive.latest('dock-1').text.startswith('北岸码头清点作业于 13 日 9 时暂停。')
    # 修订只落在被更正的那段，同一稿其余文字不动
    assert archive.latest('dock-1').text.endswith(tail)


def test_published_partner_packet_keeps_old_copy_and_correction_receipt():
    archive, editions, desk = build(scope_texts=(
        ('dock-1', '港区记者', SCOPE, None),
        ('wire-2', '通讯社', SCOPE, None),
    ))
    desk.publish('feed', 'feed-keep')
    notice = CorrectionNotice('keep', '停运范围说法有误。', target=scope_target(),
                              replacement='北岸码头清点作业于 12 日 9 时暂停清点。')
    desk.register(notice)
    desk.apply('keep')
    packet = desk.partner_receipts()[0]
    assert packet['release_ids'] == ['feed-keep']
    assert packet['original'][0]['release_id'] == 'feed-keep'
    assert packet['correction_receipt']['notice'] == 'keep'
    assert packet['correction_receipt']['status'] == 'queued'
    # 已发回执本身不被改写作废
    assert desk.receipt('feed-keep')['slot']['id'] == 'feed'
    assert desk.receipt('feed-keep')['reviews'][0]['approved'] is True


def test_unresolved_notice_holds_plausible_desks_but_not_unrelated_ones():
    from nlp_newsroom_correction_chain.desk_cli import load_desk
    path = Path(__file__).parents[1] / 'fixtures' / 'desk-corrections.json'
    desk = load_desk(path)
    held = desk.pending_slots('notice-4')
    assert 'flash-41' in held and 'brief-77' in held
    assert desk.review_slot('weather-3')[0]['approved'] is True
    desk.publish('weather-3', 'weather-release')
    assert desk.receipt('weather-release')['slot']['id'] == 'weather-3'
    with pytest.raises(ValueError, match='尚未确认归属'):
        desk.publish('flash-41', 'flash-release')


def test_support_distinguishes_original_narrower_and_identity_only():
    scope = '北岸码头清点作业暂停。'
    place = '林岳当晚在北岸码头。'
    archive = WireArchive([Wire('s1', 1, '甲', scope), Wire('s2', 1, '乙', scope),
                           Wire('p1', 1, '丙', place), Wire('p2', 1, '丁', place)])
    review = CopyReview(archive, {'供稿': 2})
    original = Claim('o', '北岸码头清点暂停。', clips=(
        Clip('s1', 1, 0, len(scope), scope, 'report'),
        Clip('s2', 1, 0, len(scope), scope, 'report'),
    ))
    assert review.check_claim(original, '供稿')['support'] == 'original'
    narrower = Claim('n', '北岸码头清点暂停。', clips=(
        Clip('s1', 1, 0, len(scope), scope, 'report', supports='narrower'),
        Clip('s2', 1, 0, len(scope), scope, 'report', supports='narrower'),
    ))
    assert review.check_claim(narrower, '供稿')['support'] == 'narrower'
    identity = Claim('i', place, clips=(), facts=(
        Fact('scope', '北岸码头清点暂停。', (
            Clip('s1', 1, 0, len(scope), scope, 'report'),
            Clip('s2', 1, 0, len(scope), scope, 'report'),
        )),
        Fact('who', place, (
            Clip('p1', 1, 0, len(place), place, 'quote', '林岳'),
            Clip('p2', 1, 0, len(place), place, 'quote', '林岳'),
        ), True, '林岳', 'identity'),
    ))
    # 这段稿件其余证据只够确认说话者身份，不够支撑“林岳当晚在北岸码头”本身。
    assert review.check_claim(identity, '供稿')['support'] == 'narrower'


def test_correction_defaults_to_covering_dependent_copies():
    archive, editions, desk = build(scope_texts=(
        ('dock-1', '港区记者', SCOPE, None),
        ('wire-2', '通讯社', SCOPE, None),
    ))
    brief = Claim('brief', '北岸码头清点暂停。',
                  clips=(Clip('dock-1', 1, 0, len(SCOPE), SCOPE, 'report'),))
    editions.claims['brief'] = brief
    editions.slots['brief'] = Slot('brief', '网站快讯', ('brief',))
    desk.claims['brief'] = brief
    notice = CorrectionNotice('cover', '停运范围说法有误。', target=scope_target(),
                              replacement='北岸码头清点作业于 12 日 9 时暂停清点。')
    desk.register(notice)
    outcome = desk.apply('cover')
    assert 'brief' in outcome['impact']['dependent_claims']
    assert {row['slot'] for row in outcome['impact']['held_slots']} == {'flash', 'feed', 'brief'}


def test_correction_fixture_runs_through_the_desk_cli(capsys):
    from nlp_newsroom_correction_chain import desk_cli
    path = Path(__file__).parents[1] / 'fixtures' / 'desk-corrections.json'
    desk_cli.main(['--desk', str(path), 'notice', 'notice-1'])
    first = json.loads(capsys.readouterr().out)
    assert first['status'] == 'resolved'
    assert first['resolved']['dependents']
    desk_cli.main(['--desk', str(path), 'notice', 'notice-4'])
    ambiguous = json.loads(capsys.readouterr().out)
    assert ambiguous['status'] == 'needs_editor'
    assert len(ambiguous['candidates']) > 1
    desk_cli.main(['--desk', str(path), 'preview', 'notice-2'])
    preview = json.loads(capsys.readouterr().out)
    channels = {row['channel']: row for row in preview['channels']}
    assert channels['供稿']['approved_before'] is True
    assert 'support_after' in channels['供稿']
