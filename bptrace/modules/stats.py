"""Statistics generation module for bptrace."""

import argparse
import sqlite3

from .types import Record

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

def stat(args: argparse.Namespace, cur: sqlite3.Cursor) -> None:
    """Generate and print statistics."""
    print("===== Statistics =====")
    if args.stats_mispredict > 0:
        counts = count_block_mispredict(cur)
        sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:args.stats_mispredict]
        print(f"Top {args.stats_mispredict} block with most mispredictions:")
        print(f"{'Address':<14} {'Mispredictions':<15}")
        for addr, count in sorted_counts:
            addr_str = Record.render_prunedaddr(addr, args.render_prunedaddr)
            print(f"{addr_str:<14} {count:<15}")

    if args.stats_br_mispredict > 0:
        counts = count_branch_mispredict(cur)
        sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:args.stats_br_mispredict]
        print(f"\nTop {args.stats_br_mispredict} branch with most mispredictions:")
        print(f"{'Address':<14} {'Position':<10} {'Mispredictions':<15}")
        for (addr, position), count in sorted_counts:
            addr_str = Record.render_prunedaddr(addr, args.render_prunedaddr)
            print(f"{addr_str:<14} {position:<10} {count:<15}")
