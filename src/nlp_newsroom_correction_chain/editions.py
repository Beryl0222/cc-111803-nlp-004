from copy import deepcopy
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Slot:
    id: str
    channel: str
    claim_ids: tuple[str, ...]


class EditionDesk:
    """用当前核签结果批准新稿并保存各次刊发的文字和依据。"""

    def __init__(self, review, claims, slots):
        self.review = review
        self.claims = {claim.id: claim for claim in claims}
        self.slots = {slot.id: slot for slot in slots}
        self._published = {}

    def assess(self, slot_id):
        slot = self.slots[slot_id]
        return [self.review.check(self.claims[claim_id], slot.channel) for claim_id in slot.claim_ids]

    def publish(self, slot_id, release_id):
        if release_id in self._published:
            raise ValueError(f'刊发编号已使用：{release_id}')
        reviews = self.assess(slot_id)
        if any(not review['approved'] for review in reviews):
            raise ValueError(f'稿件 {slot_id} 尚未全部核签')
        slot = self.slots[slot_id]
        packet = {'release_id': release_id, 'slot': asdict(slot), 'reviews': reviews,
                  'claims': [asdict(self.claims[claim_id]) for claim_id in slot.claim_ids]}
        self._published[release_id] = packet

    def receipt(self, release_id):
        return deepcopy(self._published[release_id])
