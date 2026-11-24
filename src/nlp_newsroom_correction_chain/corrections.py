"""中文更正通知的定位、影响判断与修订回查。"""

import difflib
import re
from dataclasses import dataclass, replace

from .text import normalize_text

NOTICE_KINDS = ('correction', 'addition', 'retraction')
NOTICE_SCOPES = ('fact', 'quote', 'attribution')
REVISION_KINDS = ('typo', 'wording', 'fact')

_PUNCTUATION = re.compile(r'^[\s，。、；：！？“”‘’（）《》〈〉…—·,.;:!?\'"()\[\]-]*$')
_NEGATION = '不没未无非否'
# 这些字出现在改动里通常改变事实（方向、范围、场所、数量、状态），
# 不能按手误沿用原核实结论。
_FACTUAL_CHARS = set('东南西北上下前后内外左右多少增减停开是否正负')
_SENTENCE_END = '。！？；\n'


@dataclass(frozen=True)
class CorrectionNotice:
    """编辑收到的一条中文更正、补充或撤回通知。

    坐标字段是通知自己声明的定位。没有坐标时按引用片段和地点、事件、
    时间线索查找候选，不能因为一条通知就把整份旧稿判作失效。
    """

    id: str
    text: str = ''
    kind: str = 'correction'
    scope: str = 'fact'
    wire_id: str | None = None
    edition: int | None = None
    start: int | None = None
    end: int | None = None
    quote: str | None = None
    amended: str | None = None
    revised_wire_id: str | None = None
    revised_edition: int | None = None
    revision_kind: str | None = None
    claim_id: str | None = None
    statement_id: str | None = None
    place: str | None = None
    event: str | None = None
    time: str | None = None
    speaker: str | None = None
    corrected_speaker: str | None = None
    note: str | None = None

    def __post_init__(self):
        if not self.id:
            raise ValueError('更正通知缺少编号')
        if self.kind not in NOTICE_KINDS:
            raise ValueError(f'未知更正类型：{self.kind}')
        if self.scope not in NOTICE_SCOPES:
            raise ValueError(f'未知更正对象：{self.scope}')
        if self.revision_kind is not None and self.revision_kind not in REVISION_KINDS:
            raise ValueError(f'未知修订性质：{self.revision_kind}')
        if (self.start is None) != (self.end is None):
            raise ValueError('更正坐标必须同时给出起止位置')
        if self.start is not None and (self.start < 0 or self.end <= self.start):
            raise ValueError('更正坐标无效')
        if self.revised_edition is not None and self.revised_edition < 1:
            raise ValueError('修订版次无效')


@dataclass(frozen=True)
class CorrectionTarget:
    notice_id: str
    wire_id: str
    edition: int
    start: int
    end: int
    text: str
    confirmed: bool = False
    automatic: bool = False


@dataclass(frozen=True)
class CorrectionCandidate:
    notice_id: str
    wire_id: str
    edition: int
    start: int
    end: int
    text: str
    speaker: str | None = None
    automatic: bool = True
    exact: bool = False


def _collapse(text):
    """去掉空白，同时保留每个字符回到原文的偏移。"""
    chars = []
    positions = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        chars.append(char)
        positions.append(index)
    return ''.join(chars), positions


def _search_spans(text, needle):
    if not needle:
        return []
    haystack, positions = _collapse(text)
    target = ''.join(needle.split())
    if not target:
        return []
    spans = []
    start = haystack.find(target)
    while start != -1:
        end = start + len(target)
        spans.append((positions[start], positions[end - 1] + 1))
        start = haystack.find(target, start + 1)
    return spans


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _quoted_fragments(notice):
    """通知里引用的原文片段；只取足够长的片段，避免单个虚词误配。"""
    fragments = []
    for part in re.split(r'…+|\.{3,}', notice.quote or ''):
        part = normalize_text(part)
        if len(part) >= 2:
            fragments.append(part)
    if not fragments:
        for part in re.findall(r'[“「『]([^”」』]+)[”」』]', notice.text):
            part = normalize_text(part)
            if len(part) >= 2:
                fragments.append(part)
    return fragments


