from dataclasses import dataclass
from typing import Literal

Support = Literal['original', 'narrower', 'identity', 'none']


@dataclass(frozen=True)
class Fact:
    """一段转述中的一个事实及其单独依据。

    同一段转述可以含两个事实（例如停运范围与受访者），两者各自继承所登记
    的摘句。更正、补充或撤回只影响被点中的事实，其余事实仍按原依据核签。
    ``role='identity'`` 表示该依据只用来确认说话者身份。
    """

    id: str
    text: str
    clips: tuple
    named_quote: bool = False
    speaker: str | None = None
    role: Literal['fact', 'identity'] = 'fact'

    @classmethod
    def from_dict(cls, value, clips=None):
        if clips is None:
            clips = value.get('clips', ())
        return cls(value['id'], value['text'], tuple(clips),
                   value.get('named_quote', False), value.get('speaker'), value.get('role', 'fact'))


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    clips: tuple
    named_quote: bool = False
    facts: tuple = ()
    speaker: str | None = None

    def units(self):
        """把稿件拆成可分别继承依据的事实单元。"""
        if self.facts:
            return [(fact.id, fact.text, tuple(fact.clips), fact.named_quote,
                     fact.speaker, fact.role) for fact in self.facts]
        return [('claim', self.text, tuple(self.clips), self.named_quote,
                 self.speaker, 'fact')]

    def with_units(self, units):
        if not self.facts and len(units) == 1:
            unit_id, text, clips, named_quote, speaker, role = units[0]
            return Claim(self.id, text, tuple(clips), named_quote, (), speaker)
        return Claim(self.id, self.text, self.clips, self.named_quote,
                     tuple(Fact(*unit) for unit in units), self.speaker)


def _dedupe(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


class CopyReview:
    """根据记者选定的摘句和刊发栏目的采信约定核签。

    来源按 ``Wire.source_key`` 去重：通讯社原稿和转载它的合作站即使使用
    不同稿号，只要 origin 相同就只算一家。撤回的依据不再计入来源数量，
    但保留在原坐标上供回查，因此同一句话可能仍够网站快讯、却不够供稿。
    """

    def __init__(self, archive, channel_min_desks):
        self.archive = archive
        self.channel_min_desks = channel_min_desks

    def min_desks(self, channel):
        try:
            return self.channel_min_desks[channel]
        except KeyError as exc:
            raise ValueError(f'未知栏目：{channel}') from exc

    def _valid(self, clip, text, named_quote, speaker, exclude_keys=()):
        if clip.retracted or clip.key in exclude_keys:
            return False
        try:
            self.archive.check_clip(clip)
        except ValueError:
            return False
        if named_quote and (clip.kind != 'quote' or clip.text != text):
            return False
        if named_quote and speaker and clip.speaker != speaker:
            return False
        return True

    def check(self, claim, channel):
        return self.check_claim(claim, channel)

    def check_claim(self, claim, channel, only_units=None, exclude_keys=()):
        min_desks = self.min_desks(channel)
        issues = []
        units = []
        all_desks = set()
        claimed_speakers = set()
        for unit in claim.units():
            unit_id, unit_text, clips, named_quote, unit_speaker, role = unit
            if only_units is not None and unit_id not in only_units:
                continue
            desks = set()
            unit_issues = []
            retracted = 0
            for clip in clips:
                if clip.retracted or clip.key in exclude_keys:
                    retracted += 1
                    continue
                try:
                    self.archive.check_clip(clip)
                except ValueError as exc:
                    unit_issues.append(str(exc))
                    issues.append(str(exc))
                    continue
                if named_quote and (clip.kind != 'quote' or clip.text != unit_text):
                    unit_issues.append('具名引语需要逐字摘句')
                    issues.append('具名引语需要逐字摘句')
                    continue
                if named_quote and unit_speaker and clip.speaker != unit_speaker:
                    unit_issues.append('引语说话人与原稿不符')
                    issues.append('引语说话人与原稿不符')
                    continue
                try:
                    desks.add(self.archive.source_key_for(clip))
                except ValueError as exc:
                    unit_issues.append(str(exc))
                    issues.append(str(exc))
                if clip.kind == 'quote' and clip.speaker:
                    claimed_speakers.add(clip.speaker)
            if len(desks) < min_desks:
                message = f'独立来源不足，当前 {len(desks)} 家，栏目需要 {min_desks} 家'
                unit_issues.append(message)
                issues.append(message)
            all_desks.update(desks)
            unit_support = self._unit_support(unit, exclude_keys)
            units.append({
                'id': unit_id, 'text': unit_text, 'desks': sorted(desks),
                'issues': _dedupe(unit_issues), 'approved': not unit_issues,
                'role': role, 'support': unit_support, 'retracted': retracted,
            })
        support = self._overall_support(claim, only_units)
        result = {
            'claim': claim.id, 'desks': sorted(all_desks), 'issues': _dedupe(issues),
            'approved': not issues, 'support': support, 'units': units,
        }
        if claimed_speakers:
            result['speakers'] = sorted(claimed_speakers)
        return result

    def _unit_support(self, unit, exclude_keys=()):
        unit_id, text, clips, named_quote, speaker, role = unit
        labels = []
        for clip in clips:
            if not self._valid(clip, text, named_quote, speaker, exclude_keys):
                continue
            if role == 'identity':
                labels.append('identity')
            else:
                labels.append(clip.supports)
        if not labels:
            return 'none'
        if all(label == 'identity' for label in labels):
            return 'identity'
        if 'original' in labels:
            return 'original'
        return 'narrower'

    def _overall_support(self, claim, only_units=None):
        """其余依据支撑的是原说法、较窄说法，还是只支持说话者身份。"""
        labels = []
        considered = 0
        for unit in claim.units():
            if only_units is not None and unit[0] not in only_units:
                continue
            considered += 1
            labels.append(self._unit_support(unit))
        if not labels:
            return 'none'
        supported = [label for label in labels if label != 'none']
        if not supported:
            return 'none'
        if len(supported) < considered:
            return 'narrower'
        if all(label == 'identity' for label in supported):
            return 'identity'
        if 'original' in supported:
            return 'original'
        return 'narrower'

    def coordinates(self, claim):
        """登记每条摘句的原始坐标及其对应到最新版次的位置。"""
        rows = []
        for unit_id, text, clips, named_quote, speaker, role in claim.units():
            for clip in clips:
                row = {
                    'fact_id': unit_id, 'wire_id': clip.wire_id,
                    'edition': clip.edition, 'start': clip.start, 'end': clip.end,
                    'text': clip.text, 'kind': clip.kind, 'speaker': clip.speaker,
                    'supports': clip.supports, 'retracted': clip.retracted,
                }
                try:
                    current_start, current_end, latest = self.archive.map_span(
                        clip.wire_id, clip.edition, clip.start, clip.end)
                    row['current'] = {
                        'edition': latest.edition, 'start': current_start,
                        'end': current_end,
                        'text': latest.text[current_start:current_end],
                    }
                except ValueError:
                    row['current'] = None
                rows.append(row)
        return rows
