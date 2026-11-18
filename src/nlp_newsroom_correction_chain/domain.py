import json
from pathlib import Path

def load_facts(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def summarize(facts):
    return [wire['ref'] for wire in facts['wires'] if facts['channel'] in wire['channels']]
