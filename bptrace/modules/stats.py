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
            SELECT TRAIN_PERFMETA_STARTVADDR_ADDR, COUNT(*)
            FROM BpuTrainTrace
            WHERE TRAIN_BRANCHES_{i}_VALID = 1 AND TRAIN_BRANCHES_{i}_BITS_MISPREDICT = 1
            GROUP BY TRAIN_PERFMETA_STARTVADDR_ADDR
        ''')
        for addr, count in cur.fetchall():
            counts[addr] = counts.get(addr, 0) + count
    return counts

def count_branch_mispredict(cur: sqlite3.Cursor) -> dict[tuple[int, int], int]:
    """Get misprediction counts for each (startvaddr, position) pair."""
    counts = {}
    for i in range(8):
        cur.execute(f'''
            SELECT TRAIN_PERFMETA_STARTVADDR_ADDR, TRAIN_BRANCHES_{i}_BITS_CFIPOSITION, COUNT(*)
            FROM BpuTrainTrace
            WHERE TRAIN_BRANCHES_{i}_VALID = 1 AND TRAIN_BRANCHES_{i}_BITS_MISPREDICT = 1
            GROUP BY TRAIN_PERFMETA_STARTVADDR_ADDR, TRAIN_BRANCHES_{i}_BITS_CFIPOSITION
        ''')
        for addr, position, count in cur.fetchall():
            key = (addr, position)
            counts[key] = counts.get(key, 0) + count
    return counts

def count_type(cur: sqlite3.Cursor) -> dict[tuple[int, int], int]:
    """Get branch counts for each (brType, rasAction) pair."""
    counts = {}
    for i in range(8):
        cur.execute(f'''
            SELECT TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_BRANCHTYPE, TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_RASACTION, COUNT(*)
            FROM BpuTrainTrace
            WHERE TRAIN_BRANCHES_{i}_VALID = 1
            GROUP BY TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_BRANCHTYPE, TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_RASACTION
        ''')
        for brtype, rasaction, count in cur.fetchall():
            key = (brtype, rasaction)
            counts[key] = counts.get(key, 0) + count
    return counts

def count_type_mispredict(cur: sqlite3.Cursor) -> dict[tuple[int, int], int]:
    """Get misprediction counts for each (brType, rasAction) pair."""
    counts = {}
    for i in range(8):
        cur.execute(f'''
            SELECT TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_BRANCHTYPE, TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_RASACTION, COUNT(*)
            FROM BpuTrainTrace
            WHERE TRAIN_BRANCHES_{i}_VALID = 1 AND TRAIN_BRANCHES_{i}_BITS_MISPREDICT = 1
            GROUP BY TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_BRANCHTYPE, TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_RASACTION
        ''')
        for brtype, rasaction, count in cur.fetchall():
            key = (brtype, rasaction)
            counts[key] = counts.get(key, 0) + count
    return counts

def fetch_record(cur: sqlite3.Cursor, addr: int, position: int) -> Record:
    """Fetch attributes of a specific branch identified by (addr, position)."""
    base_fields = [
        "STAMP",
        "TRAIN_PERFMETA_BPID",
        "TRAIN_PERFMETA_STARTVADDR_ADDR"
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
                  TRAIN_PERFMETA_STARTVADDR_ADDR = ? AND
                  TRAIN_BRANCHES_{i}_BITS_CFIPOSITION = ?
            LIMIT 1
        ''', (addr, position))
        row = cur.fetchone()
        if row:
            return Record.from_db(row, include_brtype=True, include_rasaction=True, include_target=True)
    raise ValueError(f"Branch with addr {addr} and position {position} not found.")

def count_override(cur: sqlite3.Cursor) -> dict[str, int]:
    overrides = {}

    cur.execute(f'''
        SELECT COUNT(*)
        FROM BpuPredictionTrace
        WHERE
            PERFMETA_S1PREDICTION_TAKEN != PERFMETA_S3PREDICTION_TAKEN
    ''')
    overrides["taken"] = cur.fetchone()[0]

    cur.execute(f'''
        SELECT COUNT(*)
        FROM BpuPredictionTrace
        WHERE
            PERFMETA_S1PREDICTION_TAKEN == PERFMETA_S3PREDICTION_TAKEN AND
            PERFMETA_S1PREDICTION_CFIPOSITION != PERFMETA_S3PREDICTION_CFIPOSITION
    ''')
    overrides["position"] = cur.fetchone()[0]

    cur.execute(f'''
        SELECT COUNT(*)
        FROM BpuPredictionTrace
        WHERE
            PERFMETA_S1PREDICTION_TAKEN == PERFMETA_S3PREDICTION_TAKEN AND
            PERFMETA_S1PREDICTION_CFIPOSITION == PERFMETA_S3PREDICTION_CFIPOSITION AND (
                PERFMETA_S1PREDICTION_ATTRIBUTE_BRANCHTYPE != PERFMETA_S3PREDICTION_ATTRIBUTE_BRANCHTYPE OR
                PERFMETA_S1PREDICTION_ATTRIBUTE_RASACTION != PERFMETA_S3PREDICTION_ATTRIBUTE_RASACTION
            )
    ''')
    overrides["attribute"] = cur.fetchone()[0]

    cur.execute(f'''
        SELECT COUNT(*)
        FROM BpuPredictionTrace
        WHERE
            PERFMETA_S1PREDICTION_TAKEN == PERFMETA_S3PREDICTION_TAKEN AND
            PERFMETA_S1PREDICTION_CFIPOSITION == PERFMETA_S3PREDICTION_CFIPOSITION AND
            PERFMETA_S1PREDICTION_ATTRIBUTE_BRANCHTYPE == PERFMETA_S3PREDICTION_ATTRIBUTE_BRANCHTYPE AND
            PERFMETA_S1PREDICTION_ATTRIBUTE_RASACTION == PERFMETA_S3PREDICTION_ATTRIBUTE_RASACTION AND
            PERFMETA_S1PREDICTION_TARGET_ADDR != PERFMETA_S3PREDICTION_TARGET_ADDR
    ''')
    overrides["target"] = cur.fetchone()[0]

    overrides["total"] = sum(overrides.values())

    return overrides

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
        print("Note: position may not be accurate for Indirect branches")

    if args.stats_type:
        counts = count_type(cur)
        sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        print(f"\nBranch counts by attribute:")
        print(f"{'brType':<12} {'rasAction':<12} {'Count':<8}")
        for (brtype, rasaction), count in sorted_counts:
            brtype_str = Record.render_brtype(brtype)
            rasaction_str = Record.render_rasaction(rasaction)
            print(f"{brtype_str:<12} {rasaction_str:<12} {count:<8}")
        print(f"{'total':<12} {'':<12} {sum(counts.values()):<8}")

    if args.stats_type_mispredict:
        counts = count_type_mispredict(cur)
        sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        print(f"\nMisprediction counts by attribute:")
        print(f"{'brType':<12} {'rasAction':<12} {'Mispreds':<8}")
        for (brtype, rasaction), count in sorted_counts:
            brtype_str = Record.render_brtype(brtype)
            rasaction_str = Record.render_rasaction(rasaction)
            print(f"{brtype_str:<12} {rasaction_str:<12} {count:<8}")
        print(f"{'total':<12} {'':<12} {sum(counts.values()):<8}")

    if args.stats_override:
        overrides = count_override(cur)
        print(f"\nOverride counts by reason:")
        print(f"{'Reason':<12} {'Count':<8}")
        for reason, count in overrides.items():
            print(f"{reason:<12} {count:<8}")
