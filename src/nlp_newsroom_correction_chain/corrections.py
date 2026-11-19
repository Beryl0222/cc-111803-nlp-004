import re
from dataclasses import dataclass, field
from typing import Literal

from .clips import NEGATION_CHARS, CONDITIONAL_MARKERS
from .text import keywords, names_in, relative_time_terms


OP_LABELS = {
    'correction': '更正', 'retraction': '撤回', 'addition': '补充',
    'clarification': '说明', 'typo': '手误',
}

OP_ALIASES = {
    '更正': 'correction', '修订': 'correction', 'revise': 'correction',
    '撤回': 'retraction', '撤销': 'retraction', 'withdraw': 'retraction',
    '补充': 'addition', 'add': 'addition',
    '说明': 'clarification', 'clarify': 'clarification',
    '手误': 'typo', 'typo': 'typo', 'corrigendum': 'correction',
}


def normalize_op(value):
    key = (value or 'correction').strip()
    return OP_ALIASES.get(key, key.lower() if key.isascii() else key)

MIN_CONFIDENCE = 0.5
BOILERPLATE = (
    '上一条稿件', '上一条', '上一条消息', '前稿', '原稿', '原文', '此前报道', '此前',
    '关于', '针对', '就', '说法有误', '说法不准确', '说法错误', '有误', '不准确',
    '系误', '误作', '误为', '错误', '应为', '应改为', '现更正为', '更正为', '改为',
    '特此更正', '现予更正', '更正', '撤回', '撤销', '补充', '说明', '通知',
    '稿件', '报道', '消息', '内容', '其中', '一处', '一处表述', '表述', '文字',
)


@dataclass(frozen=True)
class FragmentTarget:
    """更正指向的原文坐标：稿号、版次、字符区间与该区间的文字。"""

    wire_id: str
    edition: int
    start: int
    end: int
    text: str
    claim_id: str | None = None
    fact_id: str | None = None

    @property
    def key(self):
        return (self.wire_id, self.edition, self.start, self.end)

    @classmethod
    def from_dict(cls, value):
        return cls(value['wire_id'], value.get('edition', 1), value['start'], value['end'],
                   value.get('text', ''), value.get('claim_id'), value.get('fact_id'))


@dataclass(frozen=True)
class CorrectionNotice:
    """编辑部收到的一条中文更正文或修订稿。

    ``op``：correction 更正、retraction 撤回、addition 补充、
    clarification 说明、typo 手误。修订稿可给出 ``revision_text`` 与
    ``revision_wire_id``/``revision_edition``，由系统比对出改动片段。
    """

    id: str
    text: str
    op: Literal['correction', 'retraction', 'addition', 'clarification', 'typo'] = 'correction'
    target: FragmentTarget | None = None
    fact: str | None = None
    speaker: str | None = None
    replacement: str | None = None
    revision_wire_id: str | None = None
    revision_edition: int | None = None
    revision_text: str | None = None

    @classmethod
    def from_dict(cls, value):
        target = value.get('target')
        revision_text = value.get('revision_text', value.get('revised_text'))
        return cls(
            id=value['id'], text=value.get('text', ''),
            op=normalize_op(value.get('op')),
            target=FragmentTarget.from_dict(target) if target else None,
            fact=value.get('fact'), speaker=value.get('speaker'),
            replacement=value.get('replacement'),
            revision_wire_id=value.get('revision_wire_id', value.get('wire_id')),
            revision_edition=value.get('revision_edition', value.get('edition')),
            revision_text=revision_text,
        )


