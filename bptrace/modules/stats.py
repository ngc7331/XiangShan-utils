"""Statistics generation module for bptrace."""

import argparse
import sqlite3

from .types import Record
from .consts import (
    FIELD_ADDR,
    FIELD_POSITION,
    FIELD_BRTYPE,
    FIELD_RASACTION,
    FIELD_TARGET
)

def count_block_mispredict(cur: sqlite3.Cursor) -> dict[int, int]:
    """Get top N startVAddr with most mispredictions."""
    counts = {}
    for i in range(8):
        cur.execute(f'''
            SELECT TRAIN_META_DEBUG_STARTVADDR_ADDR, COUNT(*)
            FROM BpuTrainTrace
            WHERE TRAIN_BRANCHES_{i}_VALID = 1 AND TRAIN_BRANCHES_{i}_BITS_MISPREDICT = 1
            GROUP BY TRAIN_META_DEBUG_STARTVADDR_ADDR
        ''')
        for addr, count in cur.fetchall():
            counts[addr] = counts.get(addr, 0) + count
    return counts

def count_branch_mispredict(cur: sqlite3.Cursor) -> dict[tuple[int, int], int]:
    """Get misprediction counts for each (startvaddr, position) pair."""
    counts = {}
    for i in range(8):
        cur.execute(f'''
            SELECT TRAIN_META_DEBUG_STARTVADDR_ADDR, TRAIN_BRANCHES_{i}_BITS_CFIPOSITION, COUNT(*)
            FROM BpuTrainTrace
            WHERE TRAIN_BRANCHES_{i}_VALID = 1 AND TRAIN_BRANCHES_{i}_BITS_MISPREDICT = 1
            GROUP BY TRAIN_META_DEBUG_STARTVADDR_ADDR, TRAIN_BRANCHES_{i}_BITS_CFIPOSITION
        ''')
        for addr, position, count in cur.fetchall():
            key = (addr, position)
            counts[key] = counts.get(key, 0) + count
    return counts

def fetch_record(cur: sqlite3.Cursor, addr: int, position: int) -> Record:
    """Fetch attributes of a specific branch identified by (addr, position)."""
    base_fields = [
        "STAMP",
        "TRAIN_META_DEBUG_BPID",
        "TRAIN_META_DEBUG_STARTVADDR_ADDR"
    ]
    for i in range(8):
        branch_fields = [
            f"'t{i}' as type",
            f"TRAIN_BRANCHES_{i}_BITS_TAKEN as taken",
            f"TRAIN_BRANCHES_{i}_BITS_CFIPOSITION as position",
            f"TRAIN_BRANCHES_{i}_BITS_MISPREDICT as mispredict",
            f"TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_BRANCHTYPE as brType",
            f"TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_RASACTION as rasAction",
            f"TRAIN_BRANCHES_{i}_BITS_TARGET_ADDR as target"
        ]

        select_fields = base_fields + branch_fields
        select_str = ", ".join(select_fields)

        cur.execute(f'''
            SELECT {select_str}
            FROM BpuTrainTrace
            WHERE TRAIN_BRANCHES_{i}_VALID = 1 AND
                  TRAIN_META_DEBUG_STARTVADDR_ADDR = ? AND
                  TRAIN_BRANCHES_{i}_BITS_CFIPOSITION = ?
            LIMIT 1
        ''', (addr, position))
        row = cur.fetchone()
        if row:
            return Record.from_db(row, include_brtype=True, include_rasaction=True, include_target=True)
    raise ValueError(f"Branch with addr {addr} and position {position} not found.")

def stat(args: argparse.Namespace, cur: sqlite3.Cursor) -> None:
    """Generate and print statistics."""
    print("===== Statistics =====")
    if args.stats_mispredict > 0:
        counts = count_block_mispredict(cur)
        sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:args.stats_mispredict]
        print(f"Top {args.stats_mispredict} block with most mispredictions:")
        print(f"{'Address':<14} {'Mispreds':<8}")
        for addr, count in sorted_counts:
            addr_str = Record.render_prunedaddr(addr, args.render_prunedaddr)
            print(f"{addr_str:<14} {count:<8}")

    if args.stats_br_mispredict > 0:
        counts = count_branch_mispredict(cur)
        sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:args.stats_br_mispredict]
        print(f"\nTop {args.stats_br_mispredict} branch with most mispredictions:")
        print(f"{'Address':<14} {'Position':<8} {'Mispreds':<8} {'brType':<12} {'rasAction':<12} {'target':<14}")
        for (addr, position), count in sorted_counts:
            record = fetch_record(cur, addr, position).render(args.render_prunedaddr)
            print(f"{record[FIELD_ADDR]:<14} {record[FIELD_POSITION]:<8} {count:<8} {record[FIELD_BRTYPE]:<12} {record[FIELD_RASACTION]:<12} {record[FIELD_TARGET]:<14}")

        print("Note: brType, rasAction and target may not be accurate if has instruction self-modification.")
