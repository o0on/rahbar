"""One JSON line per event, to stdout, like the API's logs (section 4.8).

The indexer's filesystem is ephemeral and its stdout is what `kubectl logs`
shows, so every stage reports through here and nowhere else.
"""

from __future__ import annotations

import json
import time


def log(msg: str, **fields: object) -> None:
    print(
        json.dumps(
            {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "logger": "rahbar.indexer", "msg": msg, **fields},
            default=str,
        ),
        flush=True,
    )
