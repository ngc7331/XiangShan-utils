import argparse
import re

from modules.parser import VerilogParser

def expand_gen_signals(expr: str, parser: VerilogParser, keep_ids: set[int]) -> str:
    """Recursively expand _GEN signals in the expression, except those in keep_ids."""
    def __expand(e: str) -> str:
        def __repl(match: re.Match[str]) -> str:
            gen_id = int(match.group(1))
            if gen_id in keep_ids:
                return match.group(0)

            gen_expr = parser.get_assignment(match.group(0))
            return f"({__expand(gen_expr)})"

        return re.sub(r"_GEN(?:_(\d+))?", __repl, e)

    return __expand(expr)

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "verilog_file",
        help="Input verilog file path"
    )

    parser.add_argument(
        "-s", "--signal",
        help="Signal name to expand (only wire)"
    )

    parser.add_argument(
        "-l", "--line",
        type=int,
        help="Line number where the signal is assigned (wire or reg)"
    )

    parser.add_argument(
        "-k", "--keep",
        help="Comma-separated list of _GEN ids to keep (not expand)"
    )

    args = parser.parse_args()

    if not args.signal and not args.line:
        parser.error("Either --signal or --line must be specified.")

    with open(args.verilog_file, 'r') as f:
        verilog = f.read()

    parser = VerilogParser(verilog)

    keep_ids = set()
    if args.keep:
        keep_ids = set(int(k) for k in args.keep.split(','))

    if args.line:
        signal_name, assignment = parser.get_assignment_by_line(args.line)
    else:
        signal_name = args.signal
        assignment = parser.get_assignment(signal_name)

    # Expand _GEN signals
    expanded = expand_gen_signals(assignment, parser, keep_ids)

    print(f"Original expression for signal {signal_name}:")
    print(assignment)
    print()

    print(f"Expanded expression for signal {signal_name}:")
    print(expanded)
    print()

    print("All expanded _GEN signals:")
    for name, expr in parser.assignments.items():
        if name.startswith("_GEN"):
            print(f"{name} = {expr}")

    if args.keep:
        print()
        print(f"All kept _GEN signals:")
        for k in keep_ids:
            gen_name = f"_GEN_{k}"
            print(f"{gen_name} = {parser.get_assignment(gen_name)}")

if __name__ == "__main__":
    main()
