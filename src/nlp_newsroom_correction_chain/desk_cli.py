import argparse
from dataclasses import replace
import json
from pathlib import Path

from .clips import Clip, Wire, WireArchive
from .corrections import CorrectionNotice
from .editions import EditionDesk, Slot
from .review import Claim, CopyReview, Statement


def _statement(value):
    return Statement(**value)


def desk_from_data(data):
    archive = WireArchive(Wire(**value) for value in data['wires'])
    claims = []
    for value in data['claims']:
        value = dict(value)
        statements = tuple(_statement(item) for item in value.pop('statements', []))
        claim = Claim(**value, statements=statements)
        claim = replace(claim, clips=tuple(Clip(**clip) for clip in claim.clips))
        claims.append(claim.validate())
    slots = [Slot(value['id'], value['channel'], tuple(value['claim_ids']))
             for value in data['slots']]
    desk = EditionDesk(CopyReview(archive, data['channel_min_desks']), claims, slots)
    for release in data.get('publication_log', []):
        desk.publish(release['slot_id'], release['release_id'])
    # 更正通知在刊发之后到达，因此已发回执保持当时采用的文字和依据。
    for notice in data.get('notices', []):
        if isinstance(notice, str):
            notice = {'id': notice}
        if notice['id'] in desk.ledger.notices:
            continue
        desk.ledger.add(CorrectionNotice(**notice))
    for item in data.get('assignments', []):
        desk.assign(item['notice'], item['wire_id'], item['edition'],
                    item['start'], item['end'])
    for sent in data.get('partner_log', []):
        desk.send_to_partner(sent['release_id'], sent['partner'],
                             sent.get('text'))
    for sent in data.get('partner_notices', []):
        desk.partner_notice(sent['release_id'], sent['partner'], sent['notice'])
    for ack in data.get('partner_acks', []):
        desk.partner_ack(ack['release_id'], ack['notice'], ack.get('note'))
    return desk


def load_desk(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return desk_from_data(data)


def apply_state(desk, state):
    """回放值班编辑已做的归属确认和处理决定。"""
    for item in state.get('assignments', []):
        desk.assign(item['notice'], item['wire_id'], item['edition'],
                    item['start'], item['end'])
    for item in state.get('dismissed', []):
        desk.dismiss(item['notice'], item.get('reason'))
    return desk


def read_state(state_path, desk_path):
    path = Path(state_path)
    if not path.exists():
        return {'desk': str(desk_path), 'assignments': [], 'dismissed': []}
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('desk') not in (None, str(desk_path)):
        raise ValueError('状态文件属于另一份编辑桌资料')
    state.setdefault('assignments', [])
    state.setdefault('dismissed', [])
    return state


def write_state(state_path, state):
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='编辑部摘句核签')
    parser.add_argument('--desk', default='fixtures/desk.json')
    parser.add_argument('--state', default='var/desk-state.json')
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('review').add_argument('slot_id')
    commands.add_parser('receipt').add_argument('release_id')
    commands.add_parser('resolve').add_argument('notice_id')
    confirm = commands.add_parser('confirm')
    confirm.add_argument('notice_id')
    confirm.add_argument('wire_id')
    confirm.add_argument('edition', type=int)
    confirm.add_argument('start', type=int)
    confirm.add_argument('end', type=int)
    dismiss = commands.add_parser('dismiss')
    dismiss.add_argument('notice_id')
    dismiss.add_argument('--reason')
    commands.add_parser('pending')
    commands.add_parser('conflicts')
    commands.add_parser('impact').add_argument('notice_id')
    commands.add_parser('deliverable').add_argument('slot_id')
    commands.add_parser('worklist')
    commands.add_parser('partner-receipt').add_argument('release_id')
    commands.add_parser('partner-log').add_argument('partner')
    args = parser.parse_args(argv)
    try:
        desk = load_desk(args.desk)
        state = read_state(args.state, args.desk)
        apply_state(desk, state)
        if args.action == 'review':
            output = desk.assess(args.slot_id)
        elif args.action == 'receipt':
            output = desk.receipt(args.release_id)
        elif args.action == 'resolve':
            output = desk.resolution(args.notice_id)
        elif args.action == 'confirm':
            target = desk.assign(args.notice_id, args.wire_id, args.edition,
                                 args.start, args.end)
            state['assignments'].append({
                'notice': target.notice_id, 'wire_id': target.wire_id,
                'edition': target.edition, 'start': target.start,
                'end': target.end})
            write_state(args.state, state)
            output = {'notice': target.notice_id, 'wire_id': target.wire_id,
                      'edition': target.edition, 'start': target.start,
                      'end': target.end, 'text': target.text, 'confirmed': True}
        elif args.action == 'pending':
            output = desk.pending_notices()
        elif args.action == 'dismiss':
            output = desk.dismiss(args.notice_id, args.reason)
            state['dismissed'].append({'notice': args.notice_id,
                                       'reason': args.reason})
            write_state(args.state, state)
        elif args.action == 'conflicts':
            output = desk.conflicts()
        elif args.action == 'impact':
            output = desk.impact(args.notice_id)
        elif args.action == 'deliverable':
            output = desk.deliverable(args.slot_id)
        elif args.action == 'worklist':
            output = desk.correction_worklist()
        elif args.action == 'partner-log':
            output = desk.partner_corrections(args.partner)
        else:
            output = desk.partner_receipt(args.release_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
