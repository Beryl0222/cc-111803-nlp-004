from dataclasses import dataclass
from typing import Literal

from dataclasses import replace

from .clips import retarget_clip
from .corrections import Candidate, CorrectionLocator, OP_LABELS


STATUS_LABELS = {
    'queued': '已进入更正工作',
    'awaiting_decision': '等待决定',
    'applied': '已适用',
}


@dataclass(frozen=True)
class CorrectionWork:
    """一项更正产生的工作，按栏目和已发批次登记。"""

    id: str
    notice_id: str
    channel: str
    slot_id: str
    release_ids: tuple
    status: Literal['queued', 'awaiting_decision', 'applied']
    reason: str

    def to_dict(self):
        return {
            'id': self.id, 'notice': self.notice_id, 'channel': self.channel,
            'slot': self.slot_id, 'release_ids': list(self.release_ids),
            'status': self.status, 'status_label': STATUS_LABELS[self.status],
            'reason': self.reason,
        }


class CorrectionDesk:
    """把更正落回具体事实、待发稿和已发稿的值班工作台。

    * 待发稿：被更正触及的事实先留待核查，同一事实的其余部分和不受影响的
      版面可以继续审。
    * 已发稿：按原栏目进入更正工作；供稿另存原稿回执与更正回执。
    * 引用同一原文片段的其他稿件（短讯、专题标题、合作方稿件）一并复核。
    * 同一片段上的两条冲突通知：登记为等待决定，不阻塞其他版面。
    """

    def __init__(self, archive, claims, edition_desk, correction_columns=None, review=None):
        self.archive = archive
        # 与 EditionDesk 共用同一个稿件映射，核签时看到的是最新一份事实。
        self.claims = claims
        self.review = review or edition_desk.review
        self.editions = edition_desk
        self.columns = dict(correction_columns or {})
        self.notices = {}
        self.assessments = {}
        self.applied = {}
        self.applied_targets = {}
        self.works = []
        self.held = {}
        self.pending = {}
        self.rechecks = []
        self.partner_archive = []
        self.locator = CorrectionLocator(archive, self.claims)

    # ---- 登记与归属 -------------------------------------------------
    def register(self, notice):
        if notice.id in self.notices:
            raise ValueError(f'更正编号已使用：{notice.id}')
        self.notices[notice.id] = notice
        assessment = self.locator.assess(notice)
        self.assessments[notice.id] = assessment
        if assessment.status != 'resolved':
            # 归属未定时，所有可能受影响的待发事实先留待核查。
            self._hold_pending(notice.id, self._candidate_slots(assessment))
        return assessment.to_dict()

    def _hold_pending(self, notice_id, slots):
        slots = list(slots)
        self.pending[notice_id] = {
            'slots': {slot.id for slot in slots},
            'claims': {claim_id for slot in slots for claim_id in slot.claim_ids},
        }

    def _candidate_slots(self, assessment, candidate=None):
        """列出可能受这条更正影响的待发版面。

        归属未定时只算与首位得分接近的候选；编辑确认归属后只算该片段被引用
        到的版面，使其他版面的审稿不受牵连。
        """
        if candidate is not None:
            chosen = [candidate]
        elif assessment.candidates:
            best = max(item.score for item in assessment.candidates)
            chosen = [item for item in assessment.candidates
                      if item.score >= best - 0.05 and item.score > 0]
        else:
            chosen = []
        slots = set()
        for item in chosen:
            for claim_id, _ in item.dependents:
                for slot in self.editions.slots.values():
                    if claim_id in slot.claim_ids:
                        slots.add(slot)
        return slots

    def _assessment(self, notice_id):
        if notice_id not in self.assessments:
            notice = self.notices.get(notice_id)
            if notice is None:
                raise ValueError(f'没有这条更正：{notice_id}')
            self.assessments[notice_id] = self.locator.assess(notice)
        return self.assessments[notice_id]

    def assess_notice(self, notice_id):
        return self._assessment(notice_id).to_dict()

    def unresolved(self):
        return [self.assess_notice(notice_id) for notice_id in sorted(self.notices)
                if self._assessment(notice_id).status != 'resolved']

    def attribute(self, notice_id, candidate_id):
        """编辑确认整条更正归属到某个候选片段。"""
        assessment = self._assessment(notice_id)
        for candidate in assessment.candidates:
            if candidate.id == candidate_id:
                assessment.resolved = candidate
                assessment.ambiguous = False
                self._hold_pending(notice_id, self._candidate_slots(assessment, candidate))
                return assessment.to_dict()
        raise ValueError(f'候选不存在：{candidate_id}')

    def attribute_manual(self, notice_id, wire_id, edition, start, end, text=None):
        """编辑直接填写稿号、版次与字符位置时也应能确认归属。"""
        assessment = self._assessment(notice_id)
        wire = self.archive.get(wire_id, edition)
        if wire is None:
            raise ValueError(f'无法找到原稿：{wire_id}/{edition}')
        if not 0 <= start < end <= len(wire.text):
            raise ValueError('坐标超出原稿')
        actual = wire.text[start:end]
        if text is not None and actual != text:
            raise ValueError('坐标处的原文与所给文字不符')
        candidate = Candidate(
            id=f'manual:{wire_id}@{edition}:{start}-{end}', wire_id=wire_id,
            edition=edition, start=start, end=end, text=actual, speaker=wire.speaker,
            score=1.0, clues=('编辑确认',), dependents=self._dependents(
                wire_id, edition, start, end), occurrence=None)
        assessment.candidates.append(candidate)
        assessment.resolved = candidate
        assessment.ambiguous = False
        self._hold_pending(notice_id, self._candidate_slots(assessment, candidate))
        return assessment.to_dict()

    def _dependents(self, wire_id, edition, start, end):
        """列出引用该原文坐标的全部（稿件, 事实）。"""
        found = []
        for claim in self.claims.values():
            multi = len(claim.units()) > 1
            for unit in claim.units():
                for clip in unit[2]:
                    if clip.key == (wire_id, edition, start, end):
                        found.append((claim.id, unit[0] if multi else None))
        return tuple(dict.fromkeys(found))

    # ---- 适用 -------------------------------------------------------
    def apply(self, notice_id, replacement=None, claim_id=None, fact_id=None):
        """适用一条更正，返回受影响的待发稿、已发稿和依赖稿件。

        ``claim_id``/``fact_id`` 用于同一片段被多处引用时指明改哪一项事实。
        归属仍有歧义或疑点时拒绝适用，避免把更正安到错误事件或说话者头上。
        """
        notice = self.notices[notice_id]
        assessment = self._assessment(notice_id)
        if assessment.ambiguous or assessment.resolved is None:
            raise ValueError('更正归属尚未确认，需先由编辑指定候选原文')
        candidate = assessment.resolved
        target = self._choose_target(candidate, notice, claim_id, fact_id)
        self.pending.pop(notice_id, None)
        conflict = self._conflict_for(notice, target)
        if conflict is not None:
            slot = self._slot_for_claim(target.claim_id)
            work = self._new_work(notice, self._column_for(slot.channel), slot,
                                  self._releases(slot), 'awaiting_decision',
                                  f'同一片段已有另一条更正（{conflict}）等待决定')
            self.applied_targets[notice.id] = target
            return {'notice': notice_id, 'status': 'awaiting_decision',
                    'conflict': conflict, 'work': work.to_dict()}

        claim = self.claims[target.claim_id]
        if notice.op == 'retraction':
            self._retract(notice, claim, target)
            edition = None
        else:
            edition = self._rewrite(notice, claim, target, replacement)
        reused = False
        if notice.op == 'typo':
            reused = self._recheck(notice, claim, target, edition)
        self.applied[notice_id] = {
            'notice': notice_id, 'claim_id': claim.id, 'fact_id': target.fact_id,
            'wire_id': target.wire_id, 'edition': target.edition,
            'start': target.start, 'end': target.end, 'old_text': target.text,
            'new_edition': edition.edition if edition else None,
        }
        self.applied_targets[notice_id] = target
        claim = self.claims[claim.id]
        impact = self._mark_impact(notice, target, claim, reuse_verification=reused,
                                   dependents=candidate.dependents)
        reviews = []
        for slot in self.editions.slots.values():
            if claim.id in slot.claim_ids:
                row = self.review.check_claim(claim, slot.channel)
                reviews.append({'slot': slot.id, 'channel': slot.channel,
                                'approved': row['approved'], 'support': row['support'],
                                'desks': row['desks'], 'issues': row['issues']})
        return {
            'notice': notice_id, 'status': 'applied', 'operation': notice.op,
            'fact_id': target.fact_id, 'claim': claim.id,
            'target': {'wire_id': target.wire_id, 'edition': target.edition,
                       'start': target.start, 'end': target.end, 'text': target.text},
            'replacement_edition': edition.edition if edition else None,
            'impact': impact, 'reviews': reviews,
        }

    def _choose_target(self, candidate, notice, claim_id, fact_id):
        if not candidate.dependents:
            raise ValueError('该候选原文尚未编入任何稿件事实，无法判断更正影响哪项陈述')
        if claim_id is None and notice.target and notice.target.claim_id:
            claim_id = notice.target.claim_id
        if fact_id is None and notice.target and notice.target.fact_id:
            fact_id = notice.target.fact_id
        if claim_id is None:
            # 同一原文片段常同时被短讯、专题稿件或供稿引用；这些是同一说法，
            # 默认落在引用它的第一项事实，其余依赖稿件随更正一并复核。
            claim_id = candidate.dependents[0][0]
        dependents = [row for row in candidate.dependents if row[0] == claim_id]
        if not dependents:
            raise ValueError('更正所指片段不在这项事实中')
        if fact_id is not None and not any(
                row[1] in (None, fact_id) for row in dependents):
            raise ValueError('更正所指片段不在指定的这项事实中')
        if fact_id is None:
            fact_id = dependents[0][1]
        return candidate.target(claim_id, fact_id)

    def _revision_replacement(self, notice, target):
        """修订稿按新旧原文比对出改动片段，用作替换文字。"""
        if not notice.revision_text:
            return None
        span = self.locator._revision_span(notice)
        if span is None:
            return None
        if span['wire_id'] != target.wire_id:
            raise ValueError('修订稿与该更正指向的稿号不同，不能直接套用')
        if span['edition'] != target.edition:
            raise ValueError('修订稿依据的版次与该更正指向的版次不同，需人工核对')
        if not (target.start <= span['start'] and span['end'] <= target.end):
            raise ValueError('修订稿的改动片段超出该更正指向的片段，需人工核对')
        # 修订稿的改动落在目标片段内部：把局部改动叠回目标片段。
        offset_start = span['start'] - target.start
        offset_end = span['end'] - target.start
        return target.text[:offset_start] + span['new_text'] + target.text[offset_end:]

    def _rewrite(self, notice, claim, target, replacement):
        if replacement is None and notice.op == 'correction':
            replacement = self._revision_replacement(notice, target)
        addition = replacement if replacement is not None else notice.replacement
        clip = self._find_clip(claim, target)
        if notice.op == 'addition':
            if addition is None or not addition.strip():
                raise ValueError('补充通知没有给出补充文字')
            new_text = target.text + addition
        else:
            new_text = addition
            if new_text is None:
                raise ValueError('更正文没有给出替换文字，需由编辑补充')
            if not new_text.strip():
                raise ValueError('更正后的文字不能为空')
            if new_text == target.text:
                raise ValueError('更正后的文字与原片段相同，无法判断改动内容')
        speaker = notice.speaker or None
        new_edition, start, end = self.archive.corrected_edition(
            target.wire_id, target.edition, target.start, target.end, target.text,
            new_text, speaker=speaker)
        supports = 'narrower' if notice.op == 'clarification' else clip.supports
        updated = retarget_clip(clip, new_edition, start, end, new_text,
                                speaker=speaker if clip.kind == 'quote' else None,
                                supports=supports)
        self.claims[claim.id] = self._replace_clip(claim, target, updated)
        return new_edition

    def _retract(self, notice, claim, target):
        """撤回依据但保留摘句与坐标，使原句仍可回查。"""
        clip = self._find_clip(claim, target)
        self.claims[claim.id] = self._replace_clip(claim, target, replace(clip, retracted=True))

    def _replace_clip(self, claim, target, updated):
        """只改动被点中的事实里对应的摘句，其他事实原样保留。"""
        found = False
        new_units = []
        for unit in claim.units():
            unit_id, unit_text, clips, named_quote, speaker, role = unit
            touched = target.fact_id in (None, unit_id)
            new_clips = []
            for clip in clips:
                if touched and clip.key == target.key:
                    found = True
                    new_clips.append(updated)
                else:
                    new_clips.append(clip)
            new_units.append((unit_id, unit_text, tuple(new_clips), named_quote, speaker, role))
        if not found:
            raise ValueError('更正所指摘句已不在这项事实中')
        return claim.with_units(new_units)

    def _find_clip(self, claim, target):
        for unit in claim.units():
            for clip in unit[2]:
                if clip.key == target.key:
                    return clip
        raise ValueError('更正所指摘句已不在这项事实中')

    def _slot_for_claim(self, claim_id):
        for slot in self.editions.slots.values():
            if claim_id in slot.claim_ids:
                return slot
        raise ValueError(f'没有包含该事实的版面：{claim_id}')

    def _column_for(self, channel):
        return self.columns.get(channel, f'{channel}更正')

    def _releases(self, slot):
        return [packet['release_id'] for packet in self.editions.published_for_slot(slot.id)]

    def _new_work(self, notice, channel, slot, release_ids, status, reason):
        work = CorrectionWork(f'work-{len(self.works) + 1}', notice.id, channel, slot.id,
                              tuple(release_ids), status, reason)
        self.works.append(work)
        return work

    def _conflict_for(self, notice, target):
        """同一原文片段的另一条更正也待生效时，返回那条通知的编号。

        修订会生成新版次，所以比较的是映射到最新版次后的区间，而不是登记时的
        版次号：两条通知各自指向同一处原文叠写的不同旧版，也算冲突。
        """
        for notice_id, applied in self.applied_targets.items():
            if notice_id == notice.id:
                continue
            if applied.wire_id != target.wire_id:
                continue
            if applied.key == target.key:
                return notice_id
            if self._spans_overlap(applied, target):
                return notice_id
        return None

    def _spans_overlap(self, left, right):
        try:
            l_start, l_end, l_wire = self.archive.map_span(
                left.wire_id, left.edition, left.start, left.end)
            r_start, r_end, r_wire = self.archive.map_span(
                right.wire_id, right.edition, right.start, right.end)
        except ValueError:
            return False
        if l_wire.edition != r_wire.edition:
            return False
        return l_start < r_end and r_start < l_end

    def _recheck(self, notice, claim, target, new_edition):
        """手误更正沿用核实结论，但保留新旧原文以便回查。

        先找回改写后的摘句：只有引语意义（否定、条件）和说话者都没变，才可
        沿用原核实结论。转述句没有具名说话者，意义不变即可沿用。
        """
        if new_edition is None:
            return False
        old_clip = self._find_clip(claim, target)
        updated = None
        for unit in claim.units():
            if target.fact_id is not None and unit[0] != target.fact_id:
                continue
            for clip in unit[2]:
                if clip.edition == new_edition.edition and clip.kind == old_clip.kind:
                    updated = clip
                    break
            if updated is not None:
                break
        if updated is not None:
            new_text = new_edition.text[updated.start:updated.end]
            new_span = {'edition': new_edition.edition, 'start': updated.start,
                        'end': updated.end, 'text': new_text}
            speaker_unchanged = (updated.speaker or '') == (old_clip.speaker or '')
        else:
            new_text = new_edition.text
            new_span = {'edition': new_edition.edition, 'start': 0,
                        'end': len(new_text), 'text': new_text}
            speaker_unchanged = not old_clip.speaker
        meaning_unchanged = not _meaning_changed(target.text, new_text)
        reused = (meaning_unchanged and speaker_unchanged
                  and _typo_is_cosmetic(target.text, new_text, self._speakers()))
        self.rechecks.append({
            'notice': notice.id, 'wire_id': target.wire_id,
            'fact_id': target.fact_id,
            'old': {'edition': target.edition, 'start': target.start,
                    'end': target.end, 'text': target.text},
            'new': new_span,
            'meaning_unchanged': meaning_unchanged,
            'speaker_unchanged': speaker_unchanged,
            'verification_reused': reused,
        })
        return reused

    def _mark_impact(self, notice, target, primary_claim, reuse_verification=False,
                     dependents=()):
        """按栏目判断更正是否推翻当前核签。

        * 撤回后剩余证据仍够该栏目使用：原核签照旧，只把支撑标记为原说法、
          较窄说法或仅身份。
        * 受影响事实不能再核签：待发稿留待核查，已发稿进入相应栏目的更正工作。
        * 只修手误且意义、说话人未变：沿用核实结论，待发稿照常。
        """
        impact = {'held_slots': [], 'works': [], 'reviewable_slots': [],
                  'dependent_claims': [], 'signoff_stands': []}
        handled = set()
        # 被更正的同一说法可能还被短讯、专题标题或合作方稿件引用；按候选登记
        # 的依赖关系一并复核，而不只看摘句坐标是否相同。
        affected = {primary_claim.id: target.fact_id}
        for claim_id, fact_id in dependents:
            affected.setdefault(claim_id, fact_id)
        for claim in list(self.claims.values()):
            if claim.id == primary_claim.id:
                continue
            if any(clip.key == target.key for unit in claim.units() for clip in unit[2]):
                affected.setdefault(claim.id, None)
        for claim_id, fact_id in affected.items():
            claim = self.claims.get(claim_id)
            if claim is None:
                continue
            if claim.id != primary_claim.id and claim.id not in impact['dependent_claims']:
                impact['dependent_claims'].append(claim.id)
            for slot in self.editions.slots.values():
                if claim.id not in slot.claim_ids or slot.id in handled:
                    continue
                handled.add(slot.id)
                self._impact_for_slot(notice, claim, fact_id, slot, impact,
                                      reuse_verification)
        return impact

    def _impact_for_slot(self, notice, claim, fact_id, slot, impact,
                         reuse_verification=False):
        units = claim.units()
        reviewable_units = [unit[0] for unit in units
                            if fact_id is not None and unit[0] != fact_id]
        affected = [unit for unit in units if fact_id in (None, unit[0])]
        if reuse_verification:
            # 只修手误：核实结论沿用，待发稿不必留待核查，但仍记录回查坐标。
            impact['signoff_stands'].append({
                'slot': slot.id, 'claim_id': claim.id,
                'reason': '仅修手误，事实、引语意义与说话者未变，沿用核实结论',
            })
            return
        if notice.op in ('retraction', 'addition'):
            # 撤回或补充不改动原句说法本身：看剩余/新增证据是否仍够该栏目使用。
            row = self.review.check_claim(claim, slot.channel, only_units={
                unit[0] for unit in affected})
            if row['approved']:
                impact['signoff_stands'].append({
                    'slot': slot.id, 'claim_id': claim.id,
                    'support': row['support'], 'desks': row['desks'],
                    'signoff': self._signoff_label(row['support']),
                    'reason': '其余证据仍够本栏目使用，原核签照旧',
                })
                return
        # 更正、说明改动了事实或引语本身，推定推翻原核签，待发稿先留待核查。
        published = self.editions.published_for_slot(slot.id)
        if published:
            column = self._column_for(slot.channel)
            reason = f'{OP_LABELS[notice.op]}推翻了原核签，需在{column}刊发更正'
            work = self._new_work(notice, column, slot,
                                  [packet['release_id'] for packet in published],
                                  'queued', reason)
            self._archive_partner(notice, slot, published, work)
            impact['works'].append(work.to_dict())
            return
        self.held.setdefault(slot.id, {})[claim.id] = {
            'fact_id': fact_id, 'notice': notice.id,
        }
        if reviewable_units:
            impact['reviewable_slots'].append(slot.id)
        impact['held_slots'].append({
            'slot': slot.id, 'claim_id': claim.id, 'fact_id': fact_id,
            'other_facts_reviewable': bool(reviewable_units),
            'reviewable_units': reviewable_units,
            'reason': ('受影响事实留待核查，其余事实可继续审' if reviewable_units
                       else '受更正影响，留待核查'),
        })

    def _speakers(self):
        names = set()
        for wire in self.archive.all():
            if wire.speaker:
                names.add(wire.speaker)
        for claim in self.claims.values():
            for unit in claim.units():
                if unit[4]:
                    names.add(unit[4])
        return sorted(names)

    def _signoff_label(self, support):
        return {'original': '支撑原说法', 'narrower': '只支撑较窄的说法',
                'identity': '只支撑说话者身份', 'none': '已无依据'}.get(support, support)

    def _archive_partner(self, notice, slot, published, work):
        if '供稿' not in slot.channel:
            return
        self.partner_archive.append({
            'slot': slot.id, 'channel': slot.channel, 'notice': notice.id,
            'release_ids': [packet['release_id'] for packet in published],
            'original': [self.editions.receipt(packet['release_id']) for packet in published],
            'correction_receipt': work.to_dict(),
        })

    # ---- 预览 -------------------------------------------------------
    def preview(self, notice_id, retract_keys=None):
        """改稿前查看各栏目在撤回该依据后还剩什么支撑。

        返回每条稿件事实改前/改后的核签结果与 ``support``：原说法、较窄说法、
        仅支持说话者身份，或已无依据。撤回类通知默认预览撤掉目标片段后的结果。
        """
        notice = self.notices[notice_id]
        assessment = self._assessment(notice_id)
        if assessment.resolved is not None:
            target = assessment.resolved.target()
        elif notice.target is not None:
            target = notice.target
        else:
            raise ValueError('更正归属尚未确认，无法预览影响')
        if retract_keys is None:
            retract_keys = {target.key} if notice.op == 'retraction' else set()
        rows = []
        for slot in self.editions.slots.values():
            claim_id = target.claim_id if target.claim_id else None
            if claim_id is not None:
                candidates = [claim_id] if claim_id in slot.claim_ids else []
            else:
                candidates = list(slot.claim_ids)
            for candidate_id in candidates:
                claim = self.claims[candidate_id]
                before = self.review.check_claim(claim, slot.channel)
                after = self.review.check_claim(claim, slot.channel,
                                                exclude_keys=set(retract_keys))
                before_units = {unit['id']: unit for unit in before['units']}
                after_units = {unit['id']: unit for unit in after['units']}
                rows.append({
                    'slot': slot.id, 'channel': slot.channel, 'claim': candidate_id,
                    'approved_before': before['approved'],
                    'approved_after': after['approved'],
                    'support_before': before['support'],
                    'support_after': after['support'],
                    'desks_after': after['desks'],
                    'issues_after': after['issues'],
                    'units': [{
                        'id': unit_id,
                        'support_before': before_units[unit_id]['support'],
                        'support_after': after_units[unit_id]['support'],
                        'approved_before': before_units[unit_id]['approved'],
                        'approved_after': after_units[unit_id]['approved'],
                        'desks_after': after_units[unit_id]['desks'],
                    } for unit_id in after_units],
                })
        return {'notice': notice_id, 'target': {
            'wire_id': target.wire_id, 'edition': target.edition,
            'start': target.start, 'end': target.end, 'text': target.text,
        }, 'channels': rows}

    # ---- 查询 -------------------------------------------------------
    def corrections_queue(self, channel=None):
        return [work.to_dict() for work in self.works
                if work.status in ('queued', 'awaiting_decision')
                and (channel is None or work.channel == channel)]

    def awaiting_decision(self):
        return [work.to_dict() for work in self.works
                if work.status == 'awaiting_decision']

    def pending_slots(self, notice_id=None):
        if notice_id is not None:
            return sorted(self.pending.get(notice_id, {}).get('slots', ()))
        return {notice: sorted(entry['slots']) for notice, entry in self.pending.items()}

    def held_facts(self, slot_id=None):
        if slot_id is not None:
            return dict(self.held.get(slot_id, {}))
        return {slot: dict(values) for slot, values in self.held.items()}

    def recheck(self):
        return list(self.rechecks)

    def partner_receipts(self):
        return list(self.partner_archive)

    # ---- 核签与刊发 -------------------------------------------------
    def review_slot(self, slot_id):
        """待发稿核签：被更正触及的事实留待核查，其余部分照常审。"""
        rows = self.editions.assess(slot_id)
        held = self.held.get(slot_id, {})
        pending = [(notice_id, entry) for notice_id, entry in self.pending.items()
                   if slot_id in entry['slots']]
        for row in rows:
            row['coordinates'] = self.review.coordinates(self.claims[row['claim']])
            entry = held.get(row['claim'])
            if entry is None:
                for notice_id, pending_entry in pending:
                    if row['claim'] in pending_entry['claims']:
                        entry = {'fact_id': None, 'notice': notice_id}
                        break
            if entry is None:
                continue
            units = row.get('units', [])
            blocked = [unit for unit in units
                       if entry['fact_id'] is None or unit['id'] == entry['fact_id']]
            others = [unit for unit in units
                      if entry['fact_id'] is not None and unit['id'] != entry['fact_id']]
            row['held'] = True
            row['held_fact'] = entry['fact_id']
            row['reviewable_units'] = [unit['id'] for unit in others]
            for unit in blocked:
                unit['approved'] = False
                unit['issues'] = list(dict.fromkeys(unit['issues'] + ['留待核查']))
            if not others:
                row['approved'] = False
                row['issues'] = list(dict.fromkeys(row['issues'] + ['留待核查']))
        return rows

    def assess(self, slot_id):
        """与既有工作台接口保持一致：按版面核签。"""
        return self.review_slot(slot_id)

    def publish(self, slot_id, release_id):
        if self.held.get(slot_id):
            raise ValueError(f'稿件 {slot_id} 有事实留待核查，不能刊发')
        if any(slot_id in entry['slots'] for entry in self.pending.values()):
            raise ValueError(f'稿件 {slot_id} 涉及尚未确认归属的更正，不能刊发')
        return self.editions.publish(slot_id, release_id)

    def receipt(self, release_id):
        return self.editions.receipt(release_id)

    def coordinates(self, claim_id):
        return self.review.coordinates(self.claims[claim_id])


