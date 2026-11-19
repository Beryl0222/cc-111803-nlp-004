import argparse
import json
from pathlib import Path

from .clips import Clip, Wire, WireArchive
from .corrections import CorrectionNotice
from .editions import EditionDesk, Slot
from .editorial import CorrectionDesk
from .review import Claim, CopyReview, Fact


def _clip(value):
    return Clip.from_dict(value)


def _fact(value):
    return Fact.from_dict(value, [_clip(clip) for clip in value.get('clips', ())])


def _claim(value):
    facts = tuple(_fact(fact) for fact in value.get('facts', ()))
    return Claim(
        id=value['id'], text=value['text'],
        clips=tuple(_clip(clip) for clip in value.get('clips', ())),
        named_quote=value.get('named_quote', False), facts=facts,
        speaker=value.get('speaker'),
    )


def _notice(value):
    return CorrectionNotice.from_dict(value)


def load_desk(path):
    """加载编辑桌资料，返回可核签、刊发并处理更正的工作台。

    资料中可选 ``corrections`` 登记更正文，``correction_columns`` 说明各
    栏目对应的更正版面；没有更正时行为与原来的核签工作台一致。
    """
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    archive = WireArchive(Wire.from_dict(value) for value in data['wires'])
    claims = [_claim(value) for value in data['claims']]
    slots = [Slot(value['id'], value['channel'], tuple(value['claim_ids']))
             for value in data['slots']]
    review = CopyReview(archive, data['channel_min_desks'])
    desk = EditionDesk(review, claims, slots)
    for release in data.get('publication_log', ()):
        desk.publish(release['slot_id'], release['release_id'])
    working = CorrectionDesk(archive, desk.claims, desk, data.get('correction_columns', {}))
    for value in data.get('corrections', ()):
        working.register(_notice(value))
    return working


def main(argv=None):
    parser = argparse.ArgumentParser(description='编辑部摘句核签与更正工作台')
    parser.add_argument('--desk', default='fixtures/desk.json')
    commands = parser.add_subparsers(dest='action', required=True)
    review = commands.add_parser('review')
    review.add_argument('slot_id')
    receipt = commands.add_parser('receipt')
    receipt.add_argument('release_id')
    notice = commands.add_parser('notice')
    notice.add_argument('notice_id')
    preview = commands.add_parser('preview')
    preview.add_argument('notice_id')
    attribute = commands.add_parser('attribute')
    attribute.add_argument('notice_id')
    attribute.add_argument('candidate_id')
    apply_parser = commands.add_parser('apply')
    apply_parser.add_argument('notice_id')
    apply_parser.add_argument('--replacement')
    apply_parser.add_argument('--claim')
    apply_parser.add_argument('--fact')
    coordinates = commands.add_parser('coordinates')
    coordinates.add_argument('claim_id')
    commands.add_parser('queue')
    commands.add_parser('unresolved')
    commands.add_parser('pending')
    args = parser.parse_args(argv)
    try:
        desk = load_desk(args.desk)
        if args.action == 'review':
            output = desk.review_slot(args.slot_id)
        elif args.action == 'receipt':
            output = desk.receipt(args.release_id)
        elif args.action == 'notice':
            output = desk.assess_notice(args.notice_id)
        elif args.action == 'preview':
            output = desk.preview(args.notice_id)
        elif args.action == 'attribute':
            output = desk.attribute(args.notice_id, args.candidate_id)
        elif args.action == 'apply':
            output = desk.apply(args.notice_id, replacement=args.replacement,
                                claim_id=args.claim, fact_id=args.fact)
        elif args.action == 'coordinates':
            output = desk.coordinates(args.claim_id)
        elif args.action == 'unresolved':
            output = desk.unresolved()
        elif args.action == 'pending':
            output = desk.pending_slots()
        else:
            output = {'corrections': desk.corrections_queue(),
                      'awaiting_decision': desk.awaiting_decision(),
                      'held': desk.held_facts()}
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