def _sentence_spans(text):
    spans = []
    start = 0
    for index, char in enumerate(text):
        if char in _SENTENCE_END:
            if index > start:
                spans.append((start, index + 1 if char != '\n' else index))
            start = index + 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _sentence_bounds(text, start, end):
    left = start
    if start > 0 and text[start - 1] not in _SENTENCE_END:
        while left > 0 and text[left - 1] not in _SENTENCE_END:
            left -= 1
    right = end
    if end < len(text) and text[end - 1] not in _SENTENCE_END:
        while right < len(text) and text[right] not in _SENTENCE_END:
            right += 1
    return left, right


def trimming_issue(wire_text, clip):
    """检查摘句是否把条件、否定或限定词裁掉。

    只在同一句内检查，避免把下一句的限定误算到本句。
    """
    left, right = _sentence_bounds(wire_text, clip.start, clip.end)
    sentence = wire_text[left:right]
    prefix = wire_text[left:clip.start]
    suffix = wire_text[clip.end:right]
    clip_text = clip.text

    for marker in ('不', '没', '未', '无', '非', '并非', '否认'):
        if marker in clip_text:
            continue
        if prefix.endswith(marker) or suffix.startswith(marker):
            return f'摘句裁掉了紧邻的限定或否定词“{marker}”'

    for marker in ('如果', '若', '假如', '假设', '除非', '倘若', '一旦', '要是'):
        if marker in sentence and marker not in clip_text:
            before = sentence.split(marker, 1)[0]
            if not clip_text.startswith(before.strip()):
                return f'摘句裁掉了条件词“{marker}”'

    for marker in ('只', '仅', '才'):
        if marker in clip_text:
            continue
        # 限定词只有紧贴在摘句边界上时才改变原句范围；同一句后半段的
        # “林岳确认……”是另一层转述，不能算到本摘句头上。
        if marker in prefix[-2:] or marker in suffix[:2]:
            return f'摘句裁掉了限定词“{marker}”'
    return None


