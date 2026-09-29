"""One isolated process per analysis; parent enforces a hard wall-clock limit."""
import json
import sys
from pathlib import Path
from .agent_runner import run, write_json


def main():
    job=Path(sys.argv[1]).resolve()
    config=json.loads((job/'input.json').read_text(encoding='utf8'))
    def progress(state):write_json(job/'progress.json',state)
    try:
        result=run(config['project'],job.parent.parent,config['request'],config['limits'],progress)
        write_json(job/'result.json',result)
        write_json(job/'report.json',result['agent_report'])
    except Exception as exc:
        write_json(job/'error.json',{'error':f'{type(exc).__name__}: {exc}'})
        raise


if __name__=='__main__':main()