@dataclass(frozen=True)
class Candidate:
    """候选原文片段及其依赖它的稿件事实，供编辑确认归属。

    同一片段可能同时被短讯、专题稿件和供稿引用；候选按原文坐标去重，
    ``dependents`` 列出依赖它的全部（稿件, 事实）。
    """

    id: str
    wire_id: str
    edition: int
    start: int
    end: int
    text: str
    speaker: str | None = None
    score: float = 0.0
    clues: tuple = ()
    doubts: tuple = ()
    role: str = 'fact'
    dependents: tuple = ()
    occurrence: str | None = None

    @property
    def claim_id(self):
        return self.dependents[0][0] if self.dependents else None

    @property
    def fact_id(self):
        return self.dependents[0][1] if self.dependents else None

    def target(self, claim_id=None, fact_id=None):
        if claim_id is None and self.dependents:
            claim_id = self.dependents[0][0]
        if fact_id is None and claim_id is not None:
            for dependent_claim, dependent_fact in self.dependents:
                if dependent_claim == claim_id:
                    fact_id = dependent_fact
                    break
        return FragmentTarget(self.wire_id, self.edition, self.start, self.end, self.text,
                              claim_id, fact_id)

    def to_dict(self):
        return {
            'id': self.id, 'wire_id': self.wire_id, 'edition': self.edition,
            'start': self.start, 'end': self.end, 'text': self.text,
            'speaker': self.speaker, 'score': round(self.score, 3),
            'clues': list(self.clues), 'doubts': list(self.doubts), 'role': self.role,
            'dependents': [{'claim_id': claim_id, 'fact_id': fact_id}
                           for claim_id, fact_id in self.dependents],
            'occurrence': self.occurrence,
        }


@dataclass
class NoticeAssessment:
    notice_id: str
    operation: str
    candidates: list = field(default_factory=list)
    ambiguous: bool = False
    doubts: list = field(default_factory=list)
    resolved: Candidate | None = None

    @property
    def status(self):
        if self.resolved is not None and not self.ambiguous:
            return 'resolved'
        return 'needs_editor'

    def to_dict(self):
        return {
            'notice': self.notice_id, 'operation': self.operation,
            'status': self.status, 'ambiguous': self.ambiguous,
            'doubts': list(self.doubts),
            'resolved': self.resolved.to_dict() if self.resolved else None,
            'candidates': [candidate.to_dict() for candidate in self.candidates],
        }


def _clean_query(text):
    cleaned = text
    for phrase in sorted(BOILERPLATE, key=len, reverse=True):
        cleaned = cleaned.replace(phrase, ' ')
    return cleaned


def query_terms(text):
    cleaned = _clean_query(text)
    terms = []
    for term in keywords(cleaned):
        if term not in BOILERPLATE and term not in terms:
            terms.append(term)
    return terms


def _sentence_fragments(text):
    fragments = []
    start = 0
    for index, char in enumerate(text):
        if char in '。！？；\n':
            if index > start:
                fragments.append((start, index + 1))
            start = index + 1
    if start < len(text):
        fragments.append((start, len(text)))
    return fragments


def _mentions(text, speaker):
    if not speaker:
        return False
    return speaker in text


DATE_PATTERN = re.compile(r'(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日|(?<![\d月])(\d{1,2})\s*日')
RELATIVE_OCCURRENCE = ('当晚', '当日', '当天', '次日', '翌日', '前一日', '同日')


def _date_in(text):
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    month, day, lone_day = match.groups()
    if month and day:
        return f'{int(month)}月{int(day)}日'
    if lone_day:
        return f'{int(lone_day)}日'
    return None


def occurrence_of(wire, fragment=None):
    """把片段放回具体一次事件。

    先看片段自身的日期，其次看原稿登记的日期或正文日期，再看片段里的
    “当晚/次日”等相对说法；都没有时退回原稿标识。同一份稿里的两次停运
    因此会落成两个不同事件。
    """
    if fragment:
        found = _date_in(fragment)
        if found:
            return found
        for term in RELATIVE_OCCURRENCE:
            if term in fragment:
                return term
    if wire.occurred_at:
        return wire.occurred_at
    found = _date_in(wire.text)
    if found:
        return found
    for term in RELATIVE_OCCURRENCE:
        if term in wire.text:
            return term
    return f'wire:{wire.id}'


def _dates_in(text):
    return [match.group(0).replace(' ', '') for match in DATE_PATTERN.finditer(text)]


