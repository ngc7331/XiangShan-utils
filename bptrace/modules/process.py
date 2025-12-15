"""SQL processor module for bptrace."""

import argparse
import csv
import sqlite3
import sys
from typing import Any, Literal

from .types import Record
from .utils import chunk_list
from .consts import (
    BASE_FIELDS,
    FIELD_BRTYPE,
    FIELD_RASACTION,
    FIELD_TARGET,
    TABLE_PRED,
    TABLE_TRAIN
)

def get_time_range_where_clause(start: int, end: int | None) -> tuple[str, list[int]]:
    """Generate SQL WHERE clause and parameters for time range filtering."""
    where_clause = "STAMP >= ?"
    params = [start]
    if end is not None:
        where_clause += " AND STAMP <= ?"
        params.append(end)
    return where_clause, params

def ensure_temp_table(cur: sqlite3.Cursor) -> None:
    """Ensure temporary table exists."""
    cur.execute("""
        CREATE TEMP TABLE IF NOT EXISTS temp_bpids (
            bpid INTEGER PRIMARY KEY
        )
    """)

def clear_temp_table(cur: sqlite3.Cursor) -> None:
    """Clear all entries in temporary table."""
    cur.execute("DELETE FROM temp_bpids")

def validate_meta_fields(cur: sqlite3.Cursor, fields: str | None) -> list[str]:
    """Validate that the requested metadata fields exists and convert to list."""
    if not fields:
        return []

    # Get table schema
    cur.execute("PRAGMA table_info(BpuPredictionTrace)")
    columns = sorted(
        row[1].replace("META_", "") for row in cur.fetchall() if row[1].startswith("META_")
    )

    fields = sorted(
        field.strip().upper() for field in fields.split(',')
    )

    for field in fields:
        if field not in columns:
            print(
                f"Error: Field {field} not found in BpuPredictionTrace table.\n"
                f"Available fields: \n{'\n'.join(columns)}",
                file=sys.stderr
            )
            sys.exit(1)

    return fields

def insert_bpids_to_temp_table(cur: sqlite3.Cursor, bpids: list[int]) -> None:
    """Insert list of bpids into temporary table."""
    if not bpids:
        return

    clear_temp_table(cur)
    # Batch insert to avoid too many parameters, using INSERT OR IGNORE to handle duplicates
    for chunk in chunk_list(bpids, 500):
        placeholders = ','.join(['(?)'] * len(chunk))
        cur.execute(f"INSERT OR IGNORE INTO temp_bpids VALUES {placeholders}", chunk)


def get_bpids_subquery(table_column: str) -> str:
    """Generate a subquery for bpid filtering using temporary table."""
    return f"AND {table_column} IN (SELECT bpid FROM temp_bpids)"

def fetch_bpids(
        cur: sqlite3.Cursor,
        condition: str,
        params: list[Any],
        table: Literal["BpuPredictionTrace", "BpuTrainTrace"],
        start: int,
        end: int | None
    ) -> set[int]:
    """Get all bpids matching a specific condition (SQL query)."""
    where_clause, where_params = get_time_range_where_clause(start, end)

    cur.execute(f"""
        SELECT DISTINCT {"PERFMETA_BPID" if table == TABLE_PRED else "TRAIN_PERFMETA_BPID"}
        FROM {table}
        WHERE {where_clause} AND ({condition})
    """, where_params + params)

    return set(row[0] for row in cur.fetchall())

def fetch_override_bpids(cur: sqlite3.Cursor, start: int, end: int | None) -> set[int]:
    """Get all bpids where s1 and s3 predictions differ."""
    return fetch_bpids(
        cur,
        """
            PERFMETA_S1PREDICTION_TAKEN != PERFMETA_S3PREDICTION_TAKEN OR
            PERFMETA_S1PREDICTION_CFIPOSITION != PERFMETA_S3PREDICTION_CFIPOSITION OR
            PERFMETA_S1PREDICTION_TARGET_ADDR != PERFMETA_S3PREDICTION_TARGET_ADDR OR
            PERFMETA_S1PREDICTION_ATTRIBUTE_BRANCHTYPE != PERFMETA_S3PREDICTION_ATTRIBUTE_BRANCHTYPE OR
            PERFMETA_S1PREDICTION_ATTRIBUTE_RASACTION != PERFMETA_S3PREDICTION_ATTRIBUTE_RASACTION
        """,
        [],
        TABLE_PRED,
        start,
        end
    )

