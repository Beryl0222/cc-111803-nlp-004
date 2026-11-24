import argparse
import json
from pathlib import Path

from .repository import JsonRepository
from .workflow import TextWorkflow


def main(argv=None):
    parser = argparse.ArgumentParser(description='本地文本记录工作区')
    parser.add_argument('action', choices=['ingest', 'list', 'business', 'correct'])
    parser.add_argument('input', nargs='?')
    parser.add_argument('--source')
    parser.add_argument('--business-file', default='fixtures/business.json')
    parser.add_argument('--desk', default='fixtures/corrections.json')
    parser.add_argument('--state', default='var/desk-state.json')
    args = parser.parse_args(argv)
    workflow = TextWorkflow(JsonRepository('var/records.json'))
    if args.action == 'ingest':
        if not args.input:
            parser.error('ingest 需要输入 JSON 文件')
        values = json.loads(Path(args.input).read_text(encoding='utf-8'))
        result = workflow.ingest(values)
    elif args.action == 'business':
        result = workflow.business_summary(args.business_file)
    elif args.action == 'correct':
        from .desk_cli import apply_state, load_desk, read_state
        desk = apply_state(load_desk(args.desk), read_state(args.state, args.desk))
        result = {
            'pending': desk.pending_notices(),
            'conflicts': desk.conflicts(),
            'worklist': desk.correction_worklist(),
        }
    else:
        result = workflow.list_records(args.source)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