def _negated_dates(text):
    result = []
    for match in DATE_PATTERN.finditer(text):
        before = text[max(0, match.start() - 4):match.start()]
        if any(char in before for char in NEGATION_CHARS):
            month, day, lone_day = match.groups()
            if month and day:
                result.append(f'{int(month)}月{int(day)}日')
            elif lone_day:
                result.append(f'{int(lone_day)}日')
    return result


def _quoted_phrases(text):
    result = []
    for left, right in (('“', '”'), ('「', '」'), ('"', '"'), ("'", "'")):
        start = 0
        while True:
            begin = text.find(left, start)
            if begin < 0:
                break
            end = text.find(right, begin + 1)
            if end < 0:
                break
            phrase = text[begin + 1:end].strip()
            if phrase:
                result.append(phrase)
            start = end + 1
    return result


def _denial_targets(text):
    """找出“没有说过/未曾”之后被否认的引语对象，用于撤回或否认类更正。"""
    markers = ('没有说过', '未说过', '没说过', '并未说过', '没有提到', '未提及', '否认')
    return any(marker in text for marker in markers)


def _changes_polarity(old, new):
    old_negated = any(char in NEGATION_CHARS for char in old)
    new_negated = any(char in NEGATION_CHARS for char in new)
    old_conditional = any(marker in old for marker in CONDITIONAL_MARKERS)
    new_conditional = any(marker in new for marker in CONDITIONAL_MARKERS)
    return old_negated != new_negated or old_conditional != new_conditional


