import argparse
import sqlite3
import csv
import sys
from typing import Generator, Dict, Any


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the trace analysis tool."""
    parser = argparse.ArgumentParser(description="XiangShan branch prediction trace analysis tool")

    parser.add_argument(
        "dbfile",
        help="Input database file path"
    )

    parser.add_argument(
        "-o", "--output",
        default="./trace.csv",
        help="Output CSV file path"
    )

    parser.add_argument(
        "-s", "--start",
        type=int,
        default=0,
        help="Start processing from this clock cycle (minimum STAMP)"
    )

    parser.add_argument(
        "-e", "--end",
        type=int,
        help="Stop processing at this clock cycle (maximum STAMP)"
    )

    parser.add_argument(
        "-n", "--num",
        type=int,
        help="Number of branch entries to process (cannot be used with -e)"
    )

    parser.add_argument(
        "--only-mispredict",
        action="store_true",
        help="Only output mispredicted entries"
    )

    parser.add_argument(
        "--only-override",
        action="store_true",
        help="Only output entries with s1/s3 prediction override"
    )

    parser.add_argument(
        "--brtype",
        action="store_true",
        help="Include brType field in the output"
    )

    parser.add_argument(
        "--rasaction",
        action="store_true",
        help="Include rasAction field in the output"
    )

    parser.add_argument(
        "--target",
        action="store_true",
        help="Include target field in the output"
    )

    parser.add_argument(
        "-m", "--meta",
        help="Comma-separated list of metadata fields to include in output (e.g., XXX for META_XXX field)"
    )

    parser.add_argument(
        "--render-prunedaddr",
        action="store_true",
        help="Render real address of PrunedAddr"
    )

    args = parser.parse_args()

    if args.end is not None and args.num is not None:
        print("Cannot use -e and -n at the same time", file=sys.stderr)
        sys.exit(1)

    return args

def get_time_range_where_clause(start: int, end: int | None) -> tuple[str, list[int]]:
    """Generate SQL WHERE clause and parameters for time range filtering."""
    where_clause = "STAMP >= ?"
    params = [start]
    if end is not None:
        where_clause += " AND STAMP <= ?"
        params.append(end)
    return where_clause, params

def chunk_list(lst: list, n: int = 200) -> Generator[list, None, None]:
    """Split a list into fixed-size chunks."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

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
    """Validate that the requested metadata fields exist in the prediction table"""
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

def fetch_override_bpids(cur: sqlite3.Cursor, start: int, end: int | None) -> list[int]:
    """Get all bpids where s1 and s3 predictions differ."""
    where_clause, params = get_time_range_where_clause(start, end)

    # Create temporary table for override cases
    cur.execute("""
        CREATE TEMP TABLE IF NOT EXISTS temp_override_bpids AS
        SELECT DISTINCT META_DEBUG_BPID
        FROM BpuPredictionTrace
        WHERE
            S1PREDICTION_TAKEN != S3PREDICTION_TAKEN OR
            S1PREDICTION_CFIPOSITION != S3PREDICTION_CFIPOSITION OR
            S1PREDICTION_TARGET_ADDR != S3PREDICTION_TARGET_ADDR OR
            S1PREDICTION_ATTRIBUTE_BRANCHTYPE != S3PREDICTION_ATTRIBUTE_BRANCHTYPE OR
            S1PREDICTION_ATTRIBUTE_RASACTION != S3PREDICTION_ATTRIBUTE_RASACTION
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_override_bpid
            ON temp_override_bpids(META_DEBUG_BPID)
    """)

    # Apply time range filter and get results
    cur.execute(f"""
        SELECT t.META_DEBUG_BPID
        FROM temp_override_bpids t
        JOIN BpuPredictionTrace p ON t.META_DEBUG_BPID = p.META_DEBUG_BPID
        WHERE {where_clause}
    """, params)
    result = [row[0] for row in cur.fetchall()]

    # Cleanup temporary table
    cur.execute("DROP TABLE temp_override_bpids")
    return result


