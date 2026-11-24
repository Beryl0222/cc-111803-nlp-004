from dataclasses import dataclass

from .corrections import trimming_issue
from .text import normalize_text

SUPPORT_LEVELS = ('original', 'narrower', 'identity')


@dataclass(frozen=True)
class Statement:
    """转述中的一个可独立核查的事实或引语。

    一段转述可以含两个事实，它们各自继承各自摘句的来源，不能合并计数。
    kind='identity' 表示只支撑说话者身份或在场，不足以支撑其说过的话。
    """

    id: str
    text: str
    kind: str = 'fact'


def support_level(claim, statement, clip):
    """判断摘句支撑的是原说法、较窄说法，还是只支撑说话者身份。"""
    if clip.supports:
        if clip.supports not in SUPPORT_LEVELS:
            raise ValueError(f'未知的依据支撑层级：{clip.supports}')
        return clip.supports
    if statement.kind == 'identity':
        return 'identity'
    expected = statement.text if claim.statements else claim.text
    expected_text = normalize_text(expected)
    clip_text = normalize_text(clip.text)
    if expected_text == clip_text:
        return 'original'
    if clip_text and clip_text in expected_text and len(clip_text) < len(expected_text):
        return 'narrower'
    return 'original'


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    clips: tuple
    named_quote: bool = False
    statements: tuple = ()

    def statement_list(self):
        if self.statements:
            return self.statements
        kind = 'quote' if self.named_quote else 'fact'
        return (Statement(id=self.id, text=self.text, kind=kind),)

    def clips_for(self, statement):
        if not self.statements:
            return tuple(self.clips)
        return tuple(clip for clip in self.clips
                     if (clip.statement_id or self.id) == statement.id)

    def validate(self):
        for statement in self.statement_list():
            if statement.kind not in ('fact', 'quote', 'identity'):
                raise ValueError(f'陈述 {statement.id} 的类型无效：{statement.kind}')
        if not self.statements:
            return self
        known = {statement.id for statement in self.statements}
        for clip in self.clips:
            if clip.statement_id is not None and clip.statement_id not in known:
                raise ValueError(
                    f'稿件 {self.id} 的摘句指向不存在的陈述：{clip.statement_id}')
        return self


class CopyReview:
    """根据记者选定的摘句和刊发栏目的采信约定核签。"""

    def __init__(self, archive, channel_min_desks, ledger=None):
        self.archive = archive
        self.channel_min_desks = channel_min_desks
        self.ledger = ledger

    def _check_statement(self, claim, statement, channel, ledger):
        min_desks = self.channel_min_desks[channel]
        desks = set()
        origins = set()
        problems = []
        held = []
        support = []
        withdrawn = []
        reused = False
        text_updates = []
        for clip in claim.clips_for(statement):
            effect = ledger.clip_effect(clip) if ledger is not None else None
            if effect and effect['status'] == 'retracted':
                withdrawn.append({'clip': clip, 'notice': effect.get('notice_id')})
                continue
            if effect and effect['status'] in ('held', 'conflict', 'corrected',
                                               'addition', 'attribution'):
                held.append(effect)
            check_clip = effect.get('clip', clip) if effect else clip
            if effect and effect.get('status') == 'reused':
                reused = True
            elif effect and effect.get('status') in ('corrected', 'conflict',
                                                     'held', 'attribution'):
                # 被更正推翻、归属失效或等待确认的旧摘句不能再作为依据。
                continue
            try:
                wire = self.archive.check_clip_details(check_clip)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            if effect and effect.get('amended'):
                text_updates.append({'notice': effect['notice_id'],
                                     'statement': statement.id,
                                     'amended': effect['amended'],
                                     'scope': effect.get('scope'),
                                     'from_location': [clip.wire_id, clip.edition,
                                                       clip.start, clip.end]})
            if effect and effect['status'] == 'reused' and check_clip.text != clip.text:
                text_updates.append({'notice': effect['notice_id'],
                                     'statement': statement.id,
                                     'amended': check_clip.text,
                                     'scope': 'typo',
                                     'from': clip.text,
                                     'from_location': [clip.wire_id, clip.edition,
                                                       clip.start, clip.end],
                                     'to_location': [check_clip.wire_id,
                                                     check_clip.edition,
                                                     check_clip.start, check_clip.end]})
            trim = trimming_issue(wire.text, check_clip)
            if trim:
                problems.append(trim)
                continue
            expected = statement.text if claim.statements else claim.text
            if (claim.named_quote or statement.kind == 'quote') and (
                    check_clip.kind != 'quote'
                    or check_clip.text not in (expected, clip.text)):
                problems.append('具名引语需要逐字摘句')
                continue
            desks.add(wire.desk)
            origins.add(wire.origin)
            support.append(support_level(claim, statement, check_clip))

        if support:
            if len(origins) < min_desks:
                problems.append(
                    f'独立来源不足，当前 {len(origins)} 家，栏目需要 {min_desks} 家')
            if statement.kind in ('fact', 'quote'):
                if 'original' not in support and 'narrower' in support:
                    problems.append('剩余证据只支持较窄的说法，需改写后再核')
                elif 'original' not in support and all(
                        level == 'identity' for level in support):
                    problems.append('剩余证据只支持说话者身份，不足以支撑原说法')
        elif withdrawn and not held:
            problems.append('依据已被撤回，没有剩余证据支撑该陈述')
        elif not withdrawn and not held:
            problems.append('没有可用证据')

        for item in held:
            if item['status'] == 'conflict':
                issue = f'更正通知冲突，等待值班编辑决定：{item["notice_id"]}'
            elif item.get('issue'):
                issue = item['issue']
            else:
                issue = f'更正影响该陈述，待核查：{item["notice_id"]}'
            if issue not in problems:
                problems.append(issue)

        unique_held = []
        for item in held:
            if all(existing['notice_id'] != item['notice_id'] for existing in unique_held):
                unique_held.append(item)
        unique_withdrawn = []
        for item in withdrawn:
            if all(existing['notice'] != item['notice'] for existing in unique_withdrawn):
                unique_withdrawn.append(item)
        return {
            'statement': statement.id,
            'kind': statement.kind,
            'desks': sorted(desks),
            'origins': sorted(origins),
            'support': support,
            'withdrawn': [item['notice'] for item in unique_withdrawn],
            'held': [item['notice_id'] for item in unique_held],
            'text_updates': text_updates,
            'reused_verification': reused,
            'issues': problems,
            'approved': not problems,
        }

    def check(self, claim, channel, ledger=None):
        if channel not in self.channel_min_desks:
            raise ValueError(f'未知刊发栏目：{channel}')
        ledger = ledger if ledger is not None else self.ledger
        statements = []
        problems = []
        desks = set()
        origins = set()
        for statement in claim.statement_list():
            result = self._check_statement(claim, statement, channel, ledger)
            statements.append(result)
            problems.extend(issue for issue in result['issues'] if issue not in problems)
            desks.update(result['desks'])
            origins.update(result['origins'])
        return {
            'claim': claim.id,
            'desks': sorted(desks),
            'origins': sorted(origins),
            'statements': statements,
            'issues': problems,
            'approved': not problems and all(item['approved'] for item in statements),
        }