def fetch_mispredict_bpids(cur: sqlite3.Cursor, start: int, end: int | None) -> list[int]:
    """Get all bpids that were mispredicted."""
    return fetch_bpids(
        cur,
        " OR ".join(
            f"(TRAIN_BRANCHES_{i}_VALID = 1 AND TRAIN_BRANCHES_{i}_BITS_MISPREDICT = 1)"
            for i in range(8)
        ),
        [],
        TABLE_TRAIN,
        start,
        end
    )

def fetch_addr_bpids(cur: sqlite3.Cursor, addr: int, start: int, end: int | None) -> set[int]:
    """Get all bpids with specified startPc."""
    return fetch_bpids(
        cur,
        "PERFMETA_STARTPC_ADDR = ?",
        [addr],
        TABLE_PRED,
        start,
        end
    )

def fetch_prediction_trace(
        cur: sqlite3.Cursor,
        start: int,
        end: int | None,
        num: int | None,
        bpid_list: list[int] | None = None,
        include_brtype: bool = False,
        include_rasaction: bool = False,
        include_target: bool = False,
        meta_fields: list[str] | None = None,
    ) -> list[Record]:
    """Get prediction records from BPU prediction trace."""
    where_clause, params = get_time_range_where_clause(start, end)

    # Use temporary table if bpid_list is provided
    if bpid_list:
        ensure_temp_table(cur)
        insert_bpids_to_temp_table(cur, bpid_list)
        bpid_subquery = get_bpids_subquery("PERFMETA_BPID")
    else:
        bpid_subquery = ""

    final_params = params + params  # Both UNION ALL queries need time range parameters
    if num is not None:
        final_params.append(num * 2)  # Double for UNION ALL results

    # Build select fields list - metadata fields at the end
    base_fields = [
        "STAMP",
        "PERFMETA_BPID",
        "PERFMETA_STARTPC_ADDR",
        "'p1' as type",
        "PERFMETA_S1PREDICTION_TAKEN as taken",
        "PERFMETA_S1PREDICTION_CFIPOSITION as position",
        "'-' as mispredict"
    ]

    # Add optional fields based on parameters
    if include_brtype:
        base_fields.append("PERFMETA_S1PREDICTION_ATTRIBUTE_BRANCHTYPE as brType")
    if include_rasaction:
        base_fields.append("PERFMETA_S1PREDICTION_ATTRIBUTE_RASACTION as rasAction")
    if include_target:
        base_fields.append("PERFMETA_S1PREDICTION_TARGET_ADDR as target")

    meta_field_names = [f"META_{f}" for f in (meta_fields or [])]

    select_fields = base_fields + meta_field_names
    select_str = ", ".join(select_fields)

    cur.execute(f"""
        SELECT {select_str}
        FROM BpuPredictionTrace WHERE {where_clause} {bpid_subquery}
        UNION ALL
        SELECT {select_str.replace("'p1'", "'p3'").replace("S1PREDICTION", "S3PREDICTION")}
        FROM BpuPredictionTrace WHERE {where_clause} {bpid_subquery}
        ORDER BY STAMP ASC, PERFMETA_BPID, type
        {' LIMIT ? ' if num is not None else ''}
    """, final_params)

    result = [
        Record.from_db(
            row,
            include_brtype=include_brtype,
            include_rasaction=include_rasaction,
            include_target=include_target,
            meta_fields=meta_fields
        ) for row in cur.fetchall()
    ]

    # Cleanup temporary table
    if bpid_list:
        clear_temp_table(cur)

    return result