def fetch_mispredict_bpids(cur: sqlite3.Cursor, start: int, end: int | None) -> list[int]:
    """Get all bpids that were mispredicted."""
    where_clause, params = get_time_range_where_clause(start, end)

    # Create temporary table for mispredictions
    conditions = []
    for i in range(8):
        conditions.append(
            f"(TRAIN_BRANCHES_{i}_VALID = 1 AND TRAIN_BRANCHES_{i}_BITS_MISPREDICT = 1)"
        )

    cur.execute(f"""
        CREATE TEMP TABLE IF NOT EXISTS temp_mispredict_bpids AS
        SELECT DISTINCT TRAIN_META_DEBUG_BPID
        FROM BpuTrainTrace
        WHERE {" OR ".join(conditions)}
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_mispredict_bpid
            ON temp_mispredict_bpids(TRAIN_META_DEBUG_BPID)
    """)

    # Apply time range filter and get results
    cur.execute(f"""
        SELECT t.TRAIN_META_DEBUG_BPID
        FROM temp_mispredict_bpids t
        JOIN BpuTrainTrace tr ON t.TRAIN_META_DEBUG_BPID = tr.TRAIN_META_DEBUG_BPID
        WHERE {where_clause}
    """, params)
    result = [row[0] for row in cur.fetchall()]

    # Cleanup temporary table
    cur.execute("DROP TABLE temp_mispredict_bpids")
    return result

