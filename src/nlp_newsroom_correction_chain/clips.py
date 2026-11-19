from dataclasses import dataclass, replace
from typing import Literal


SENTENCE_END = '。！？；\n'
NEGATION_CHARS = '不未没无非否勿'
CONDITIONAL_MARKERS = ('若', '如果', '假如', '倘若', '除非', '一旦', '要是')


@dataclass(frozen=True)
class Wire:
    """一版原始稿件。

    ``origin`` 表示来源归属。通讯社原稿和转载它的合作站即使稿号不同，
    只要 origin 相同，就只算一家独立来源；未填写时使用 desk。
    ``occurred_at`` 用于把“12 日”“当晚”这类相对时间放回具体事件。
    """

    id: str
    edition: int
    desk: str
    text: str
    speaker: str | None = None
    origin: str | None = None
    occurred_at: str | None = None

    @property
    def source_key(self):
        return self.origin or self.desk

    @classmethod
    def from_dict(cls, value):
        """容忍常见的同义字段：source/agency 等同于 origin，date 等同于 occurred_at。"""
        origin = value.get('origin', value.get('source', value.get('agency')))
        occurred_at = value.get('occurred_at', value.get('date'))
        return cls(
            id=value['id'], edition=value['edition'], desk=value['desk'],
            text=value['text'], speaker=value.get('speaker'),
            origin=origin, occurred_at=occurred_at,
        )


@dataclass(frozen=True)
class Clip:
    """登记在稿件上的摘句。

    ``supports`` 说明该依据支撑的是原说法（original）还是较窄说法
    （narrower）；``retracted`` 标记已撤回的依据，保留坐标供回查。
    """

    wire_id: str
    edition: int
    start: int
    end: int
    text: str
    kind: Literal['quote', 'report']
    speaker: str | None = None
    supports: Literal['original', 'narrower'] = 'original'
    retracted: bool = False

    @property
    def key(self):
        return (self.wire_id, self.edition, self.start, self.end)

    @classmethod
    def from_dict(cls, value):
        return cls(
            wire_id=value['wire_id'], edition=value['edition'],
            start=value['start'], end=value['end'], text=value['text'],
            kind=value['kind'], speaker=value.get('speaker'),
            supports=value.get('supports', 'original'),
            retracted=bool(value.get('retracted', False)),
        )


def _sentence_bounds(text, start, end):
    left = start
    while left > 0 and text[left - 1] not in SENTENCE_END:
        left -= 1
    right = end
    while right < len(text) and text[right] not in SENTENCE_END:
        right += 1
    return left, right


def meaning_cut_problem(text, start, end, clip_text):
    """检查摘句是否把否定或条件截掉，从而可能读成相反意思。

    只看同一句内的前文，避免把上一句的否定误算到本句头上；否定词要贴着
    被截掉的谓语才算被截断。
    """
    left, _ = _sentence_bounds(text, start, end)
    prefix = text[left:start]
    governing = text[max(left, start - 4):start]
    if any(char in NEGATION_CHARS for char in governing) and not any(
            char in NEGATION_CHARS for char in clip_text):
        return '摘句截断了否定'
    if any(marker in prefix for marker in CONDITIONAL_MARKERS) and not any(
            marker in clip_text for marker in CONDITIONAL_MARKERS):
        return '摘句截断了条件'
    return None


