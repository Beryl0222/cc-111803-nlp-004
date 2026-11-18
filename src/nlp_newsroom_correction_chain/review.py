from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    clips: tuple
    named_quote: bool = False


class CopyReview:
    """根据记者选定的摘句和刊发栏目的采信约定核签。"""

    def __init__(self, archive, channel_min_desks):
        self.archive = archive
        self.channel_min_desks = channel_min_desks

    def check(self, claim, channel):
        min_desks = self.channel_min_desks[channel]
        desks = set()
        problems = []
        for clip in claim.clips:
            try:
                desk = self.archive.check_clip(clip)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            if claim.named_quote and (clip.kind != 'quote' or clip.text != claim.text):
                problems.append('具名引语需要逐字摘句')
                continue
            desks.add(desk)
        if len(desks) < min_desks:
            problems.append(f'独立来源不足，当前 {len(desks)} 家，栏目需要 {min_desks} 家')
        return {'claim': claim.id, 'desks': sorted(desks), 'issues': problems, 'approved': not problems}