def fetch_train_trace(
        cur: sqlite3.Cursor,
        start: int,
        end: int | None,
        num: int | None,
        bpid_list: list[int] | None = None,
        include_brtype: bool = False,
        include_rasaction: bool = False,
        include_target: bool = False,
        meta_fields: list[str] | None = None,
    ) -> list[Record]:
    """Get branch training records from BPU train trace."""
    where_clause, params = get_time_range_where_clause(start, end)
    result: list[Record] = []

    # Use temporary table if bpid_list is provided
    if bpid_list:
        ensure_temp_table(cur)
        insert_bpids_to_temp_table(cur, bpid_list)
        bpid_subquery = get_bpids_subquery("TRAIN_PERFMETA_BPID")
    else:
        bpid_subquery = ""

    # Build select fields list - metadata fields at the end
    base_fields = [
        "STAMP",
        "TRAIN_PERFMETA_BPID",
        "TRAIN_PERFMETA_STARTPC_ADDR"
    ]
    meta_field_names = [f"TRAIN_META_{f}" for f in (meta_fields or [])]

    # Process 8 branches separately to avoid too many parameters
    for i in range(8):
        branch_fields = [
            f"'t{i}' as type",
            f"TRAIN_BRANCHES_{i}_BITS_TAKEN as taken",
            f"TRAIN_BRANCHES_{i}_BITS_CFIPOSITION as position",
            f"TRAIN_BRANCHES_{i}_BITS_MISPREDICT as mispredict"
        ]

        # Add optional fields based on parameters
        if include_brtype:
            branch_fields.append(f"TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_BRANCHTYPE as brType")
        if include_rasaction:
            branch_fields.append(f"TRAIN_BRANCHES_{i}_BITS_ATTRIBUTE_RASACTION as rasAction")
        if include_target:
            branch_fields.append(f"TRAIN_BRANCHES_{i}_BITS_TARGET_ADDR as target")
        select_fields = base_fields + branch_fields + meta_field_names
        select_str = ", ".join(select_fields)

        # Do not sort here, we want sort from all branches together later
        cur.execute(f"""
            SELECT {select_str}
            FROM BpuTrainTrace
            WHERE {where_clause} {bpid_subquery} AND TRAIN_BRANCHES_{i}_VALID = 1
        """, params)

        result.extend(
            Record.from_db(
                row,
                include_brtype=include_brtype,
                include_rasaction=include_rasaction,
                include_target=include_target,
                meta_fields=meta_fields
            ) for row in cur.fetchall()
        )

    # Cleanup temporary table
    if bpid_list:
        clear_temp_table(cur)

    # Sort results and limit if needed
    result.sort(key=lambda x: (x.stamp, x.id, x.type))
    if num is not None:
        result = result[:num]

    return result



def export(args: argparse.Namespace, cur: sqlite3.Cursor) -> None:
    """Export processed trace data to CSV file."""
    # Validate meta fields if provided
    meta_fields = validate_meta_fields(cur, args.meta)

    # Select bpids based on filters
    bpid_lists = []
    if args.only_addr is not None:
        bpid_lists.append(fetch_addr_bpids(cur, args.only_addr, args.start, args.end))
    if args.only_mispredict:
        bpid_lists.append(fetch_mispredict_bpids(cur, args.start, args.end))
    if args.only_override:
        bpid_lists.append(fetch_override_bpids(cur, args.start, args.end))

    bpid_list = list(set.intersection(*bpid_lists)) if bpid_lists else None

    # Fetch prediction and training records
    pred = fetch_prediction_trace(
        cur, args.start, args.end, args.num,
        bpid_list=bpid_list,
        include_brtype=args.brtype,
        include_rasaction=args.rasaction,
        include_target=args.target,
        meta_fields=meta_fields
    )
    train = fetch_train_trace(
        cur, args.start, args.end, args.num,
        bpid_list=bpid_list,
        include_brtype=args.brtype,
        include_rasaction=args.rasaction,
        include_target=args.target,
        meta_fields=meta_fields
    )

    # Ensure data is found
    if not pred and not train:
        if bpid_list:
            print("No matching records found", file=sys.stderr)
            sys.exit(0)
        else:
            print("No records in database", file=sys.stderr)
            sys.exit(0)

    # Sort by timestamp and ID
    records = pred + train
    records.sort(key=lambda x: (x.stamp, x.id, x.type))

    # Prepare CSV fieldnames - metadata fields at the end
    fieldnames = BASE_FIELDS.copy()

    # Add optional fields based on command line arguments
    if args.brtype:
        fieldnames.append(FIELD_BRTYPE)
    if args.rasaction:
        fieldnames.append(FIELD_RASACTION)
    if args.target:
        fieldnames.append(FIELD_TARGET)
    if meta_fields:
        fieldnames.extend(meta_fields)

    # Output results
    with open(args.output, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(record.render(args.render_prunedaddr))

    print(f"Bp trace exported to {args.output}")
