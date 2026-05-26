#!/usr/bin/env python3
"""Host-side cron entry point for orphan sandbox GC.

Schedule via Temporal Schedule (every 15 min) or as a system cron:

    */15 * * * * /usr/bin/env python3 /opt/agent-temporal/scripts/gc_orphans.py

Reads TEMPORAL_TARGET from env. Returns 0 on success."""
from __future__ import annotations

import asyncio
import logging
import sys

from src.activities.cleanup_orphans import gc_orphan_sandboxes


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    n = await gc_orphan_sandboxes()
    logging.info("reaped %d orphan sandbox container(s)", n)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
