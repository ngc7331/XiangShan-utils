#!/usr/bin/env python3
"""Select top-N checkpoints per testcase family from a JSON spec file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple


ORDER_INT = [
	"400.perlbench",
	"401.bzip2",
	"403.gcc",
	"429.mcf",
	"445.gobmk",
	"456.hmmer",
	"458.sjeng",
	"462.libquantum",
	"464.h264ref",
	"471.omnetpp",
	"473.astar",
	"483.xalancbmk",
]

ORDER_FP = [
	"410.bwaves",
	"416.gamess",
	"433.milc",
	"434.zeusmp",
	"435.gromacs",
	"436.cactusADM",
	"437.leslie3d",
	"444.namd",
	"447.dealII",
	"450.soplex",
	"453.povray",
	"454.calculix",
	"459.GemsFDTD",
	"465.tonto",
	"470.lbm",
	"481.wrf",
	"482.sphinx3",
	"999.specrand",
]

ORDER_ALL = ORDER_INT + ORDER_FP


def collect_points(data: Dict[str, dict]) -> Dict[str, List[Tuple[str, str, float]]]:
	"""Group all checkpoint weights by testcase prefix.

	Prefix is everything before the first underscore in the testcase name
	(e.g. `bzip2_chicken` and `bzip2_combined` share prefix `bzip2`).
	"""

	grouped: Dict[str, List[Tuple[str, str, float]]] = {}
	for testcase, payload in data.items():
		prefix = testcase.split("_", 1)[0]
		points = payload.get("points", {}) or {}
		for ckpt, raw_weight in points.items():
			try:
				weight = float(raw_weight)
			except (TypeError, ValueError):
				# Skip entries with non-numeric weights
				continue
			grouped.setdefault(prefix, []).append((testcase, ckpt, weight))
	return grouped


def top_n(grouped: Dict[str, List[Tuple[str, str, float]]], n: int) -> Dict[str, List[dict]]:
	"""Pick the top-N checkpoints per testcase prefix."""

	result: Dict[str, List[dict]] = {}
	for prefix, items in grouped.items():
		items_sorted = sorted(items, key=lambda x: x[2], reverse=True)
		top_items = items_sorted[:n]
		result[prefix] = [
			{"testcase": testcase, "ckpt": ckpt, "weight": weight}
			for testcase, ckpt, weight in top_items
		]
	return result


def order_key(prefix: str) -> int:
	"""Return ordering index based on SPEC06 list; unknowns go last."""

	for idx, name in enumerate(ORDER_ALL):
		short = name.split(".", 1)[-1]
		if prefix == name or prefix == short:
			return idx
	return len(ORDER_ALL)


def resolve_alias(alias: str, data: Dict[str, List[dict]]) -> str | None:
	"""Find the actual prefix key matching an ordered alias (with or without numeric)."""

	if alias in data:
		return alias
	short = alias.split(".", 1)[-1]
	return short if short in data else None


def render_text(filtered: Dict[str, List[dict]]) -> str:
	"""Render human-readable text grouped by SPEC06 order."""

	lines: List[str] = []
	seen: set[str] = set()
	sections = [
		("Part 1: Integer Benchmarks", ORDER_INT),
		("Part 2: Floating Point Benchmarks", ORDER_FP),
	]

	for title, order_list in sections:
		lines.append(title)
		for alias in order_list:
			key = resolve_alias(alias, filtered)
			if key is None:
				continue
			seen.add(key)
			lines.append(f"- {alias}")
			for item in filtered[key]:
				lines.append(
					f"  - {item['testcase']} ckpt {item['ckpt']} weight {item['weight']:.6f}"
				)

	# Append any remaining prefixes not in the official order, sorted by order_key then name.
	remaining_keys = set(filtered.keys()) - seen
	if remaining_keys:
		lines.append("Other")
		for key in sorted(remaining_keys, key=lambda k: (order_key(k), k)):
			lines.append(f"- {key}")
			for item in filtered[key]:
				lines.append(
					f"  - {item['testcase']} ckpt {item['ckpt']} weight {item['weight']:.6f}"
				)

	return "\n".join(lines)


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Select top-N checkpoints per testcase family from a spec JSON file.",
	)
	parser.add_argument("input", type=Path, help="Path to spec JSON file")
	parser.add_argument(
		"--top",
		type=int,
		default=3,
		help="Number of checkpoints to keep per testcase prefix (default: 3)",
	)
	parser.add_argument(
		"--format",
		choices=["json", "text"],
		default="json",
		help="Output format: json (default) or text for human-readable order",
	)
	parser.add_argument(
		"--output",
		type=Path,
		help="Optional path to write filtered JSON; prints to stdout if omitted",
	)
	return parser.parse_args()


def main() -> None:
	args = parse_args()
	data = json.loads(args.input.read_text())

	grouped = collect_points(data)
	filtered = top_n(grouped, args.top)

	if args.format == "json":
		output_text = json.dumps(filtered, indent=2, sort_keys=True)
	else:
		output_text = render_text(filtered)
	if args.output:
		args.output.write_text(output_text)
	else:
		print(output_text)


if __name__ == "__main__":
	main()