def _typo_is_cosmetic(old, new, speakers=()):
    """手误只应改到个别字。动到数字、日期、否定、条件或人名时按实质更正处理。"""
    prefix = 0
    limit = min(len(old), len(new))
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]:
        suffix += 1
    old_changed = old[prefix:len(old) - suffix]
    new_changed = new[prefix:len(new) - suffix]
    if len(old_changed) > 2 and len(new_changed) > 2:
        return False
    changed = old_changed + new_changed
    if any(char.isdigit() for char in changed):
        return False
    from .clips import NEGATION_CHARS, CONDITIONAL_MARKERS
    if any(char in NEGATION_CHARS for char in changed):
        return False
    if any(marker in changed for marker in CONDITIONAL_MARKERS):
        return False
    if any(name and (name in old_changed or name in new_changed) for name in speakers):
        return False
    if any(token in old_changed + new_changed for token in ('年', '月', '日', '时', '分')):
        return False
    return True


def _meaning_changed(old, new):
    from .clips import NEGATION_CHARS, CONDITIONAL_MARKERS
    if any(char in NEGATION_CHARS for char in old) != any(char in NEGATION_CHARS for char in new):
        return True
    return any(marker in old for marker in CONDITIONAL_MARKERS) != any(
        marker in new for marker in CONDITIONAL_MARKERS)
