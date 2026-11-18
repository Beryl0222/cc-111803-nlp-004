import argparse
from dataclasses import replace
import json
from pathlib import Path

from .clips import Clip, Wire, WireArchive
from .editions import EditionDesk, Slot
from .review import Claim, CopyReview


def load_desk(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    archive = WireArchive(Wire(**value) for value in data['wires'])
    claims = []
    for value in data['claims']:
        claim = Claim(**value)
        claims.append(replace(claim, clips=tuple(Clip(**clip) for clip in claim.clips)))
    slots = [Slot(value['id'], value['channel'], tuple(value['claim_ids'])) for value in data['slots']]
    desk = EditionDesk(CopyReview(archive, data['channel_min_desks']), claims, slots)
    for release in data['publication_log']:
        desk.publish(release['slot_id'], release['release_id'])
    return desk


def main(argv=None):
    parser = argparse.ArgumentParser(description='编辑部摘句核签')
    parser.add_argument('--desk', default='fixtures/desk.json')
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('review').add_argument('slot_id')
    commands.add_parser('receipt').add_argument('release_id')
    args = parser.parse_args(argv)
    try:
        desk = load_desk(args.desk)
        if args.action == 'review':
            output = desk.assess(args.slot_id)
        else:
            output = desk.receipt(args.release_id)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
