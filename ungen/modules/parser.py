import re

class VerilogParser:
    """A simple verilog parser to extract signal assignments."""

    def __init__(self, verilog: str):
        self.lines = verilog.splitlines()

        self.assignments = {}

    def get_assignment(self, signal: str) -> str:
        """Get the assignment expression of a signal."""

        if self.assignments.get(signal):
            return self.assignments[signal]

        patterns = [
            re.compile(rf"^\s*assign\s+{re.escape(signal)}\s*="),
            re.compile(
                rf"^\s*wire\s+(?:\[[^\]]+\]\s+)?{re.escape(signal)}(?:\s+\[[^\]]+\])?\s*="
            ),
        ]

        for idx, line in enumerate(self.lines):
            if any(pat.match(line) for pat in patterns):
                return self._collect_assignment(idx, signal)

        raise ValueError(f"Signal {signal} not found in verilog.")

    def get_assignment_by_line(self, line_no: int) -> tuple[str, str]:
        """Get (signal, assignment) for the declaration starting at the given 1-based line."""

        if line_no < 1 or line_no > len(self.lines):
            raise ValueError(f"Line {line_no} out of range.")

        idx = line_no - 1
        line = self.lines[idx]

        patterns = [
            re.compile(r"^\s*assign\s+([A-Za-z_][A-Za-z0-9_$]*)\s*="),
            re.compile(
                r"^\s*wire\s+(?:\[[^\]]+\]\s+)?([A-Za-z_][A-Za-z0-9_$]*)(?:\s+\[[^\]]+\])?\s*="
            ),
            re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s*<?=")
        ]

        for pat in patterns:
            match = pat.match(line)
            if match:
                signal = match.group(1)
                return signal, self._collect_assignment(idx, signal)

        raise ValueError(f"No assign/wire found at line {line_no}.")

    def _collect_assignment(self, start_idx: int, signal: str) -> str:
        """Collect lines from start_idx until ';' and cache the assignment."""

        expr_parts = []

        for line in self.lines[start_idx:]:
            if '=' in line and not expr_parts:
                expr_parts.append(line.split('=', 1)[1].strip())
            else:
                expr_parts.append(line.strip())

            if ';' in line:
                joined = ' '.join(expr_parts)
                assignment = joined.split(';', 1)[0].strip()
                self.assignments[signal] = assignment
                return assignment

        raise ValueError(f"Signal {signal} assignment not terminated with ';'.")