class CorrectionLedger:
    """把更正通知定位到具体版次和字符区间，并给出对每条摘句的影响。"""

    def __init__(self, archive, notices=(), claims=()):
        self.archive = archive
        self.notices = {}
        for notice in notices:
            if notice.id in self.notices:
                raise ValueError(f'更正通知编号重复：{notice.id}')
            self.notices[notice.id] = notice
        self.claims = {claim.id: claim for claim in claims}
        self._confirmations = {}
        self._dismissed = set()

    def add(self, notice):
        if notice.id in self.notices:
            raise ValueError(f'更正通知编号重复：{notice.id}')
        self.notices[notice.id] = notice
        return notice

    def notice(self, notice_id):
        try:
            return self.notices[notice_id]
        except KeyError:
            raise ValueError(f'没有该更正通知：{notice_id}') from None

    # ---- 定位 -------------------------------------------------------
    def _explicit_target(self, notice):
        if notice.wire_id is None or notice.edition is None:
            return None
        wire = self.archive.get(notice.wire_id, notice.edition)
        if wire is None:
            raise ValueError(f'更正通知 {notice.id} 指向不存在的版次')
        if notice.start is None:
            return None
        if not 0 <= notice.start < notice.end <= len(wire.text):
            raise ValueError(f'更正通知 {notice.id} 的坐标超出原稿')
        if notice.quote:
            actual = normalize_text(wire.text[notice.start:notice.end])
            if actual != normalize_text(notice.quote):
                raise ValueError(f'更正通知 {notice.id} 的引文与坐标不符')
        # 坐标由通知自己声明，可复现但不等于编辑已确认归属。
        return CorrectionTarget(notice.id, notice.wire_id, notice.edition,
                                notice.start, notice.end,
                                wire.text[notice.start:notice.end])

    def _candidate_wires(self, notice):
        """只按稿号、版次、说话者做硬过滤。

        地点、事件和时间常来自通讯稿或被通知用简称、相对时间表达，只作
        定位线索，不能因为对不上就排除掉独立采访里的同一句话。
        """
        wires = []
        for wire in self.archive.wires:
            if notice.wire_id and wire.id != notice.wire_id:
                continue
            if notice.edition and wire.edition != notice.edition:
                continue
            if notice.speaker and wire.speaker != notice.speaker:
                continue
            wires.append(wire)
        return wires

    def _hint_sentences(self, wire, notice):
        hints = [hint for hint in (notice.place, notice.event, notice.time) if hint]
        if not hints:
            return []
        scored = []
        for start, end in _sentence_spans(wire.text):
            score = sum(1 for hint in hints if hint in wire.text[start:end])
            if score:
                scored.append((score, start, end))
        if not scored:
            return []
        best = max(score for score, _, _ in scored)
        return [(start, end) for score, start, end in scored if score == best]

    def candidates(self, notice):
        explicit = self._explicit_target(notice)
        if explicit is not None:
            wire = self.archive.get(explicit.wire_id, explicit.edition)
            return [CorrectionCandidate(
                notice.id, explicit.wire_id, explicit.edition,
                explicit.start, explicit.end, explicit.text,
                speaker=wire.speaker if wire else None, automatic=False,
                exact=True)]
        fragments = _quoted_fragments(notice)
        claim_clips = []
        if notice.claim_id and notice.claim_id in self.claims:
            claim = self.claims[notice.claim_id]
            for clip in claim.clips:
                if notice.statement_id and (clip.statement_id or claim.id) != notice.statement_id:
                    continue
                claim_clips.append(clip)
        found = {}
        for wire in self._candidate_wires(notice):
            spans = []
            for fragment in fragments:
                spans.extend(_search_spans(wire.text, fragment))
            exact = bool(fragments) and bool(spans)
            if not fragments and not claim_clips:
                spans.extend(self._hint_sentences(wire, notice))
                if not spans and (notice.wire_id or notice.edition):
                    # 只说“上一条稿”而没有引文时，按句列出候选，绝不整份判作失效。
                    spans.extend(_sentence_spans(wire.text))
            for start, end in spans:
                key = (wire.id, wire.edition, start, end)
                if key not in found:
                    found[key] = CorrectionCandidate(
                        notice.id, wire.id, wire.edition, start, end,
                        wire.text[start:end], speaker=wire.speaker, exact=exact)
        for clip in claim_clips:
            wire = self.archive.get(clip.wire_id, clip.edition)
            if wire is None:
                continue
            key = (wire.id, wire.edition, clip.start, clip.end)
            if key not in found:
                found[key] = CorrectionCandidate(
                    notice.id, wire.id, wire.edition, clip.start, clip.end,
                    wire.text[clip.start:clip.end], speaker=wire.speaker)
        # 通知明确点名某版次时，候选缩到该版次，避免新旧版次被当作两个事件。
        if notice.edition and found:
            filtered = {key: value for key, value in found.items() if key[1] == notice.edition}
            if filtered:
                found = filtered
        return sorted(found.values(), key=lambda item: (item.wire_id, item.edition, item.start))

    def _automatic_target(self, notice, candidates):
        """没有坐标时，只有精确定位到唯一片段才能自动认定归属。

        仅靠地点、事件等线索命中的候选必须由编辑确认；但若候选全都落在
        已登记摘句的同一坐标上，可以按该摘句定位。
        """
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        if candidate.exact or notice.claim_id:
            return CorrectionTarget(notice.id, candidate.wire_id, candidate.edition,
                                    candidate.start, candidate.end, candidate.text)
        return None

    def status(self, notice_id):
        notice = self.notice(notice_id)
        if notice_id in self._confirmations:
            return 'confirmed'
        if self._explicit_target(notice) is not None:
            return 'resolved'
        candidates = self.candidates(notice)
        if not candidates:
            return 'unresolved'
        if self._automatic_target(notice, candidates) is not None:
            return 'resolved'
        return 'ambiguous'

    def confirm(self, notice_id, wire_id, edition, start, end):
        notice = self.notice(notice_id)
        wire = self.archive.get(wire_id, edition)
        if wire is None:
            raise ValueError(f'无法确认到不存在的原稿：{wire_id}/{edition}')
        if not 0 <= start < end <= len(wire.text):
            raise ValueError('确认坐标超出原稿')
        actual = normalize_text(wire.text[start:end])
        fragments = _quoted_fragments(notice)
        if fragments and not any(normalize_text(f) in actual for f in fragments):
            raise ValueError('确认的原文与更正引文不符')
        if notice.speaker and wire.speaker != notice.speaker:
            raise ValueError('确认的原文说话人与更正通知不符')
        target = CorrectionTarget(notice_id, wire_id, edition, start, end,
                                  wire.text[start:end], confirmed=True)
        self._confirmations[notice_id] = target
        return target

    def target(self, notice):
        if notice.id in self._confirmations:
            return self._confirmations[notice.id]
        explicit = self._explicit_target(notice)
        if explicit is not None:
            return explicit
        return self._automatic_target(notice, self.candidates(notice))

    def doubts(self, notice, candidates):
        doubts = []
        if not candidates:
            doubts.append('无法从通知文字和线索定位到原文，需要编辑指明版次和位置')
        if len(candidates) > 1:
            doubts.append(f'有 {len(candidates)} 个候选片段，事件或说话者可能不同，需确认归属')
            places = {candidate.text for candidate in candidates}
            speakers = {candidate.speaker for candidate in candidates}
            editions = {(candidate.wire_id, candidate.edition) for candidate in candidates}
            if len(speakers) > 1:
                doubts.append('候选片段的说话者不同，相同地点不等于同一句话')
            if len(editions) > 1:
                doubts.append('候选分布在多个稿号或版次，需确认是哪一次事件')
            if len(places) == 1:
                doubts.append('候选文字完全相同，仅凭文字无法区分事件或说话者')
        for candidate in candidates:
            wire = self.archive.get(candidate.wire_id, candidate.edition)
            if wire is None:
                continue
            snippet = wire.text[max(0, candidate.start - 12):candidate.end + 12]
            if any(marker in snippet for marker in ('如果', '若', '假如', '除非', '不', '没', '否认')):
                doubts.append(f'候选原文含条件或否定，确认时不得裁成相反意思：{snippet}')
                break
        return doubts

    def resolution(self, notice_id):
        notice = self.notice(notice_id)
        candidates = self.candidates(notice)
        confirmed = self._confirmations.get(notice_id)
        target = self.target(notice)
        return {
            'notice': notice.id,
            'status': 'confirmed' if confirmed else self.status(notice_id),
            'target': None if target is None else {
                'wire_id': target.wire_id, 'edition': target.edition,
                'start': target.start, 'end': target.end, 'text': target.text,
                'confirmed': target.confirmed, 'automatic': target.automatic,
            },
            'candidates': [{
                'wire_id': item.wire_id, 'edition': item.edition,
                'start': item.start, 'end': item.end, 'text': item.text,
                'speaker': item.speaker, 'automatic': item.automatic,
                'exact': item.exact,
            } for item in candidates],
            'doubts': self.doubts(notice, candidates),
        }

    # ---- 修订与摘句影响 ---------------------------------------------
    def _revision_change(self, notice, target, clip):
        if not notice.revised_edition or target.edition != clip.edition:
            return None
        if target.wire_id != clip.wire_id:
            return None
        if not _overlaps(target.start, target.end, clip.start, clip.end):
            # 同一修订稿里其他片段的改动仍会影响落到该片段上的摘句；
            # 只有本通知定位的片段才决定能否沿用核实结论。
            return None
        old = self.archive.get(target.wire_id, target.edition)
        new = self.archive.get(notice.revised_wire_id or target.wire_id,
                               notice.revised_edition)
        if old is None or new is None:
            raise ValueError(f'更正通知 {notice.id} 的修订版次不存在')
        assessment = revision_assessment(old, new, notice, target.start, target.end)
        if assessment['reusable']:
            mapped = map_span(old, new, clip.start, clip.end)
            if mapped is None:
                return {'status': 'held', 'notice_id': notice.id,
                        'issue': f'更正后无法找回原摘句位置：{notice.id}',
                        'assessment': assessment}
            new_start, new_end = mapped
            replacement = replace(clip, edition=new.edition,
                                  start=new_start, end=new_end,
                                  text=new.text[new_start:new_end])
            return {'status': 'reused', 'notice_id': notice.id, 'clip': replacement,
                    'assessment': assessment}
        return {'status': 'held', 'notice_id': notice.id,
                'issue': f'更正改变了事实、引语意义或说话者，需重新核实：{notice.id}',
                'assessment': assessment}

    def _notice_effect(self, notice, clip):
        target = self.target(notice)
        if notice.id in self._dismissed:
            return {'status': 'superseded', 'notice_id': notice.id}
        if target is None:
            candidates = self.candidates(notice)
            overlapping = [item for item in candidates
                           if item.wire_id == clip.wire_id
                           and item.edition == clip.edition
                           and _overlaps(item.start, item.end, clip.start, clip.end)]
            if overlapping:
                return {'status': 'held', 'notice_id': notice.id,
                        'issue': f'更正归属待确认：{notice.id}',
                        'candidates': [
                            {'wire_id': item.wire_id, 'edition': item.edition,
                             'start': item.start, 'end': item.end, 'text': item.text}
                            for item in overlapping]}
            return None
        if target.wire_id != clip.wire_id:
            return None
        if notice.corrected_speaker and target.edition == clip.edition \
                and _overlaps(target.start, target.end, clip.start, clip.end):
            # 说话者更正优先于文字修订：改的是归属，不是措辞。
            return {'status': 'attribution', 'notice_id': notice.id,
                    'issue': (f'实际说话者更正为 {notice.corrected_speaker}，'
                              f'原引语归属失效，需重新核实：{notice.id}'),
                    'corrected_speaker': notice.corrected_speaker}
        revision = self._revision_change(notice, target, clip)
        if revision is not None:
            return revision
        if target.edition != clip.edition:
            return None
        if not _overlaps(target.start, target.end, clip.start, clip.end):
            return None
        if notice.kind == 'retraction':
            return {'status': 'retracted', 'notice_id': notice.id}
        if notice.kind == 'addition':
            return {'status': 'held', 'notice_id': notice.id,
                    'issue': f'有补充材料待并入：{notice.id}'}
        if notice.corrected_speaker:
            # 说话者被更正：原引语即使文字未变也不能沿用，必须换人重核。
            return {'status': 'attribution', 'notice_id': notice.id,
                    'issue': (f'实际说话者更正为 {notice.corrected_speaker}，'
                              f'原引语归属失效，需重新核实：{notice.id}'),
                    'corrected_speaker': notice.corrected_speaker}
        return {'status': 'corrected', 'notice_id': notice.id,
                'issue': f'更正推翻原说法，需改写后再核：{notice.id}',
                'amended': notice.amended, 'scope': notice.scope}

    def dismiss(self, notice_id, reason=None):
        """值班编辑处理冲突或误发：被撤下的通知不再影响核签。"""
        self.notice(notice_id)
        self._dismissed.add(notice_id)
        return {'notice': notice_id, 'dismissed': True, 'reason': reason}

    def _conflicting(self, effects):
        decided = [item for item in effects
                   if item['status'] in ('corrected', 'retracted', 'attribution')]
        if len(decided) < 2:
            return []
        for index, first in enumerate(decided):
            first_notice = self.notice(first['notice_id'])
            for second in decided[index + 1:]:
                if self._notices_disagree(first_notice, self.notice(second['notice_id'])):
                    return sorted({item['notice_id'] for item in decided})
        return []

    def clip_effects(self, clip):
        effects = []
        for notice in self.notices.values():
            effect = self._notice_effect(notice, clip)
            if effect is None or effect['status'] == 'superseded':
                continue
            effects.append(effect)
        conflicts = self._conflicting(effects)
        for effect in effects:
            if effect['notice_id'] in conflicts:
                effect['status'] = 'conflict'
                effect['issue'] = (f'同一片段存在相互冲突的更正，等待值班编辑决定：'
                                   f'{effect["notice_id"]}')
        return effects

    def clip_effect(self, clip):
        effects = self.clip_effects(clip)
        if not effects:
            return None
        for status in ('conflict', 'attribution', 'retracted', 'held', 'corrected'):
            for item in effects:
                if item['status'] == status:
                    return item
        for item in effects:
            if item['status'] == 'reused':
                return item
        return effects[0]

    # ---- 汇总 -------------------------------------------------------
    def pending(self):
        pending = []
        for notice in self.notices.values():
            if notice.id in self._dismissed:
                continue
            resolution = self.resolution(notice.id)
            if resolution['status'] in ('ambiguous', 'unresolved'):
                pending.append(resolution)
        return pending

    def _notices_disagree(self, first, second):
        """两条通知是否给出互相排斥的处理结果。"""
        if 'addition' in (first.kind, second.kind):
            return False
        if first.kind != second.kind:
            return True
        if first.corrected_speaker and second.corrected_speaker:
            return first.corrected_speaker != second.corrected_speaker
        if first.amended and second.amended:
            return first.amended != second.amended
        return False

    def conflicts(self, notice_ids=None):
        notices = [self.notice(notice_id) for notice_id in notice_ids] \
            if notice_ids is not None else list(self.notices.values())
        conflicts = []
        for index, first in enumerate(notices):
            if first.id in self._dismissed:
                continue
            first_target = self.target(first)
            if first_target is None:
                continue
            for second in notices[index + 1:]:
                if second.id in self._dismissed:
                    continue
                second_target = self.target(second)
                if second_target is None:
                    continue
                if (first_target.wire_id, first_target.edition) != (
                        second_target.wire_id, second_target.edition):
                    continue
                if not _overlaps(first_target.start, first_target.end,
                                 second_target.start, second_target.end):
                    continue
                if not self._notices_disagree(first, second):
                    continue
                conflicts.append({
                    'notices': [first.id, second.id],
                    'wire_id': first_target.wire_id,
                    'edition': first_target.edition,
                    'spans': [[first_target.start, first_target.end],
                              [second_target.start, second_target.end]],
                })
        return conflicts

    def affected_notices(self, clip):
        return [effect['notice_id'] for effect in self.clip_effects(clip)]


