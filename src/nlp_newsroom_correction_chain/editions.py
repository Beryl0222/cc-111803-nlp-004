from copy import deepcopy
from dataclasses import asdict, dataclass

from .corrections import CorrectionLedger


@dataclass(frozen=True)
class Slot:
    id: str
    channel: str
    claim_ids: tuple[str, ...]


class EditionDesk:
    """用当前核签结果批准新稿并保存各次刊发的文字和依据。"""

    def __init__(self, review, claims, slots, ledger=None):
        self.review = review
        self.claims = {claim.id: claim for claim in claims}
        self.slots = {slot.id: slot for slot in slots}
        if ledger is None:
            ledger = CorrectionLedger(review.archive, claims=self.claims.values())
        self.ledger = ledger
        self.review.ledger = ledger
        self._published = {}

    # ---- 核签 -------------------------------------------------------
    def assess(self, slot_id):
        slot = self.slots[slot_id]
        return [self.review.check(self.claims[claim_id], slot.channel)
                for claim_id in slot.claim_ids]

    def publish(self, slot_id, release_id, partner=None):
        if release_id in self._published:
            raise ValueError(f'刊发编号已使用：{release_id}')
        reviews = self.assess(slot_id)
        if any(not review['approved'] for review in reviews):
            raise ValueError(f'稿件 {slot_id} 尚未全部核签')
        slot = self.slots[slot_id]
        packet = {'release_id': release_id, 'slot': asdict(slot), 'reviews': reviews,
                  'claims': [asdict(self.claims[claim_id]) for claim_id in slot.claim_ids],
                  'partner': partner,
                  'partner_notices': [], 'partner_acks': [],
                  'corrections': []}
        self._published[release_id] = packet
        return packet

    def receipt(self, release_id):
        return deepcopy(self._published[release_id])

    # ---- 更正定位 ---------------------------------------------------
    def assign(self, notice_id, wire_id, edition, start, end):
        return self.ledger.confirm(notice_id, wire_id, edition, start, end)

    def dismiss(self, notice_id, reason=None):
        return self.ledger.dismiss(notice_id, reason)

    def resolution(self, notice_id):
        return self.ledger.resolution(notice_id)

    def pending_notices(self):
        return self.ledger.pending()

    def conflicts(self):
        return self.ledger.conflicts()

    def impact(self, notice_id):
        """列出该通知影响的栏目、待发稿和已发稿。"""
        notice = self.ledger.notices[notice_id]
        affected_claims = set()
        for claim in self.claims.values():
            for statement in claim.statement_list():
                for clip in claim.clips_for(statement):
                    if notice_id in self.ledger.affected_notices(clip):
                        affected_claims.add(claim.id)
        pending_slots = []
        published = []
        for slot in self.slots.values():
            if affected_claims.intersection(slot.claim_ids):
                assessments = self.assess(slot.id)
                pending_slots.append({
                    'slot': slot.id, 'channel': slot.channel,
                    'approved': all(item['approved'] for item in assessments),
                    'blocked_statements': [
                        {'claim': item['claim'], 'statement': statement['statement'],
                         'issues': statement['issues']}
                        for item in assessments for statement in item['statements']
                        if not statement['approved']],
                })
        for release_id, packet in self._published.items():
            if affected_claims.intersection(packet['slot']['claim_ids']):
                published.append({'release_id': release_id,
                                  'slot': packet['slot']['id'],
                                  'channel': packet['slot']['channel']})
        return {
            'notice': notice_id,
            'status': self.ledger.status(notice_id),
            'claims': sorted(affected_claims),
            'pending_slots': pending_slots,
            'published': published,
            'conflicts': self.ledger.conflicts(),
        }

    def deliverable(self, slot_id):
        """能独立核实、不受该更正疑点影响的陈述可继续进入渠道。

        按陈述逐条判断，而不是因为某一条摘句被撤回就否掉整段转述：
        其余证据可能仍够当前栏目使用。
        """
        slot = self.slots[slot_id]
        ready = []
        held = []
        for claim_id in slot.claim_ids:
            claim = self.claims[claim_id]
            review = self.review.check(claim, slot.channel)
            by_statement = {item['statement']: item for item in review['statements']}
            for statement in claim.statement_list():
                result = by_statement[statement.id]
                entry = {'claim': claim_id, 'statement': statement.id,
                         'issues': result['issues'],
                         'withdrawn': result['withdrawn'],
                         'held': result['held'],
                         'support': result['support']}
                (ready if result['approved'] else held).append(entry)
        return {'slot': slot_id, 'channel': slot.channel,
                'ready': ready, 'held': held}

    # ---- 已发稿的更正工作 -------------------------------------------
    def correction_worklist(self):
        """已发稿中需要进入更正工作的条目，按栏目列出。

        同一原文不同片段的修订分别生效；针对同一片段冲突的通知会标记出来，
        不影响其他条目继续处理。
        """
        worklist = []
        for release_id, packet in self._published.items():
            for claim_id in packet['slot']['claim_ids']:
                claim = self.claims[claim_id]
                notices = []
                for statement in claim.statement_list():
                    for clip in claim.clips_for(statement):
                        for effect in self.ledger.clip_effects(clip):
                            notices.append({'statement': statement.id,
                                            'clip': [clip.wire_id, clip.edition,
                                                     clip.start, clip.end],
                                            'notice': effect['notice_id'],
                                            'status': effect['status'],
                                            'issue': effect.get('issue')})
                if notices:
                    worklist.append({'release_id': release_id,
                                     'slot': packet['slot']['id'],
                                     'channel': packet['slot']['channel'],
                                     'claim': claim_id,
                                     'notices': notices,
                                     'blocked': any(item['status'] == 'conflict'
                                                    for item in notices)})
        return worklist

    # ---- 合作方回执 -------------------------------------------------
    def send_to_partner(self, release_id, partner, sent_text=None):
        packet = self._published[release_id]
        if packet.get('partner') not in (None, partner):
            raise ValueError(f'刊发 {release_id} 已登记给其他合作方')
        entry = {'partner': partner, 'release_id': release_id,
                 'text': sent_text if sent_text is not None else [
                     claim['text'] for claim in packet['claims']],
                 'superseded_by': []}
        packet['partner'] = partner
        packet['partner_notices'].append(entry)
        return deepcopy(entry)

    def partner_notice(self, release_id, partner, notice_id):
        """把更正回执发给合作方；旧稿和回执都保留。"""
        packet = self._published[release_id]
        if packet.get('partner') != partner:
            raise ValueError(f'刊发 {release_id} 未登记给合作方 {partner}')
        if notice_id not in self.ledger.notices:
            raise ValueError(f'没有该更正通知：{notice_id}')
        resolution = self.ledger.resolution(notice_id)
        entry = {'notice': notice_id, 'partner': partner,
                 'status': resolution['status'],
                 'target': resolution['target'],
                 'amended': self.ledger.notices[notice_id].amended,
                 'acknowledged': False}
        packet['partner_notices'].append(entry)
        for sent in packet['partner_notices']:
            if 'release_id' in sent:
                sent['superseded_by'].append(notice_id)
        return deepcopy(entry)

    def partner_ack(self, release_id, notice_id, note=None):
        packet = self._published[release_id]
        for entry in packet['partner_notices']:
            if entry.get('notice') == notice_id:
                entry['acknowledged'] = True
                entry['ack_note'] = note
                packet['partner_acks'].append({'notice': notice_id, 'note': note})
                return deepcopy(entry)
        raise ValueError(f'合作方尚未收到该更正回执：{notice_id}')

    def partner_receipt(self, release_id):
        """合作站留存的完整回执：旧稿文字、更正回执和对方确认。"""
        packet = deepcopy(self._published[release_id])
        return {
            'release_id': release_id,
            'slot': packet['slot']['id'],
            'channel': packet['slot']['channel'],
            'partner': packet.get('partner'),
            'copy': [{'release_id': item['release_id'], 'text': item['text'],
                      'superseded_by': list(item['superseded_by'])}
                     for item in packet['partner_notices'] if 'release_id' in item],
            'corrections': [{'notice': item['notice'], 'status': item['status'],
                             'target': item['target'], 'amended': item['amended'],
                             'acknowledged': item['acknowledged'],
                             'ack_note': item.get('ack_note')}
                            for item in packet['partner_notices'] if 'notice' in item],
            'acks': packet['partner_acks'],
        }

    def partner_corrections(self, partner):
        """按合作方汇总所有已发出的旧稿和更正回执，便于留档核对。"""
        records = []
        for release_id, packet in self._published.items():
            if packet.get('partner') != partner:
                continue
            receipt = self.partner_receipt(release_id)
            records.append({'release_id': release_id,
                            'slot': receipt['slot'],
                            'copy': receipt['copy'],
                            'corrections': receipt['corrections']})
        return {'partner': partner, 'releases': records}