def get_bpids_subquery(table_column: str) -> str:
    """Generate a subquery for bpid filtering using temporary table."""
    return f"AND {table_column} IN (SELECT bpid FROM temp_bpids)"

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
    ) -> list[Dict[str, Any]]:
    """Get prediction records from BPU prediction trace."""
    where_clause, params = get_time_range_where_clause(start, end)

    # Use temporary table if bpid_list is provided
    if bpid_list:
        ensure_temp_table(cur)
        insert_bpids_to_temp_table(cur, bpid_list)
        bpid_subquery = get_bpids_subquery("META_DEBUG_BPID")
    else:
        bpid_subquery = ""

    final_params = params + params  # Both UNION ALL queries need time range parameters
    if num is not None:
        final_params.append(num * 2)  # Double for UNION ALL results

    # Build select fields list - metadata fields at the end
    base_fields = [
        "STAMP",
        "META_DEBUG_BPID",
        "META_DEBUG_STARTVADDR_ADDR",
        "'p1' as type",
        "S1PREDICTION_TAKEN as taken",
        "S1PREDICTION_CFIPOSITION as position",
        "'-' as mispredict"
    ]

    # Add optional fields based on parameters
    if include_brtype:
        base_fields.append("S1PREDICTION_ATTRIBUTE_BRANCHTYPE as brType")
    if include_rasaction:
        base_fields.append("S1PREDICTION_ATTRIBUTE_RASACTION as rasAction")
    if include_target:
        base_fields.append("S1PREDICTION_TARGET_ADDR as target")

    meta_field_names = [f"META_{f}" for f in (meta_fields or [])]

    select_fields = base_fields + meta_field_names
    select_str = ", ".join(select_fields)

    cur.execute(f"""
        SELECT {select_str}
        FROM BpuPredictionTrace WHERE {where_clause} {bpid_subquery}
        UNION ALL
        SELECT {select_str.replace("'p1'", "'p3'").replace("S1PREDICTION", "S3PREDICTION")}
        FROM BpuPredictionTrace WHERE {where_clause} {bpid_subquery}
        ORDER BY STAMP ASC, META_DEBUG_BPID, type
        {' LIMIT ? ' if num is not None else ''}
    """, final_params)

    result = []
    for row in cur.fetchall():
        # Build row dict with standard fields
        row_dict = {
            'stamp': row[0],
            'id': row[1],
            'addr': row[2],
            'type': row[3],
            'taken': row[4],
            'position': row[5],
            'mispredict': row[6]
        }

        # Add optional fields if they were included in the query
        field_index = len(row_dict)
        if include_brtype:
            row_dict['brType'] = row[field_index]
            field_index += 1
        if include_rasaction:
            row_dict['rasAction'] = row[field_index]
            field_index += 1
        if include_target:
            row_dict['target'] = row[field_index]
            field_index += 1

        # Add metadata fields from the end of the row
        for i, field in enumerate(meta_fields or []):
            row_dict[field] = row[field_index + i]

        result.append(row_dict)

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
    ) -> list[Dict[str, Any]]:
    """Get branch training records from BPU train trace."""
    where_clause, params = get_time_range_where_clause(start, end)
    result = []

    # Use temporary table if bpid_list is provided
    if bpid_list:
        ensure_temp_table(cur)
        insert_bpids_to_temp_table(cur, bpid_list)
        bpid_subquery = get_bpids_subquery("TRAIN_META_DEBUG_BPID")
    else:
        bpid_subquery = ""

    # Build select fields list - metadata fields at the end
    base_fields = [
        "STAMP",
        "TRAIN_META_DEBUG_BPID",
        "TRAIN_META_DEBUG_STARTVADDR_ADDR"
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

        cur.execute(f"""
            SELECT {select_str}
            FROM BpuTrainTrace
            WHERE {where_clause} {bpid_subquery} AND TRAIN_BRANCHES_{i}_VALID = 1
        """, params)
        chunk_rows = cur.fetchall()

        for row in chunk_rows:
            # Build row dict with standard fields
            row_dict = {
                'stamp': row[0],
                'id': row[1],
                'addr': row[2],
                'type': row[3],
                'taken': row[4],
                'position': row[5],
                'mispredict': row[6]
            }

            # Add optional fields if they were included in the query
            field_index = len(row_dict)
            if include_brtype:
                row_dict['brType'] = row[field_index]
                field_index += 1
            if include_rasaction:
                row_dict['rasAction'] = row[field_index]
                field_index += 1
            if include_target:
                row_dict['target'] = row[field_index]
                field_index += 1

            # Add metadata fields from the end of the row
            for j, field in enumerate(meta_fields or []):
                row_dict[field] = row[field_index + j]

            result.append(row_dict)

    # Cleanup temporary table
    if bpid_list:
        clear_temp_table(cur)

    # Sort results and limit if needed
    result.sort(key=lambda x: (x['stamp'], x['id']))
    if num is not None:
        result = result[:num]

    return result

def render_prunedaddr(addr: int, use_pruned: bool) -> str:
    """Convert pruned address to hex string"""
    return hex(addr << 1) if use_pruned else hex(addr)

def render_brtype(brtype: int) -> str:
    """Convert branch type integer to string representation."""
    return [
        'None',
        'Conditional',
        'Direct',
        'Indirect'
    ][brtype]

def render_rasaction(rasaction: int) -> str:
    """Convert RAS action integer to string representation."""
    return [
        'None',
        'Pop',
        'Push',
        'PopAndPush'
    ][rasaction]

def main() -> None:
    """Main entry point for the branch prediction trace analysis tool."""
    args = parse_args()

    conn = sqlite3.connect(args.dbfile)
    cur = conn.cursor()

    # Validate meta fields if provided
    meta_fields = validate_meta_fields(cur, args.meta)

    # Select bpids based on filters
    bpid_list = None
    if args.only_mispredict or args.only_override:
        filter_lists = []

        if args.only_mispredict:
            mispredict_list = fetch_mispredict_bpids(cur, args.start, args.end)
            if not mispredict_list:
                print("No misprediction records found", file=sys.stderr)
                sys.exit(0)
            filter_lists.append(set(mispredict_list))

        if args.only_override:
            override_list = fetch_override_bpids(cur, args.start, args.end)
            if not override_list:
                print("No override records found", file=sys.stderr)
                sys.exit(0)
            filter_lists.append(set(override_list))

        # Take intersection of all filter conditions
        bpid_list = list(set.intersection(*filter_lists))
        print(f"Filtered records: {len(bpid_list)}")
        if not bpid_list:
            sys.exit(0)

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
    all_rows = pred + train
    all_rows.sort(key=lambda x: (x['stamp'], x['id'], x['type']))

    # Prepare CSV fieldnames - metadata fields at the end
    fieldnames = [
        'stamp',
        'id',
        'addr',
        'type',
        'taken',
        'position'
    ]

    # Add mispredict field before the optional fields
    fieldnames.append('mispredict')

    # Add optional fields based on command line arguments
    if args.brtype:
        fieldnames.append('brType')
    if args.rasaction:
        fieldnames.append('rasAction')
    if args.target:
        fieldnames.append('target')

    if meta_fields:
        fieldnames.extend(meta_fields)

    # Output results
    with open(args.output, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_rows:
            out_row = row.copy()
            out_row['addr'] = render_prunedaddr(row['addr'], args.render_prunedaddr)
            if (args.brtype):
                out_row['brType'] = render_brtype(row['brType'])
            if (args.rasaction):
                out_row['rasAction'] = render_rasaction(row['rasAction'])
            if (args.target):
                out_row['target'] = render_prunedaddr(row['target'], args.render_prunedaddr)
            writer.writerow(out_row)

    print(f"Export completed: {args.output}")

if __name__ == "__main__":
    main()
