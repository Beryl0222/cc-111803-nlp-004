from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Wire:
    """一次通讯稿版次。

    source 表示通讯社/供稿方这一层的来源标识。通讯社和转载它的合作站即使
    使用不同稿号，也共用同一个 source，核签时只算一家独立来源。
    """

    id: str
    edition: int
    desk: str
    text: str
    speaker: str | None = None
    source: str | None = None
    event: str | None = None
    place: str | None = None
    time: str | None = None

    @property
    def origin(self):
        return self.source or self.desk


@dataclass(frozen=True)
class Clip:
    wire_id: str
    edition: int
    start: int
    end: int
    text: str
    kind: Literal['quote', 'report']
    speaker: str | None = None
    # 'original' / 'narrower' / 'identity' / None（由文字关系推断）
    supports: str | None = None
    statement_id: str | None = None


class WireArchive:
    """按稿号和版次保管原文，校对记者手工录入的摘句。"""

    def __init__(self, wires):
        self._wires = {}
        for wire in wires:
            key = wire.id, wire.edition
            if wire.edition < 1 or not wire.text or key in self._wires:
                raise ValueError(f'稿件版次无效或重复：{key}')
            self._wires[key] = wire

    @property
    def wires(self):
        return tuple(self._wires.values())

    def get(self, wire_id, edition):
        return self._wires.get((wire_id, edition))

    def check_clip_details(self, clip):
        wire = self._wires.get((clip.wire_id, clip.edition))
        if wire is None:
            raise ValueError(f'无法找到摘句原稿：{clip.wire_id}/{clip.edition}')
        if not 0 <= clip.start < clip.end <= len(wire.text):
            raise ValueError('摘句位置超出原稿')
        if wire.text[clip.start:clip.end] != clip.text:
            raise ValueError('摘句和原稿文字不符')
        if clip.kind == 'quote':
            if not clip.speaker or clip.speaker != wire.speaker:
                raise ValueError('引语说话人与原稿不符')
        elif clip.kind != 'report' or clip.speaker:
            raise ValueError('转述不能登记为具名引语')
        return wire

    def check_clip(self, clip):
        """兼容旧接口：返回摘句所属编辑台。"""
        return self.check_clip_details(clip).desk

    def latest(self, wire_id):
        return max((wire for wire in self._wires.values() if wire.id == wire_id),
                   key=lambda wire: wire.edition, default=None)

    def editions(self, wire_id):
        return sorted((wire for wire in self._wires.values() if wire.id == wire_id),
                      key=lambda wire: wire.edition)