def _classify_change(old_text, new_text):
    if old_text == new_text:
        return None
    if _PUNCTUATION.match(old_text or '') and _PUNCTUATION.match(new_text or ''):
        return 'punctuation'
    if not normalize_text(old_text) or not normalize_text(new_text):
        return 'character'
    if len(normalize_text(old_text)) <= 1 and len(normalize_text(new_text)) <= 1:
        return 'character'
    return 'substantive'


def compare_editions(old, new):
    """按字符比较同一稿号的两个版次，保留新旧坐标。"""
    if old.id != new.id:
        raise ValueError('只能比较同一稿号的不同版次')
    matcher = difflib.SequenceMatcher(None, old.text, new.text, autojunk=False)
    changes = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        kind = _classify_change(old.text[i1:i2], new.text[j1:j2])
        changes.append({
            'kind': kind,
            'old_start': i1, 'old_end': i2, 'old_text': old.text[i1:i2],
            'new_start': j1, 'new_end': j2, 'new_text': new.text[j1:j2],
        })
    return {
        'wire_id': old.id,
        'from_edition': old.edition,
        'to_edition': new.edition,
        'speaker_changed': old.speaker != new.speaker,
        'changes': changes,
    }


def map_span(old, new, start, end):
    """把旧版次坐标映射到新版次。

    起止点若落在替换/增删段内，就扩展到该段在新原文中的边界，保证
    整段改写的文字都包含在新坐标里。
    """
    if not 0 <= start < end <= len(old.text):
        raise ValueError('待映射的摘句坐标超出原稿')
    matcher = difflib.SequenceMatcher(None, old.text, new.text, autojunk=False)
    opcodes = matcher.get_opcodes()
    new_start = new_end = None
    for tag, i1, i2, j1, j2 in opcodes:
        if i1 <= start < i2:
            new_start = start - i1 + j1 if tag == 'equal' else j1
            break
    for tag, i1, i2, j1, j2 in opcodes:
        if i1 < end <= i2:
            new_end = end - i1 + j1 if tag == 'equal' else j2
            break
    if new_start is None or new_end is None or new_start >= new_end:
        return None
    return new_start, new_end


