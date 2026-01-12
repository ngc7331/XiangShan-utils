import argparse
import logging
import math
import os
import random
import re
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm


class XiangShanPerf:
    def __init__(self, spec_dir: str):
        self.spec_dir = Path(spec_dir)

        self.checkpoints: list[Path] = []

        with logging_redirect_tqdm():
            for checkpoint in tqdm(self.spec_dir.iterdir(), desc="Processing"):
                if not checkpoint.is_dir():
                    continue
                logging.debug("Checkpoint: %s", checkpoint.name)
                if os.path.exists(checkpoint / "simulator_err.txt"):
                    self.checkpoints.append(checkpoint)

    def load(self, perf: str) -> dict[str, dict[str, float]]:
        if not self.checkpoints:
            logging.warning(
                "No checkpoints with simulator_err.txt found under %s", self.spec_dir
            )
            return {}

        perf_regex = re.compile(perf)
        checkpoints = sorted(self.checkpoints)
        baseline = checkpoints[0] / "simulator_err.txt"

        # Perf line example: [PERF ][time=   123456] abc.def.xxx: counter_name, 666
        line_regex = re.compile(
            r"^\[PERF\s*\]\[time=\s*\d+\]\s+([^:]+):\s*([^,]+),\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
        )

        targets: set[str] = set()
        try:
            with baseline.open("r") as f:
                for line in f:
                    if not perf_regex.search(line):
                        continue
                    match = line_regex.match(line.strip())
                    if not match:
                        continue
                    module_name = match.group(1).strip()
                    counter_name = match.group(2).strip()
                    targets.add(f"{module_name}:{counter_name}")
        except FileNotFoundError:
            logging.warning("Baseline simulator_err.txt not found at %s", baseline)
            return {}

        result: dict[str, dict[str, float]] = {target: {} for target in targets}

        for checkpoint in tqdm(checkpoints, desc="Loading checkpoints"):
            checkpoint_name = checkpoint.name
            values = {target: float("nan") for target in targets}
            file_path = checkpoint / "simulator_err.txt"

            try:
                with file_path.open("r") as f:
                    for line in f:
                        match = line_regex.match(line.strip())
                        if not match:
                            continue
                        module_name = match.group(1).strip()
                        counter_name = match.group(2).strip()
                        key = f"{module_name}:{counter_name}"
                        if key not in targets:
                            continue
                        try:
                            value = float(match.group(3))
                        except (TypeError, ValueError):
                            value = float("nan")
                        # Keep the last occurrence in the file
                        values[key] = value
            except FileNotFoundError:
                logging.debug(
                    "simulator_err.txt missing in checkpoint %s", checkpoint_name
                )

            for target in targets:
                result[target][checkpoint_name] = values[target]

        return result

    def plot(
        self,
        perf: str,
        whis: float = 1.5,
        max_annotations: int = 50,
        annotation_mode: str = "max",
        y_max: float | None = None,
    ) -> None:
        whis = max(0.0, whis)
        max_annotations = max(0, max_annotations)
        annotation_mode = annotation_mode.lower()
        if annotation_mode not in {"max", "min", "random"}:
            logging.warning(
                "Unknown annotation_mode '%s', fallback to 'max'", annotation_mode
            )
            annotation_mode = "max"
        data = self.load(perf)
        if not data:
            logging.warning("No data to plot for regex: %s", perf)
            return

        counters = sorted(data.keys())
        checkpoint_names = sorted(
            {name for counters_map in data.values() for name in counters_map}
        )
        if not counters or not checkpoint_names:
            logging.warning("Counters or checkpoints are empty, skip plotting")
            return

        series: list[list[float]] = []
        labels: list[str] = []
        per_counter_items: list[list[tuple[str, float]]] = []
        for counter in counters:
            items = [(cp, v) for cp, v in data[counter].items() if not math.isnan(v)]
            if not items:
                continue
            labels.append(counter)
            series.append([v for _, v in items])
            per_counter_items.append(items)

        if not series:
            logging.warning("All selected counters are NaN; nothing to plot")
            return

        fig_width = max(10, len(labels) * .75)
        fig_height = 20
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))

        box = ax.boxplot(
            series,
            patch_artist=True,
            tick_labels=labels,
            showfliers=True,
            whis=whis,
        )
        for patch in box["boxes"]:
            patch.set_facecolor("#a6cee3")
        for median in box["medians"]:
            median.set_color("#1f78b4")

        def percentile(sorted_vals: list[float], pct: float) -> float:
            if not sorted_vals:
                return float("nan")
            k = (len(sorted_vals) - 1) * pct
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return sorted_vals[int(k)]
            d = k - f
            return sorted_vals[f] * (1 - d) + sorted_vals[c] * d

        for idx, items in enumerate(per_counter_items, start=1):
            vals_only = sorted([v for _, v in items])
            q1 = percentile(vals_only, 0.25)
            q3 = percentile(vals_only, 0.75)
            iqr = q3 - q1
            lower = q1 - whis * iqr
            upper = q3 + whis * iqr

            median_val = percentile(vals_only, 0.5)
            mean_val = sum(vals_only) / len(vals_only) if vals_only else float("nan")

            # Mean dashed line and label
            ax.hlines(
                mean_val,
                idx - 0.25,
                idx + 0.25,
                colors=["#33a02c"],
                linestyles="dashed",
                linewidth=1,
                zorder=2,
            )
            ax.text(
                idx + 0.27,
                mean_val,
                f"mean {mean_val}",
                fontsize=7,
                va="bottom",
                ha="left",
                color="#33a02c",
            )

            # Quartile and median labels to the right
            ax.text(
                idx + 0.27,
                q1,
                f"Q1 {q1}",
                fontsize=7,
                va="center",
                ha="left",
                color="#555555",
            )
            ax.text(
                idx + 0.27,
                median_val,
                f"median {median_val}",
                fontsize=7,
                va="center",
                ha="left",
                color="#1f78b4",
            )
            ax.text(
                idx + 0.27,
                q3,
                f"Q3 {q3}",
                fontsize=7,
                va="center",
                ha="left",
                color="#555555",
            )

            outliers = [(cp, val) for cp, val in items if val < lower or val > upper]
            if annotation_mode == "max":
                outliers.sort(key=lambda x: x[1], reverse=True)
            elif annotation_mode == "min":
                outliers.sort(key=lambda x: x[1])
            else:  # random
                random.shuffle(outliers)

            for j, (cp, val) in enumerate(outliers[:max_annotations]):
                x_offset = 0.1 if j % 2 == 0 else -0.1
                y_offset = 0.0
                ax.scatter(idx, val, color="#e31a1c", s=20, zorder=3)
                ax.text(
                    idx + x_offset,
                    val + y_offset,
                    cp,
                    fontsize=7,
                    va="center",
                    ha="left" if x_offset > 0 else "right",
                )

        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel("Counter Value")
        ax.set_xlabel("Module:Counter")
        ax.set_title(f"Perf counters matching '{perf}'")
        ax.grid(True, linestyle="--", alpha=0.3)
        if y_max is not None:
            ax.set_ylim(top=y_max)

        # Give rotated labels enough room
        plt.subplots_adjust(bottom=0.25, top=0.9, right=0.95)

        backend = matplotlib.get_backend().lower()
        is_interactive = not backend.endswith("agg")

        if is_interactive:
            plt.show()
        else:
            safe_perf = re.sub(r"[^\w.-]+", "_", perf)
            out_path = self.spec_dir / f"perf_plot_{safe_perf}.png"
            fig.savefig(str(out_path), bbox_inches="tight")
            logging.info("Saved plot to %s (backend %s)", out_path, backend)
            plt.close(fig)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "spec_dir", type=str, help="Directory containing the spec06 logs"
    )

    parser.add_argument(
        "perf", type=str, help="Performance metric to analyze, supports regex"
    )

    parser.add_argument("--plot", action="store_true", help="Whether to generate plots")
    parser.add_argument(
        "--whis",
        type=float,
        default=1.5,
        help="Whisker length in IQR multiples for boxplot/outlier detection",
    )
    parser.add_argument(
        "--max-annotations",
        type=int,
        default=50,
        help="Maximum number of outlier labels to draw",
    )
    parser.add_argument(
        "--annotation-mode",
        choices=["max", "min", "random"],
        default="max",
        help="Outlier labeling priority: largest, smallest, or random",
    )
    parser.add_argument(
        "--y-max",
        type=float,
        default=None,
        help="Set an explicit upper limit for the Y axis",
    )

    args = parser.parse_args()

    xs = XiangShanPerf(args.spec_dir)

    if args.plot:
        xs.plot(
            args.perf,
            whis=args.whis,
            max_annotations=args.max_annotations,
            annotation_mode=args.annotation_mode,
            y_max=args.y_max,
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    main()
