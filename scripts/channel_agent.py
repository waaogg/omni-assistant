#!/usr/bin/env python3
"""JSON-line bridge used by the Node WeChat adapter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.channel_agent import ChannelAgent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    args = parser.parse_args()
    request = json.loads(sys.stdin.read() or "{}")
    response = ChannelAgent().run(args.channel, str(request.get("user", "")), str(request.get("text", "")))
    print(json.dumps({"response": response}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
