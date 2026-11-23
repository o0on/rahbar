"""D2: rebuild the exact evidence and prompt for one trace id.

    python scripts/trace_tool.py <trace-id> [--path traces/trace.jsonl]

The trace stores the context passages and the prompt messages verbatim, so this
tool needs neither Chroma nor Ollama: it proves what the model actually saw.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.tracing import read_trace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Show the context and prompt behind a trace id")
    parser.add_argument("trace_id")
    parser.add_argument("--path", default=os.environ.get("TRACE_PATH", ""),
                        help="trace JSONL file (default: $TRACE_PATH)")
    args = parser.parse_args()
    trace = read_trace(args.trace_id, Path(args.path) if args.path else None)

    print(f"TRACE ID      {trace['trace_id']}")
    print(f"TIMESTAMP     {trace['timestamp']}")
    print(f"QUERY         {trace['query']}")
    print(f"COLLECTION    {trace['collection']}  "
          f"EMBEDDING {trace['embedding_model']}/{trace['embedding_version']}")
    print(f"FILTERS       {json.dumps(trace['filters'])}")
    print(f"PROMPT HASH   {trace['prompt_hash']}")
    print(f"CACHE         {'hit' if trace['cache_hit'] else 'miss'}")
    print("CONTEXT (exact passages retrieved):")
    for i, item in enumerate(trace["context"], start=1):
        print(f"  [{i}] chunk={item['chunk_id']} doc_id={item.get('doc_id')} "
              f"version={item.get('doc_version')} score={item.get('score')}")
        print(f"       source={item['source']} section={item['section']} page={item.get('page')}")
        for line in str(item["text"]).splitlines():
            print(f"       {line}")
    print("PROMPT (exact messages sent to the model):")
    for message in trace["prompt"]:
        print(f"  --- {message['role']} ---")
        for line in message["content"].splitlines():
            print(f"  {line}")
    print(f"GUARDRAIL     {json.dumps(trace['guardrail_actions'])}")
    print(f"RESPONSE      {json.dumps(trace['response'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