def revision_assessment(old, new, notice=None, start=None, end=None):
    """判断修订是否只修手误，可作为沿用核实结论的依据。

    只比较通知指向的片段（给了坐标时），同一原文其他片段的修订另行评估。
    """
    if start is None:
        start, end = 0, len(old.text)
    mapped = map_span(old, new, start, end)
    old_piece = old.text[start:end]
    new_piece = '' if mapped is None else new.text[mapped[0]:mapped[1]]
    changes = []
    matcher = difflib.SequenceMatcher(None, old_piece, new_piece, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        kind = _classify_change(old_piece[i1:i2], new_piece[j1:j2])
        changes.append({
            'kind': kind,
            'old_text': old_piece[i1:i2], 'new_text': new_piece[j1:j2],
        })
    changed_text = ''.join(change['old_text'] + change['new_text'] for change in changes)
    digits = any(char.isdigit() for char in changed_text)
    negation = any(char in _NEGATION for char in changed_text)
    factual = any(char in _FACTUAL_CHARS for char in changed_text)
    revision_kind = notice.revision_kind if notice else None
    amended = notice.amended if notice else None
    amended_matches = None
    if amended is not None:
        amended_matches = normalize_text(amended) == normalize_text(new_piece)
    if revision_kind in ('fact', 'wording'):
        # 通知已说明改动的是事实或措辞意义，不能沿用原核实结论。
        reusable = False
    elif amended_matches is False:
        # 修订稿的实际文字与通知声称的改法不一致，先回查再谈沿用。
        reusable = False
    elif not changes:
        reusable = True
    elif revision_kind == 'typo':
        reusable = (not digits and not negation and not factual
                    and all(change['kind'] in ('punctuation', 'character')
                            for change in changes))
    else:
        # 未声明修订性质时，只有纯标点变化才可自动沿用。
        reusable = all(change['kind'] == 'punctuation' for change in changes)
    return {
        'wire_id': old.id,
        'from_edition': old.edition,
        'to_edition': new.edition,
        'speaker_changed': old.speaker != new.speaker,
        'mapped': None if mapped is None else {'start': mapped[0], 'end': mapped[1]},
        'old_text': old_piece,
        'new_text': new_piece,
        'changes': changes,
        'revision_kind': revision_kind,
        'amended_matches_revision': amended_matches,
        'reusable': reusable and not old.speaker != new.speaker,
    }


def recheck(old, new, start, end, notice=None):
    """回查新旧原文：是否可沿用核实结论，以及新坐标和新文字。"""
    assessment = revision_assessment(old, new, notice, start, end)
    mapped = map_span(old, new, start, end)
    return {
        'reusable': assessment['reusable'],
        'assessment': assessment,
        'old': {'edition': old.edition, 'start': start, 'end': end,
                'text': old.text[start:end]},
        'new': None if mapped is None else {
            'edition': new.edition, 'start': mapped[0], 'end': mapped[1],
            'text': new.text[mapped[0]:mapped[1]]},
    }