class CorrectionLocator:
    """在没有明确稿号时，把一条更正定位到具体事实和原文片段。"""

    def __init__(self, archive, claims):
        self.archive = archive
        self.claims = claims

    # ---- 语料 -------------------------------------------------------
    def _all_speakers(self):
        speakers = set()
        for wire in self.archive.all():
            if wire.speaker:
                speakers.add(wire.speaker)
        for claim in self.claims.values():
            for unit in claim.units():
                if unit[4]:
                    speakers.add(unit[4])
        return sorted(speakers)

    def _revision_span(self, notice):
        if not notice.revision_text or not notice.revision_wire_id:
            return None
        edition = notice.revision_edition
        source = (self.archive.get(notice.revision_wire_id, edition) if edition
                  else self.archive.latest(notice.revision_wire_id))
        if source is None:
            return None
        old, new = source.text, notice.revision_text
        prefix = 0
        limit = min(len(old), len(new))
        while prefix < limit and old[prefix] == new[prefix]:
            prefix += 1
        suffix = 0
        while suffix < limit - prefix and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]:
            suffix += 1
        old_end = len(old) - suffix
        new_end = len(new) - suffix
        return {
            'wire_id': source.id, 'edition': source.edition,
            'start': prefix, 'end': old_end, 'text': old[prefix:old_end],
            'new_text': new[prefix:new_end],
        }

    def _terms_for(self, notice, revision):
        terms = query_terms(notice.text)
        if revision:
            terms += [term for term in keywords(revision['text'] + ' ' + revision['new_text'])
                      if term not in terms]
        return terms

    def _score(self, notice, terms, revision, unit_text, clip_text, wire, speaker,
               occurrence=None, clip=None):
        haystack = ' '.join(filter(None, [unit_text, clip_text, wire.text]))
        hay_terms = set(keywords(haystack))
        matched = [term for term in terms if term in hay_terms or term in haystack]
        coverage = len({term for term in matched}) / len(set(terms)) if terms else 0.0
        score = coverage
        clues = []
        quoted = [term for term in terms
                  if len(term) > 1 and term in notice.text and term in clip_text]
        if clip_text and clip_text in notice.text:
            score += 0.6
            clues.append('更正文直接引用该摘句')
        elif quoted:
            score += 0.15 * min(len(quoted), 4)
            clues.append('用词与摘句重合')
        named = names_in(notice.text, self._all_speakers())
        if speaker and speaker in named:
            score += 0.3
            clues.append(f'提到说话者{speaker}')
        if speaker and named and speaker not in named:
            score -= 0.35
        times = relative_time_terms(notice.text)
        if times:
            wire_time = wire.occurred_at or ''
            if any(term in wire.text or (wire_time and term in wire_time) for term in times):
                score += 0.12
                clues.append('相对时间与该稿相符')
        dates = set(_dates_in(notice.text))
        if occurrence:
            occurrence_date = _date_in(occurrence)
            if occurrence_date and occurrence_date in dates:
                score += 0.3
                clues.append(f'更正提到的日期与片段日期一致（{occurrence_date}）')
            for negated in _negated_dates(notice.text):
                if occurrence_date and negated == occurrence_date:
                    score += 0.25
                    clues.append(f'更正否定了该片段的日期（{negated}），它可能正是要改的说法')
                    break
        if revision and clip is not None and wire.id == revision['wire_id'] \
                and revision['edition'] == clip.edition \
                and revision['start'] < clip.end and revision['end'] > clip.start:
            score += 0.8
            clues.append('修订稿改动落在该摘句上')
        if notice.target and notice.target.wire_id == wire.id \
                and notice.target.edition == wire.edition:
            score += 0.2
            clues.append('更正登记了该稿号和版次')
        return score, clues

    def _doubts(self, notice, revision, unit_text, clip, wire, speaker, named):
        doubts = []
        if speaker and named and speaker not in named and (clip.kind == 'quote' or unit_text != clip.text):
            doubts.append(f'更正提到的不是该引语的说话者{speaker}，可能指向他人言论')
        if named and not speaker and clip.kind == 'quote' and wire.speaker:
            doubts.append(f'该引语的实际说话者为{wire.speaker}，与更正提及的人不同')
        if notice.replacement and _changes_polarity(clip.text, notice.replacement):
            doubts.append('替换文字改变了否定或条件，可能把原话裁成相反意思')
        if revision and revision['new_text'] and _changes_polarity(revision['text'], revision['new_text']):
            doubts.append('修订稿改变了否定或条件，可能把原话裁成相反意思')
        if _denial_targets(notice.text) and clip.kind == 'quote' and notice.speaker \
                and wire.speaker and notice.speaker != wire.speaker:
            doubts.append(f'更正文否认的是{notice.speaker}的话，而该引语登记为{wire.speaker}所说')
        if clip.kind == 'quote' and wire.speaker and notice.speaker and notice.speaker != wire.speaker \
                and not _denial_targets(notice.text):
            doubts.append(f'更正提到{notice.speaker}，而该引语登记为{wire.speaker}所说')
        if clip.kind == 'quote' and wire.speaker and named and wire.speaker not in named                 and not notice.speaker:
            doubts.append(f'更正提到的人{("、".join(named))}不是该引语的说话者{wire.speaker}')
        for phrase in _quoted_phrases(notice.text):
            bare_phrase = phrase.rstrip('。！？；')
            bare_clip = (clip.text or '').rstrip('。！？；')
            if bare_phrase and bare_clip.startswith(bare_phrase) and len(bare_phrase) < len(bare_clip):
                doubts.append('更正文只引用了该句的一部分，可能只影响其中一项事实')
        return doubts

    # ---- 候选 -------------------------------------------------------
    def _group_key(self, clip):
        return (clip.wire_id, clip.edition, clip.start, clip.end)

    def candidates(self, notice):
        """按原文坐标归集候选，并附上依赖该片段的全部稿件事实。"""
        terms = self._terms_for(notice, None)
        revision = self._revision_span(notice)
        named = names_in(notice.text, self._all_speakers())
        groups = {}
        for claim in self.claims.values():
            units = claim.units()
            multi = len(units) > 1
            for unit_id, unit_text, clips, named_quote, unit_speaker, role in units:
                if notice.fact and notice.fact not in (unit_id, unit_text):
                    continue
                if notice.speaker and unit_speaker and notice.speaker != unit_speaker:
                    continue
                for clip in clips:
                    wire = self.archive.get(clip.wire_id, clip.edition)
                    if wire is None:
                        continue
                    key = (clip.wire_id, clip.edition, clip.start, clip.end)
                    bucket = groups.get(key)
                    if bucket is None:
                        score, clues = self._score(notice, terms, revision, unit_text,
                                                   clip.text, wire, unit_speaker,
                                                   occurrence_of(wire, clip.text), clip)
                        doubts = self._doubts(notice, revision, unit_text, clip, wire,
                                              unit_speaker, named)
                        bucket = {
                            'key': key, 'wire': wire, 'clip': clip, 'score': score,
                            'clues': list(clues), 'doubts': list(doubts),
                            'dependents': [], 'speakers': set(),
                        }
                        groups[key] = bucket
                    bucket['dependents'].append((claim.id, unit_id if multi else None))
                    if unit_speaker:
                        bucket['speakers'].add(unit_speaker)
                        if multi and named and unit_speaker not in named:
                            bucket['doubts'].append(
                                f'同一段转述中另一事实属于{unit_speaker}，更正未必针对本事实')
        # 同一段文字、同一次事件出现在数份来源稿本时归为一组；日期不同的是两次相似停运。
        # 说话者不同时，即使一字不差也是不同的引语，不能并成一个候选。
        by_statement = {}
        for key, bucket in groups.items():
            occurrence = occurrence_of(bucket['wire'], bucket['clip'].text)
            label = bucket['wire'].speaker
            by_statement.setdefault((bucket['clip'].text, occurrence, label), []).append(bucket)
        result = []
        for (text, occurrence, label), buckets in by_statement.items():
            buckets.sort(key=lambda item: (-item['score'], item['key']))
            primary = buckets[0]
            others = buckets[1:]
            clues = list(primary['clues'])
            doubts = list(primary['doubts'])
            similar = [item for item in others if item['score'] >= primary['score'] - 0.05]
            if similar:
                clues.append('同文另有来源稿本：' + '、'.join(
                    f"{item['key'][0]}/{item['key'][1]}" for item in similar))
                speakers = {item['wire'].speaker for item in buckets
                            if item['wire'].speaker}
                all_speakers = set(bucket['speakers']) | speakers
                if len(all_speakers) > 1:
                    doubts.append('同样的话出现在不同说话者的稿件中，需确认更正针对谁的发言')
            dependents = tuple(dict.fromkeys(
                dependent for item in buckets for dependent in item['dependents']))
            result.append(Candidate(
                id=(f"{primary['key'][0]}@{primary['key'][1]}:"
                    f"{primary['key'][2]}-{primary['key'][3]}"),
                wire_id=primary['key'][0], edition=primary['key'][1],
                start=primary['key'][2], end=primary['key'][3], text=text,
                speaker=label, score=primary['score'],
                clues=tuple(dict.fromkeys(clues)), doubts=tuple(dict.fromkeys(doubts)),
                role='fact' if dependents else 'source',
                dependents=dependents, occurrence=occurrence))
        result.extend(self._unattached(notice, terms, revision, groups))
        return sorted(result, key=lambda item: (-item.score, item.id))

    def _unattached(self, notice, terms, revision, attached):
        """尚未编入稿件、但可能正是更正所指的原文片段。"""
        if not terms:
            return []
        result = []
        attached_keys = set(attached)
        for wire in self.archive.all():
            hay_terms = set(keywords(wire.text))
            if not any(term in hay_terms or term in wire.text for term in terms):
                continue
            for start, end in _sentence_fragments(wire.text):
                if (wire.id, wire.edition, start, end) in attached_keys:
                    continue
                fragment = wire.text[start:end]
                fragment_terms = set(keywords(fragment))
                local = [term for term in terms if term in fragment_terms or term in fragment]
                if not local:
                    continue
                score = 0.45 * len(set(local)) / len(set(terms))
                if revision and wire.id == revision['wire_id'] and revision['start'] < end and revision['end'] > start:
                    score += 0.5
                if score < 0.2:
                    continue
                result.append(Candidate(
                    id=f'unattached:{wire.id}@{wire.edition}:{start}-{end}',
                    wire_id=wire.id, edition=wire.edition, start=start, end=end,
                    text=fragment, speaker=wire.speaker, score=score,
                    clues=('尚未编入稿件',), doubts=(), role='source',
                    occurrence=occurrence_of(wire, fragment)))
        return result

    # ---- 归属 -------------------------------------------------------
    def assess(self, notice):
        candidates = self.candidates(notice)
        doubts = []
        if not candidates:
            doubts.append('没有找到可对应的原文，需人工指定稿号、版次与字符位置')
            return NoticeAssessment(notice.id, notice.op, [], True, doubts, None)
        if notice.target:
            exact = None
            mapped = None
            for candidate in candidates:
                if (candidate.wire_id, candidate.edition) != (notice.target.wire_id, notice.target.edition):
                    continue
                if (candidate.start, candidate.end) != (notice.target.start, notice.target.end):
                    continue
                if notice.target.claim_id and notice.target.claim_id not in {
                        claim_id for claim_id, _ in candidate.dependents}:
                    continue
                if notice.target.fact_id and notice.target.fact_id not in {
                        fact_id for _, fact_id in candidate.dependents}:
                    continue
                exact = candidate
                break
            if exact is not None:
                return NoticeAssessment(notice.id, notice.op, candidates, False, doubts, exact)
            # 登记坐标属于旧版次、而该稿已有新版时，按映射后的位置仍可确认归属。
            try:
                start, end, latest = self.archive.map_span(
                    notice.target.wire_id, notice.target.edition,
                    notice.target.start, notice.target.end)
            except ValueError:
                start = end = None
            if start is not None:
                for candidate in candidates:
                    if candidate.wire_id != notice.target.wire_id:
                        continue
                    try:
                        cand_start, cand_end, _ = self.archive.map_span(
                            candidate.wire_id, candidate.edition,
                            candidate.start, candidate.end)
                    except ValueError:
                        continue
                    if (cand_start, cand_end) == (start, end):
                        mapped = candidate
                        break
                if mapped is not None:
                    doubts.append('登记坐标属于旧版次，已按修订映射到当前版次')
                    return NoticeAssessment(notice.id, notice.op, candidates,
                                            not mapped.doubts, doubts, mapped)
            doubts.append('登记的原文坐标没有对上任何摘句，仍需按内容核对')
        top = candidates[0]
        if top.score < MIN_CONFIDENCE:
            doubts.append('候选原文与更正内容的对应关系不够明确，不能据稿号或标题把整份旧稿判为失效')
            return NoticeAssessment(notice.id, notice.op, candidates, True, doubts, None)
        close = [candidate for candidate in candidates
                 if candidate.score >= top.score - 0.05 and candidate.score > 0]
        different_statements = {candidate.text for candidate in close}
        different_speakers = {candidate.speaker for candidate in close if candidate.speaker}
        different_occurrences = {candidate.occurrence for candidate in close
                                 if candidate.occurrence}
        if len(close) > 1 and len(different_statements) > 1:
            doubts.append('多个事件或说法都可能对应这条更正')
            for candidate in close:
                doubts.extend(candidate.doubts)
            return NoticeAssessment(notice.id, notice.op, candidates, True,
                                    list(dict.fromkeys(doubts)), None)
        if len(close) > 1 and len(different_speakers) > 1:
            doubts.append('同样的话出现在不同说话者的稿件中，需确认更正针对谁的发言')
            return NoticeAssessment(notice.id, notice.op, candidates, True,
                                    list(dict.fromkeys(doubts)), None)
        if len(close) > 1 and len(different_occurrences) > 1:
            doubts.append('同一说法出现在不同时间，可能是两次相似事件，需确认更正针对哪一次')
            return NoticeAssessment(notice.id, notice.op, candidates, True,
                                    list(dict.fromkeys(doubts)), None)
        if top.doubts:
            return NoticeAssessment(notice.id, notice.op, candidates, True,
                                    list(dict.fromkeys(doubts + list(top.doubts))), None)
        return NoticeAssessment(notice.id, notice.op, candidates, False, doubts, top)