class WireArchive:
    """按稿号和版次保管原文，校对记者手工录入的摘句。

    每一版原文都原样保留。更正或修订只追加新版次，不覆盖旧版次，因此旧摘句
    的稿号、版次和字符偏移仍然可以回查；新版坐标由改写记录逐版映射得到。
    """

    def __init__(self, wires=()):
        self._wires = {}
        self._edits = {}
        for wire in wires:
            self.add(wire)

    # ---- 保管 -------------------------------------------------------
    def all(self):
        return list(self._wires.values())

    def add(self, wire):
        key = wire.id, wire.edition
        if not wire.id or wire.edition < 1 or not wire.text or key in self._wires:
            raise ValueError(f'稿件版次无效或重复：{key}')
        self._wires[key] = wire
        return wire

    def get(self, wire_id, edition):
        return self._wires.get((wire_id, edition))

    def editions(self, wire_id):
        return sorted((wire for wire in self._wires.values() if wire.id == wire_id),
                      key=lambda wire: wire.edition)

    def latest(self, wire_id):
        return max((wire for wire in self._wires.values() if wire.id == wire_id),
                   key=lambda wire: wire.edition, default=None)

    def add_edition(self, wire_id, text, speaker=None, occurred_at=None, edit=None):
        """追加新版次。``edit`` 为（旧起点, 旧终点, 新长度）。"""
        latest = self.latest(wire_id)
        if latest is None:
            raise ValueError(f'无法找到原稿：{wire_id}')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('新版原文不能为空')
        wire = self.add(Wire(id=latest.id, edition=latest.edition + 1, desk=latest.desk,
                             text=text, speaker=latest.speaker if speaker is None else speaker,
                             origin=latest.origin,
                             occurred_at=latest.occurred_at if occurred_at is None else occurred_at))
        if edit is not None:
            self._edits[(wire_id, wire.edition)] = edit
        return wire

    def add_revision(self, wire_id, text):
        """接收修订稿全文，按新旧共同前后缀登记一处改写并生成新版次。"""
        latest = self.latest(wire_id)
        if latest is None:
            raise ValueError(f'无法找到原稿：{wire_id}')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('修订稿原文不能为空')
        old = latest.text
        prefix = 0
        limit = min(len(old), len(text))
        while prefix < limit and old[prefix] == text[prefix]:
            prefix += 1
        suffix = 0
        while suffix < limit - prefix and old[len(old) - 1 - suffix] == text[len(text) - 1 - suffix]:
            suffix += 1
        parent_end = len(old) - suffix
        new_length = len(text) - prefix - suffix
        return self.add_edition(wire_id, text, edit=(prefix, parent_end, new_length))

    # ---- 坐标 -------------------------------------------------------
    def map_span(self, wire_id, edition, start, end):
        """把旧版次上的坐标映射到最新版次，返回（起点, 终点, 最新版原稿）。"""
        latest = self.latest(wire_id)
        source = self.get(wire_id, edition)
        if latest is None or source is None:
            raise ValueError(f'无法找到原稿：{wire_id}/{edition}')
        if not 0 <= start < end <= len(source.text):
            raise ValueError('坐标超出原稿')
        current_start, current_end, current_edition = start, end, edition
        while current_edition < latest.edition:
            next_edition = current_edition + 1
            edit = self._edits.get((wire_id, next_edition))
            if edit is None:
                raise ValueError(f'版次 {wire_id}/{next_edition} 缺少改写记录，无法回查坐标')
            parent_start, parent_end, new_length = edit
            delta = new_length - (parent_end - parent_start)
            if current_end <= parent_start:
                pass
            elif current_start >= parent_end:
                current_start += delta
                current_end += delta
            elif current_start == parent_start and current_end == parent_end:
                current_end = parent_start + new_length
            else:
                raise ValueError('摘句跨越了后续修订，无法在不改变含义的情况下单独定位')
            current_edition = next_edition
        return current_start, current_end, latest

    def corrected_edition(self, wire_id, edition, start, end, old_text, new_text, speaker=None):
        """在指定片段上写入更正，生成新版次并返回（新版, 新起点, 新终点）。

        同一原文不同片段的修订分别生效：若该稿已有新版次，先把旧坐标映射到
        新版次；映射后若原文已变或跨过冲突修订，则拒绝并说明原因。
        """
        source = self.get(wire_id, edition)
        if source is None:
            raise ValueError(f'无法找到更正所指原稿：{wire_id}/{edition}')
        if not 0 <= start < end <= len(source.text):
            raise ValueError('更正所指位置超出原稿')
        if source.text[start:end] != old_text:
            raise ValueError('更正所指片段和原稿文字不符')
        if not isinstance(new_text, str) or not new_text.strip():
            raise ValueError('更正后的文字不能为空')
        base_start, base_end, base = self.map_span(wire_id, edition, start, end)
        if base.text[base_start:base_end] != old_text:
            raise ValueError('更正所指片段在新版原文中已被其他修订改动，需先处理冲突')
        updated = base.text[:base_start] + new_text + base.text[base_end:]
        edition_new = self.add_edition(wire_id, updated, speaker=speaker,
                                       edit=(base_start, base_end, len(new_text)))
        return edition_new, base_start, base_start + len(new_text)

    # ---- 校对 -------------------------------------------------------
    def source_key_for(self, clip):
        wire = self.get(clip.wire_id, clip.edition)
        if wire is None:
            raise ValueError(f'无法找到摘句原稿：{clip.wire_id}/{clip.edition}')
        return wire.source_key

    def source_label_for(self, clip):
        wire = self.get(clip.wire_id, clip.edition)
        if wire is None:
            raise ValueError(f'无法找到摘句原稿：{clip.wire_id}/{clip.edition}')
        return wire.desk

    def check_clip(self, clip):
        wire = self.get(clip.wire_id, clip.edition)
        if wire is None:
            raise ValueError(f'无法找到摘句原稿：{clip.wire_id}/{clip.edition}')
        if not 0 <= clip.start < clip.end <= len(wire.text):
            raise ValueError('摘句位置超出原稿')
        if wire.text[clip.start:clip.end] != clip.text:
            raise ValueError('摘句和原稿文字不符')
        problem = meaning_cut_problem(wire.text, clip.start, clip.end, clip.text)
        if problem:
            raise ValueError(problem)
        if clip.kind == 'quote':
            if not clip.speaker or clip.speaker != wire.speaker:
                raise ValueError('引语说话人与原稿不符')
        elif clip.kind != 'report' or clip.speaker:
            raise ValueError('转述不能登记为具名引语')
        return wire.desk


def retarget_clip(clip, new_wire, start, end, text, speaker=None, supports=None):
    values = {'edition': new_wire.edition, 'start': start, 'end': end, 'text': text}
    if speaker is not None:
        values['speaker'] = speaker
    if supports is not None:
        values['supports'] = supports
    return replace(clip, **values)
