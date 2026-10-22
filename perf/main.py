#!/usr/bin/env python3
"""Plot checkpoint-weighted SPEC counters from XiangShan simulator logs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from functools import lru_cache
from multiprocessing import get_context
from pathlib import Path


CounterDefinition = str | list[str] | Callable[[dict[str, float]], float]
CheckpointWeights = dict[str, float]
DEFAULT_JOBS = min(8, os.cpu_count() or 1)

COLORS = [
    # "#93C5FD",
    # "#1D4ED8",
    "#C3CEDA",
    "#2563EB",
]

# SPEC CPU2006 order from env-scripts/perf_trigger/modules/spec.py.
SPEC06_GROUPS = (
    ("SPEC06 INT", (
        "400.perlbench", "401.bzip2", "403.gcc", "429.mcf", "445.gobmk",
        "456.hmmer", "458.sjeng", "462.libquantum", "464.h264ref",
        "471.omnetpp", "473.astar", "483.xalancbmk",
    )),
    ("SPEC06 FP", (
        "410.bwaves", "416.gamess", "433.milc", "434.zeusmp", "435.gromacs",
        "436.cactusADM", "437.leslie3d", "444.namd", "447.dealII",
        "450.soplex", "453.povray", "454.calculix", "459.GemsFDTD",
        "465.tonto", "470.lbm", "481.wrf", "482.sphinx3",
    )),
)
SPEC06_ORDER = {
    alias: (group_index, benchmark_index)
    for group_index, (_, names) in enumerate(SPEC06_GROUPS)
    for benchmark_index, name in enumerate(names)
    for alias in (name, name.split(".", 1)[1])
}
SPEC06_FULL_NAMES = {
    alias: name
    for _, names in SPEC06_GROUPS
    for name in names
    for alias in (name, name.split(".", 1)[1])
}

# Extend this dictionary with regex aliases, regex groups, or derived counters.
# Callables receive BASE aliases after checkpoint weighting (no normalization).
COUNTERS: dict[str, CounterDefinition] = {
    "cycles": r"\brob:\s*clock_cycle$",
    "instructions": r"\brob:\s*commitInstr$",
    "ipc": lambda values: values["instructions"] / values["cycles"],
    "mispredict": [
        r"\bftq:\s*commit_branch_mispredicts$", # v3
        r"\bftq:\s*BpWrong$", # v2
    ],
    "mpki": lambda values: 1000 * values["mispredict"] / values["instructions"],
    "fbsize": [
        r"\bifu\.perfAnalyzer:\s*ifu2ibuffer_validCnt_sum$", # v3
        r"\bifu:\s*ifu2ibuffer_validCnt_sum$", # v2
    ],
    "fbnum": [
        r"\bifu\.perfAnalyzer:\s*ifu2ibuffer_validCnt_sampled$", # v3
        r"\bifu:\s*ifu2ibuffer_validCnt_sampled$", #v2
    ],
    "fetch_bw": lambda values: values["fbsize"] / values["fbnum"],

    "load_latency": r"\bmissQueue:\s*miss_load_refill_latency$",
    "store_latency": r"\bmissQueue:\s*miss_store_refill_latency$",
    "amo_latency": r"\bmissQueue:\s*miss_amo_refill_latency$",
    "pf_latency": r"\bmissQueue:\s*miss_pf_refill_latency$",
    "primary_fire": r"\bmissQueue\.entries_\d+:\s*miss_req_primary$",
    "refill_latency": lambda values: (
        values["load_latency"] + values["store_latency"]
        + values["amo_latency"] + values["pf_latency"]
    ) / values["primary_fire"],

    "l1d_miss": [
        r"\bprefetcherMonitor:\s*l1DemandMiss", # v3
        r"\bfdpMonitor:\s*demand_miss_en", # v2
    ],
    "l1d_mpki": lambda values: 1000 * values["l1d_miss"] / values["instructions"],

    "load_hit": r"\bdcache\.ldu_\d:\s*load_hit",
    "load_miss": r"\bdcache\.ldu_\d:\s*load_miss",
    "load_hitrate": lambda values: values["load_hit"] / (values["load_hit"] + values["load_miss"]),
}

PERF_TIME_RE = re.compile(r"^\s*\[PERF\s*\]\[time=\s*(\d+)\]\s*(.*)$")
NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
SOURCE_NAME_RE = re.compile(r"[\w][\w. -]*")
PERF_PREFIX = "[PERF ][time="


class AnalysisError(ValueError):
    """Invalid input or insufficient data to produce a comparison."""


@dataclass(frozen=True)
class DataSource:
    name: str
    path: Path


@dataclass(frozen=True)
class Checkpoint:
    log_path: Path
    benchmark: str
    weight: float


def parse_data_source(spec: str) -> DataSource:
    """Accept a bare directory or NAME:directory, preserving colons in paths."""
    if not spec.strip():
        raise argparse.ArgumentTypeError("--data must not be empty")

    path = Path(spec).expanduser()
    name: str | None = None
    # An existing bare path takes precedence. Otherwise only a legal label
    # before the FIRST colon is special, so NAME:/path/with:colons also works.
    if not path.is_dir() and ":" in spec:
        prefix, _, suffix = spec.partition(":")
        if not prefix.strip():
            raise argparse.ArgumentTypeError("data source name must not be empty")
        if SOURCE_NAME_RE.fullmatch(prefix):
            if not suffix.strip():
                raise argparse.ArgumentTypeError("data source path must not be empty")
            name = prefix
            path = Path(suffix).expanduser()

    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"data directory does not exist: {path}")
    if name is None:
        name = path.absolute().name
    if not name or name in {".", ".."}:
        name = path.resolve().name
    if not name.strip():
        raise argparse.ArgumentTypeError("data source name must not be empty")
    return DataSource(name, path)


def checkpoint_identity(name: str) -> tuple[str, str, str]:
    """Split from the right so complete workload names may contain underscores."""
    parts = name.rsplit("_", 2)
    if len(parts) != 3 or not parts[0].split("_", 1)[0] or not parts[1].isdecimal():
        raise AnalysisError(
            f"invalid checkpoint directory {name!r}; expected WORKLOAD_CHECKPOINT_WEIGHT"
        )
    return parts[0], parts[1], parts[2]


def parse_checkpoint_name(name: str) -> tuple[str, float]:
    """Parse WORKLOAD_CHECKPOINT_WEIGHT and group e.g. 500.perlbench_rate_*."""
    workload, _, raw_weight = checkpoint_identity(name)
    try:
        weight = float(raw_weight)
    except ValueError as exc:
        raise AnalysisError(f"invalid checkpoint weight in {name!r}") from exc
    if not math.isfinite(weight) or weight < 0:
        raise AnalysisError(f"checkpoint weight must be finite and non-negative: {name!r}")
    benchmark = workload.split("_", 1)[0]
    return benchmark, weight


def load_checkpoint_json(path: Path) -> CheckpointWeights:
    """Map exact WORKLOAD_POINT_RAWWEIGHT directory names to JSON weights."""
    try:
        with path.open(encoding="utf-8") as profile:
            document = json.load(profile)
    except (OSError, ValueError) as exc:
        raise AnalysisError(f"cannot read checkpoint JSON {path}: {exc}") from exc
    if not isinstance(document, dict) or not document:
        raise AnalysisError("checkpoint JSON must be a non-empty object")
    weights: CheckpointWeights = {}
    for workload, description in document.items():
        if not workload or not workload.split("_", 1)[0] or not isinstance(description, dict):
            raise AnalysisError(f"invalid workload in checkpoint JSON: {workload!r}")
        points = description.get("points")
        if not isinstance(points, dict) or not points:
            raise AnalysisError(f"{workload!r}: 'points' must be a non-empty object")
        for point, raw_weight in points.items():
            if not point.isdecimal():
                raise AnalysisError(f"{workload!r}: checkpoint ID must be an integer: {point!r}")
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError) as exc:
                raise AnalysisError(
                    f"invalid JSON weight for {workload}/{point}: {raw_weight!r}"
                ) from exc
            if isinstance(raw_weight, bool) or not math.isfinite(weight) or weight < 0:
                raise AnalysisError(
                    f"JSON weight for {workload}/{point} must be finite and non-negative"
                )
            # Preserve string weights (including trailing zeros) in the identity:
            # workload/point alone can identify several different checkpoints.
            weights[f"{workload}_{point}_{raw_weight}"] = weight
    return weights


def discover_checkpoints(
    source: DataSource, weights: CheckpointWeights | None = None
) -> list[Checkpoint]:
    """Filter by complete directory name before opening any checkpoint logs."""
    checkpoints = []
    paths = sorted(
        path for path in source.path.iterdir()
        if not path.name.startswith(".") and path.is_dir()
    )
    if weights is not None:
        available = {path.name for path in paths}
        selected_weights = {}
        missing = []
        for name, weight in weights.items():
            matched_name = name
            if matched_name not in available:
                workload, point, _ = checkpoint_identity(name)
                # CI formats directory weights with six decimals, while JSON
                # may retain more precision. Match that complete name exactly.
                matched_name = f"{workload}_{point}_{weight:.6f}"
            if matched_name in available:
                selected_weights[matched_name] = weight
            elif weight > 0:
                missing.append(name)
        if missing:
            logging.warning(
                "%s: JSON checkpoints missing from data directory: %s",
                source.name, ", ".join(sorted(missing)),
            )
        weights = selected_weights
    for path in paths:
        if weights is not None and path.name not in weights:
            continue
        try:
            workload, _, _ = checkpoint_identity(path.name)
            if weights is None:
                benchmark, weight = parse_checkpoint_name(path.name)
        except AnalysisError as exc:
            logging.warning("%s: %s; skipping", source.name, exc)
            continue
        if weights is not None:
            benchmark, weight = workload.split("_", 1)[0], weights[path.name]
        if weight == 0:
            continue
        log_path = path / "simulator_err.txt"
        if not log_path.is_file():
            logging.warning("%s: missing %s; skipping", source.name, log_path)
            continue
        checkpoints.append(Checkpoint(log_path, benchmark, weight))
    return checkpoints


def compile_counters(
    counter: str, definitions: Mapping[str, CounterDefinition]
) -> dict[str, list[re.Pattern[str]]]:
    """Select a base alias, all bases for a callable, or a literal CLI regex."""
    definition = definitions.get(counter, counter)
    bases = (
        {name: value for name, value in definitions.items() if not callable(value)}
        if callable(definition)
        else {counter: definition}
    )
    patterns: dict[str, list[re.Pattern[str]]] = {}
    for name, value in bases.items():
        expressions = [value] if isinstance(value, str) else value
        if (
            not isinstance(expressions, list)
            or not expressions
            or not all(isinstance(expression, str) for expression in expressions)
        ):
            raise AnalysisError(
                f"counter {name!r} must be a regex, a non-empty list of regexes, "
                "or a callable"
            )
        try:
            patterns[name] = [re.compile(expression) for expression in expressions]
        except re.error as exc:
            raise AnalysisError(f"invalid regex for counter {name!r}: {exc}") from exc
    return patterns


class CounterMatcher:
    """Cache positive AND negative matches across snapshots and log files."""

    def __init__(self, patterns: Mapping[str, Sequence[re.Pattern[str]]]):
        self.patterns = patterns
        self.match = lru_cache(maxsize=65536)(self._match)

    def _match(self, module: str, terminal: str) -> tuple[str, ...]:
        full_path = f"{module}:{terminal}"
        spaced_path = f"{module}: {terminal}"
        aliases = []
        for alias, expressions in self.patterns.items():
            for expression in expressions:
                if (
                    expression.search(full_path)
                    or expression.search(terminal)
                    or expression.search(spaced_path)
                ):
                    aliases.append(alias)
                    break
        return tuple(aliases)


def read_final_counters(
    path: Path, patterns: Mapping[str, Sequence[re.Pattern[str]]] | CounterMatcher
) -> dict[str, float]:
    """Extract aliases from only the greatest PERF time seen anywhere in a file.

    Keep the last value for each exact module/counter in that snapshot. Distinct
    paths matching an alias are summed, and overlapping regexes count a path once.
    Even a PERF record without a numeric counter advances the snapshot time.
    """
    matcher = patterns if isinstance(patterns, CounterMatcher) else CounterMatcher(patterns)
    latest_time = -1
    previous_stamp = None
    time = -1
    snapshot: dict[str, dict[str, float]] = {}
    with path.open(encoding="utf-8", errors="replace") as log:
        for line in log:
            # The real logs use this fixed prefix. Avoid two regex matches and
            # an int conversion on every line; retain a fallback for whitespace.
            if line.startswith(PERF_PREFIX):
                end = line.find("]", len(PERF_PREFIX))
                if end < 0:
                    continue
                stamp = line[len(PERF_PREFIX):end]
                if stamp != previous_stamp:
                    if not stamp.lstrip().isdecimal():
                        continue
                    time = int(stamp)
                    previous_stamp = stamp
                body = line[end + 1:]
            else:
                record = PERF_TIME_RE.match(line)
                if record is None:
                    continue
                time = int(record[1])
                previous_stamp = None
                body = record[2]
            if time > latest_time:
                latest_time = time
                snapshot.clear()
            elif time < latest_time:
                continue
            module, colon, rest = body.partition(":")
            terminal, comma, raw_value = rest.partition(",")
            if not colon or not comma:
                continue
            module, terminal = module.strip(), terminal.strip()
            if not module or not terminal:
                continue
            aliases = matcher.match(module, terminal)
            if not aliases:
                continue
            raw_value = raw_value.strip()
            if NUMBER_RE.fullmatch(raw_value) is None:
                continue
            value = float(raw_value)
            full_path = f"{module}:{terminal}"
            for alias in aliases:
                snapshot.setdefault(alias, {})[full_path] = value
    result = {}
    for alias, values in snapshot.items():
        if not all(math.isfinite(value) for value in values.values()):
            raise AnalysisError(f"non-finite counter for {alias!r} in final snapshot of {path}")
        result[alias] = math.fsum(values.values())
    return result


_WORKER_MATCHER: CounterMatcher | None = None


def initialize_worker(patterns: Mapping[str, Sequence[re.Pattern[str]]]) -> None:
    global _WORKER_MATCHER
    _WORKER_MATCHER = CounterMatcher(patterns)


def read_checkpoint_worker(path: Path) -> dict[str, float]:
    assert _WORKER_MATCHER is not None
    try:
        return read_final_counters(path, _WORKER_MATCHER)
    except OSError as exc:
        raise AnalysisError(f"cannot read {path}: {exc}") from exc


def read_checkpoints(
    checkpoints: Sequence[Checkpoint], patterns: Mapping[str, Sequence[re.Pattern[str]]],
    jobs: int, source_name: str,
) -> list[dict[str, float]]:
    """Report completed work immediately, but return results in directory order."""
    from tqdm import tqdm

    if jobs < 1:
        raise AnalysisError("jobs must be at least 1")
    with tqdm(total=len(checkpoints), desc=f"Loading {source_name}", unit="checkpoint") as progress:
        if jobs == 1 or len(checkpoints) < 2:
            matcher = CounterMatcher(patterns)
            results = []
            for checkpoint in checkpoints:
                try:
                    results.append(read_final_counters(checkpoint.log_path, matcher))
                except OSError as exc:
                    raise AnalysisError(
                        f"cannot read {checkpoint.log_path}: {exc}"
                    ) from exc
                progress.update()
            return results
        ordered: list[dict[str, float]] = [{} for _ in checkpoints]
        with ProcessPoolExecutor(
            max_workers=min(jobs, len(checkpoints)), mp_context=get_context("spawn"),
            initializer=initialize_worker, initargs=(patterns,),
        ) as pool:
            futures = {
                pool.submit(read_checkpoint_worker, checkpoint.log_path): index
                for index, checkpoint in enumerate(checkpoints)
            }
            try:
                for future in as_completed(futures):
                    ordered[futures[future]] = future.result()
                    progress.update()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        return ordered


def load_source_counters(
    source: DataSource,
    counter: str,
    definitions: Mapping[str, CounterDefinition] | None = None,
    *, weights: CheckpointWeights | None = None, jobs: int = 1,
) -> dict[str, dict[str, float]]:
    """Read once for every alias; require usable results for the requested one."""
    from tqdm.contrib.logging import logging_redirect_tqdm

    definitions = dict(COUNTERS if definitions is None else definitions)
    definitions.setdefault(counter, counter)
    patterns = {}
    for name, definition in definitions.items():
        if not callable(definition):
            patterns.update(compile_counters(name, definitions))
    totals: dict[str, dict[str, float]] = {}
    complete: dict[str, set[str]] = {}
    with logging_redirect_tqdm():
        checkpoints = discover_checkpoints(source, weights)
        parsed = read_checkpoints(checkpoints, patterns, jobs, source.name)
    checkpoint_count = len(checkpoints)
    for checkpoint, values in zip(checkpoints, parsed):
        benchmark = checkpoint.benchmark
        benchmark_totals = totals.setdefault(benchmark, {})
        complete.setdefault(benchmark, set(patterns)).intersection_update(values)
        for alias, value in values.items():
            benchmark_totals[alias] = benchmark_totals.get(alias, 0.0) + value * checkpoint.weight

    results: dict[str, dict[str, float]] = {name: {} for name in definitions}
    for benchmark, values in totals.items():
        incomplete = values.keys() - complete[benchmark]
        if incomplete:
            logging.warning(
                "%s / %s: counters missing from some final snapshots: %s",
                source.name, benchmark, ", ".join(sorted(incomplete)),
            )
        bases = {name: value for name, value in values.items() if name in complete[benchmark]}
        for name, definition in definitions.items():
            try:
                value = float(definition(dict(bases)) if callable(definition) else bases[name])
                if not math.isfinite(value):
                    raise ValueError("result is not finite")
            except (KeyError, ArithmeticError, TypeError, ValueError) as exc:
                # Optional aliases may be unavailable in this log format. Only
                # report evaluation failures for the counter being plotted.
                if name == counter:
                    logging.warning(
                        "%s / %s: cannot compute %r from final snapshots (%s); skipping benchmark",
                        source.name, benchmark, name, exc,
                    )
                continue
            results[name][benchmark] = value

    if not results[counter]:
        raise AnalysisError(
            f"{source.name}: no usable benchmarks for counter {counter!r} "
            f"({checkpoint_count} checkpoint logs read)"
        )
    logging.info(
        "%s: loaded %d checkpoints, %d benchmarks", source.name, checkpoint_count, len(results[counter])
    )
    return {name: values for name, values in results.items() if values}


def load_source(
    source: DataSource,
    counter: str,
    definitions: Mapping[str, CounterDefinition] | None = None,
    *, weights: CheckpointWeights | None = None, jobs: int = 1,
) -> dict[str, float]:
    """Extract one counter, including its base dependencies for a callable."""
    if definitions is None:
        definitions = COUNTERS
    definition = definitions.get(counter, counter)
    selected = {
        name: value for name, value in definitions.items() if not callable(value)
    } if callable(definition) else {}
    selected[counter] = definition
    return load_source_counters(
        source, counter, selected, weights=weights, jobs=jobs,
    )[counter]


def source_cache_path(source: DataSource, cache_dir: Path = Path(".cache")) -> Path:
    """Use a stable path hash so changing a source's legend label reuses its data."""
    digest = hashlib.sha256(str(source.path.resolve()).encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.json"


def read_source_cache(path: Path, data_path: str) -> dict:
    try:
        with path.open(encoding="utf-8") as cache_file:
            cache = json.load(cache_file)
        if (
            not isinstance(cache, dict) or cache.get("version") != 1
            or cache.get("data") != data_path or not isinstance(cache.get("profiles"), dict)
        ):
            raise ValueError("invalid cache format")
        return cache
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        logging.warning("cannot use cache %s (%s); extracting data", path, exc)
    return {"version": 1, "data": data_path, "profiles": {}}


def write_source_cache(path: Path, cache: dict) -> None:
    """Replace atomically so an interrupted write cannot leave partial JSON."""
    temporary_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.stem}-", suffix=".tmp", delete=False,
        ) as cache_file:
            temporary_path = Path(cache_file.name)
            json.dump(cache, cache_file, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            cache_file.write("\n")
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_cached_source(
    source: DataSource, counter: str, *, weights: CheckpointWeights | None = None,
    jobs: int = 1, force_update: bool = False, cache_dir: Path = Path(".cache"),
) -> dict[str, float]:
    """On a miss, cache every configured alias from one scan, before GEOMEAN."""
    path = source_cache_path(source, cache_dir)
    cache = read_source_cache(path, str(source.path.resolve()))
    # The filename depends only on --data. Keep independent alias tables for
    # each JSON selection/weight set inside that file, including unfiltered data.
    profile = "all" if weights is None else hashlib.sha256(
        json.dumps(weights, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    counters = cache["profiles"].get(profile, {})
    if not isinstance(counters, dict):
        counters = {}
    values = counters.get(counter)
    if not force_update and isinstance(values, dict) and values and all(
        isinstance(benchmark, str) and isinstance(value, (int, float))
        and not isinstance(value, bool) and math.isfinite(value)
        for benchmark, value in values.items()
    ):
        logging.info("%s: cache hit for %r (%s)", source.name, counter, path)
        return values

    extracted = load_source_counters(source, counter, weights=weights, jobs=jobs)
    # All configured aliases were just re-extracted. Drop stale entries that
    # can no longer be computed, while preserving other ad hoc regex results.
    for name in {*COUNTERS, counter}:
        counters.pop(name, None)
    counters.update(extracted)
    cache["profiles"][profile] = counters
    try:
        write_source_cache(path, cache)
    except (OSError, ValueError) as exc:
        logging.warning("cannot write cache %s: %s", path, exc)
    else:
        logging.info("%s: cached %d counters in %s", source.name, len(extracted), path)
    return extracted[counter]


def benchmark_sort_key(benchmark: str) -> tuple[int, int, str]:
    """Order INT, FP, unrecognized names, then GEOMEAN; retain original labels."""
    if benchmark == "GEOMEAN":
        return 3, 0, benchmark
    group, rank = SPEC06_ORDER.get(benchmark, (2, 0))
    return group, rank, benchmark


def common_benchmarks(results: Mapping[str, Mapping[str, float]]) -> list[str]:
    """Use the intersection for both the plot and CSV, warning about omissions."""
    benchmark_sets = [set(values) for values in results.values()]
    if not benchmark_sets:
        raise AnalysisError("no data sources")
    union = set.union(*benchmark_sets)
    for name, values in results.items():
        missing = union - values.keys()
        if missing:
            logging.warning(
                "%s: missing benchmarks: %s; using intersection",
                name, ", ".join(sorted(missing)),
            )
    common = sorted(set.intersection(*benchmark_sets), key=benchmark_sort_key)
    if not common:
        raise AnalysisError("data sources have no benchmarks in common")
    return common


def add_geomean(
    results: Mapping[str, Mapping[str, float]], benchmarks: Sequence[str]
) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Append each source's geometric mean over the benchmarks being plotted."""
    augmented = {}
    for name, source_values in results.items():
        values = [source_values[benchmark] for benchmark in benchmarks]
        if not values or any(not math.isfinite(value) or value < 0 for value in values):
            raise AnalysisError(f"{name}: GEOMEAN requires finite, non-negative benchmark values")
        if 0 in values:
            mean = 0.0
        elif len(values) == 1:
            mean = values[0]
        else:
            # Computing a product first can overflow or underflow on raw counters.
            mean = math.exp(math.fsum(math.log(value) for value in values) / len(values))
        augmented[name] = {**source_values, "GEOMEAN": mean}
    return augmented, [*benchmarks, "GEOMEAN"]


def compare_results(
    results: Mapping[str, Mapping[str, float]], benchmarks: Sequence[str]
) -> dict[str, dict[str, float]]:
    """Convert values to percentage change relative to the first data source."""
    if not results:
        raise AnalysisError("percent mode requires at least one data source")
    baseline_name, baseline_values = next(iter(results.items()))
    converted: dict[str, dict[str, float]] = {}
    for benchmark in benchmarks:
        baseline = baseline_values[benchmark]
        if not math.isfinite(baseline) or baseline == 0:
            raise AnalysisError(
                f"percent mode requires a finite, non-zero baseline for {benchmark!r} "
                f"in data source {baseline_name!r}"
            )
    for name, values in results.items():
        converted[name] = {
            benchmark: (values[benchmark] / baseline_values[benchmark] - 1.0) * 100.0
            for benchmark in benchmarks
        }
    return converted

def plot_comparison(
    results: Mapping[str, Mapping[str, float]], benchmarks: Sequence[str], counter: str,
    *, y_lim: Sequence[float] | None = None, width: float | None = None,
    height: float = 6, font_size: float = 16, legend_font_size: float = 24,
    no_ytitle: bool = False, no_xtitle: bool = False, no_legend: bool = False,
    no_xlabel: bool = False, no_ylabel: bool = False,
    mode: str = "value",
):
    """Build a single-color or grouped bar chart; caller owns the figure.

    ``percent`` formats ratio-valued bars as percentages, while ``compare``
    expects values already converted to percentage deltas.
    """
    # Import only when plotting so parsing and --help need no matplotlib.
    from matplotlib import colormaps, rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import PercentFormatter
    from matplotlib.transforms import ScaledTranslation

    if mode not in {"value", "percent", "compare"}:
        raise AnalysisError(f"unsupported plot mode: {mode!r}")

    font_settings = {
        "font.size": font_size,
        "xtick.labelsize": font_size,
        "ytick.labelsize": font_size,
        "axes.labelsize": legend_font_size,
        "legend.fontsize": legend_font_size,
    }
    benchmarks = sorted(benchmarks, key=benchmark_sort_key)
    with rc_context(font_settings):
        figure_width = width if width is not None else max(9, len(benchmarks) * 0.65)
        figure = Figure(figsize=(figure_width, height))
        FigureCanvasAgg(figure)
        axes = figure.subplots()
        source_count = len(results)
        bar_step = 0.8 / source_count
        bar_width = bar_step * (0.9 if source_count > 1 else 1)
        palette = colormaps["tab10" if source_count <= 10 else "hsv"]
        upper_annotations = []
        for index, (name, values) in enumerate(results.items()):
            positions = [
                position - 0.4 + bar_step * (index + 0.5)
                for position in range(len(benchmarks))
            ]
            actual_values = [values[benchmark] for benchmark in benchmarks]
            display_values = actual_values if y_lim is None else [
                min(max(value, y_lim[0]), y_lim[1]) for value in actual_values
            ]
            # color = palette(index if source_count <= 10 else index / source_count)
            color = COLORS[index % len(COLORS)]
            axes.bar(
                positions, display_values, width=bar_width, label=name, color=color,
            )
            if y_lim is not None:
                for position, actual in zip(positions, actual_values):
                    if y_lim[0] <= actual <= y_lim[1]:
                        continue
                    below = actual < y_lim[0]
                    boundary = y_lim[0] if below else y_lim[1]
                    axes.plot(
                        position, boundary, marker="v" if below else "^",
                        color="black", markersize=4, clip_on=False, zorder=5,
                    )
                    annotation = axes.annotate(
                        (
                            f"{actual * 100:.2f}%" if mode == "percent"
                            else f"{actual:.2f}{'%' if mode == 'compare' else ''}"
                        ),
                        xy=(position, boundary),
                        xytext=(0, 6), textcoords="offset points",
                        ha="center", va="bottom", annotation_clip=False, zorder=6,
                    )
                    if not below:
                        upper_annotations.append((position, annotation))
        # Stagger in visual left-to-right order, including across data sources
        # and benchmark groups. Scale the row gap with the selected font size.
        for index, (_, annotation) in enumerate(sorted(upper_annotations, key=lambda item: item[0])):
            size = annotation.get_fontsize()
            offset = 6 + (index % 2) * (1.2 * size + 4)
            annotation.set_position((0, offset))
        # Divide only the groups that are present in this comparison.
        start = 0
        while start < len(benchmarks):
            group = benchmark_sort_key(benchmarks[start])[0]
            end = start + 1
            while end < len(benchmarks) and benchmark_sort_key(benchmarks[end])[0] == group:
                end += 1
            if start:
                axes.axvline(start - 0.5, color="0.35", linewidth=1, zorder=2)
            start = end
        if y_lim is not None:
            axes.set_ylim(*y_lim)
        labels = [SPEC06_FULL_NAMES.get(benchmark, benchmark) for benchmark in benchmarks]
        axes.set_xticks(range(len(benchmarks)), labels, rotation=45, ha="right")
        if no_xlabel:
            axes.tick_params(axis="x", which="both", labelbottom=False, labeltop=False)
            axes.xaxis.get_offset_text().set_visible(False)
        if no_ylabel:
            axes.tick_params(axis="y", which="both", labelleft=False, labelright=False)
            axes.yaxis.get_offset_text().set_visible(False)
        if not no_xtitle:
            axes.set_xlabel("SPEC benchmark")
        if not no_ytitle:
            axes.set_ylabel(f"{counter} (%)" if mode in {"percent", "compare"} else counter)
        if mode == "percent":
            axes.yaxis.set_major_formatter(PercentFormatter(xmax=1))
        elif mode == "compare":
            axes.yaxis.set_major_formatter(PercentFormatter(xmax=100))
        if not no_legend:
            axes.legend(loc="upper right")
        axes.grid(axis="y", linestyle="--", alpha=0.3)
        axes.set_axisbelow(True)
        # Reserve the same horizontal space for a given figure/font size,
        # independent of tick text, counter names, or hidden labels/titles.
        ytitle_distance = 3 * font_size + 12  # points from the left spine
        if not no_ytitle:
            axes.yaxis.set_label_coords(
                0, 0.5,
                transform=axes.transAxes + ScaledTranslation(
                    -ytitle_distance / 72, 0, figure.dpi_scale_trans,
                ),
            )
        figure.tight_layout()
        width_points = figure_width * 72
        left_padding = ytitle_distance + 1.2 * legend_font_size + 12
        right_padding = max(12, font_size)
        figure.subplots_adjust(
            left=min(left_padding / width_points, 0.45),
            right=1 - min(right_padding / width_points, 0.1),
        )
    return figure


def write_csv(
    path: Path, results: Mapping[str, Mapping[str, float]], benchmarks: Sequence[str]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["benchmark", *results])
        for benchmark in benchmarks:
            writer.writerow([benchmark, *(values[benchmark] for values in results.values())])


def finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("must be finite")
    return number


def positive_float(value: str) -> float:
    number = finite_float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data", action="append", required=True, type=parse_data_source,
        metavar="[NAME:]PATH", help="data root (repeat for multiple sources)",
    )
    parser.add_argument(
        "counter", help="COUNTERS alias or a regex matching module:counter / counter name"
    )
    parser.add_argument(
        "--output", type=Path,
        help="output image (default: <counter>_comparison.png); CSV uses the same stem",
    )
    parser.add_argument(
        "--json", type=Path, metavar="PATH",
        help="checkpoint profile: match exact WORKLOAD_POINT_WEIGHT names and use JSON weights",
    )
    parser.add_argument(
        "--jobs", "-j", type=int, default=DEFAULT_JOBS, metavar="N",
        help=f"worker processes (default: {DEFAULT_JOBS}; 1 for serial loading)",
    )
    parser.add_argument(
        "--force-update", action="store_true",
        help="reload all configured aliases and the requested counter from logs into the cache",
    )
    parser.add_argument(
        "--y-lim", type=finite_float, nargs=2, metavar=("MIN", "MAX"),
        help="y-axis bounds; mark truncated bars with their original values",
    )
    parser.add_argument(
        "--width", type=positive_float, help="figure width in inches (default: automatic)",
    )
    parser.add_argument(
        "--height", type=positive_float, default=6, help="figure height in inches (default: 6)",
    )
    parser.add_argument(
        "--font-size", type=positive_float, default=16, metavar="POINTS",
        help="font size for ticks and annotations (default: 16)",
    )
    parser.add_argument(
        "--legend-font-size", type=positive_float, default=24, metavar="POINTS",
        help="font size for legend and axis titles (default: 24)",
    )
    parser.add_argument(
        "--mode", choices=("value", "percent", "compare"), default="value",
        help="plot raw values, format ratios as percentages, or compare to the first "
             "data source (default: value)",
    )
    parser.add_argument("--no-ytitle", action="store_true", help="hide the y-axis title")
    parser.add_argument("--no-xtitle", action="store_true", help="hide the x-axis title")
    parser.add_argument("--no-legend", action="store_true", help="hide the legend")
    parser.add_argument("--no-xlabel", action="store_true", help="hide x-axis tick labels")
    parser.add_argument("--no-ylabel", action="store_true", help="hide y-axis tick labels")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    names = [source.name for source in args.data]
    if len(set(names)) != len(names):
        parser.error("duplicate data source names; assign a unique NAME to each --data")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.y_lim is not None and args.y_lim[0] >= args.y_lim[1]:
        parser.error("--y-lim MIN must be less than MAX")

    # A raw regex may contain slashes or other filename delimiters.
    safe_counter = re.sub(r"[^\w.-]+", "_", args.counter).strip("._") or "counter"
    output = args.output or Path("output") / f"{safe_counter}_comparison.png"
    if not output.suffix:
        output = output.with_suffix(".png")
    if not output.parent.exists():
        output.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output.with_suffix(".csv")
    if csv_path == output:
        parser.error("--output must be an image filename, not a CSV filename")
    try:
        compile_counters(args.counter, COUNTERS)
        weights = load_checkpoint_json(args.json.expanduser()) if args.json else None
        results = {
            source.name: load_cached_source(
                source, args.counter, weights=weights, jobs=args.jobs, force_update=args.force_update,
            )
            for source in args.data
        }
        benchmarks = common_benchmarks(results)
        results, benchmarks = add_geomean(results, benchmarks)
        if args.mode == "compare":
            results = compare_results(results, benchmarks)
        figure = plot_comparison(
            results, benchmarks, args.counter, y_lim=args.y_lim,
            width=args.width, height=args.height, font_size=args.font_size,
            legend_font_size=args.legend_font_size,
            no_ytitle=args.no_ytitle, no_xtitle=args.no_xtitle, no_legend=args.no_legend,
            no_xlabel=args.no_xlabel, no_ylabel=args.no_ylabel,
            mode=args.mode,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(output, dpi=160)
        write_csv(csv_path, results, benchmarks)
    except ImportError as exc:
        parser.exit(
            1, f"error: missing dependency ({exc}); install dependencies with "
            "python3 -m pip install -r requirements.txt\n",
        )
    except (OSError, ValueError, ArithmeticError, BrokenProcessPool) as exc:
        parser.exit(1, f"error: {exc}\n")
    logging.info("wrote %s and %s", output, csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
