''' XiangShan Utils '''
import re
import logging
from typing import Dict
import math

from .github import GitHub
from .utils import geometric_mean

class XiangShanLog:
    ''' Parser class for XiangShan stdout & stderr logs '''
    def __init__(self, content: str):
        self.content = content

    def get_ipc(self) -> float:
        ''' Extract IPC from the log content. '''
        match = re.search(r"IPC = (\d+\.\d+)", self.content)
        if match:
            return float(match.group(1))
        raise ValueError("IPC not found in log")

class XiangShanSummary:
    ''' Parser class for XiangShan summary logs '''
    def __init__(self, content: str):
        self.content = content

    def get_ipc(self) -> Dict[str, float]:
        ''' Extract IPC from the log content. '''
        ipc: Dict[str, float] = {}
        for line in self.content.splitlines():
            if match := re.match(r".*echo \"\| (\S+) \| (\d+\.\d+) \|", line):
                ipc[match.group(1)] = float(match.group(2))
        return ipc

class XiangShanAction:
    ''' Parser class for XiangShan action runs '''

    BASICS_TESTCASES = [
        "coremark",
        "linux",
        "microbench",
        "povray",
        "copy_and_run",
    ]

    PERFORMANCE_TESTCASES = [
        "hmmer-Vector",
        "mcf",
        "xalancbmk",
        "gcc",
        "namd",
        "milc",
        "lbm",
        "gromacs",
        "wrf",
        "astar",
    ]

    ALL_TESTCASES = sorted(BASICS_TESTCASES + PERFORMANCE_TESTCASES)

    def __init__(
        self,
        run_id: int,
        summaries: dict[str, str],
        branch: str | None = None,
        commit_sha: str | None = None,
        pull_request: int | None = None,
        updated_at: str | None = None,
    ):
        self.run_id = run_id
        self.summaries = {
            k: XiangShanSummary(v)
            for k, v in sorted(summaries.items())
        }
        self.branch = branch
        self.commit_sha = commit_sha
        self.pull_request = pull_request
        self.updated_at = updated_at

        self.ipcs = {}
        for summary in self.summaries.values():
            self.ipcs.update(summary.get_ipc())

        self.META = {
            "Branch": self.branch_str,
            "Commit": self.commit_sha_str,
            "PR": self.pull_request_str,
            "Updated": self.updated_at_str,
        }

        logging.debug(
            f"Parsing logs from run {self.run_id_str()}\n" +
            "\n".join(f"... {meta}: {func()}" for meta, func in self.META.items()) + "\n" +
            f"... Testcases: {', '.join(self.ipcs.keys())}"
        )

    def run_id_str(self, *, is_base: bool = False) -> str:
        ''' Get run ID as a string. Add " (base)" suffix if is_base is True. '''
        return f"{self.run_id} (base)" if is_base else str(self.run_id)

    def branch_str(self) -> str:
        ''' Get branch name as a string, or "unknown" if not set. '''
        return self.branch or "unknown"

    def commit_sha_str(self) -> str:
        ''' Get commit SHA as a string, or "unknown" if not set. '''
        return self.commit_sha or "unknown"

    def pull_request_str(self) -> str:
        ''' Get pull request number as a string, or "unknown" if not set. '''
        return f"#{self.pull_request}" if self.pull_request is not None else "unknown"

    def updated_at_str(self) -> str:
        ''' Get updated_at timestamp as a string, or "unknown" if not set. '''
        return self.updated_at or "unknown"

    @staticmethod
    def from_github_api(api: GitHub, id: str) -> "XiangShanAction":
        ''' Get logs from GitHub Actions and generate XiangShanLog '''

        logging.info(f"Fetching metadata of {id}")

        try:
            id = int(id)
        except ValueError:
            pass

        if isinstance(id, int):
            logging.debug(f"... assuming it's a run ID")
            meta = api.actions.get_run("OpenXiangShan", "XiangShan", id)
            run_id = id
        else: # str
            if id.startswith("#"):
                logging.debug(f"... assuming it's a PR number")
                meta = api.pull_requests.get("OpenXiangShan", "XiangShan", int(id[1:]))
                id = meta["head"]["sha"]
            else:
                logging.debug(f"... assuming it's a commit SHA")
            meta = api.actions.list_runs("OpenXiangShan", "XiangShan", id)
            if meta["total_count"] == 0:
                raise ValueError(f"No workflow runs found for commit SHA {id}")
            meta = meta["workflow_runs"][0]
            run_id = meta["id"]

        logging.info(f"Getting logs for run {run_id} from GitHub Actions")

        summaries = {}
        try:
            filters = [re.compile(rf"Run echo \"##.*summary.*")]
            summaries = api.actions.get_log(
                "OpenXiangShan",
                "XiangShan",
                job_id="EMU - Basics",
                run_id=run_id,
                group_filters=filters,
            )
            summaries.update(api.actions.get_log(
                "OpenXiangShan",
                "XiangShan",
                job_id="EMU - Performance",
                run_id=run_id,
                group_filters=filters,
            ))
        except ValueError as e:
            if re.match(r"Job .+ not found in run \d+ logs", e.args[0]):
                logging.warning(e)
            else:
                raise e

        return XiangShanAction(
            run_id,
            summaries,
            branch=meta["head_branch"],
            commit_sha=meta["head_sha"],
            pull_request=meta["pull_requests"][0]["number"] if len(meta["pull_requests"]) > 0 else None,
            updated_at=meta["updated_at"],
        )

    def get_ipc(self) -> dict[str, float]:
        ''' Get IPC for all test cases in the action logs. '''
        ipc = {}

        for testcase in self.ALL_TESTCASES:
            if testcase not in self.ipcs:
                ipc[testcase] = float("NaN")
                continue
            try:
                ipc[testcase] = self.ipcs[testcase]
            except ValueError as e:
                logging.warning(e)
                ipc[testcase] = float("NaN")

        ipc["GEOMEAN"] = geometric_mean(ipc.values())

        return ipc

    def get_diff(self, base: "XiangShanAction") -> dict[str, float]:
        ''' Get IPC diff compared to the base action. '''
        # XiangShanAction.get_ipc() ensures we have IPC values for all test cases
        ipc = self.get_ipc()
        base_ipc = base.get_ipc()
        diff = {
            testcase: ipc[testcase] / base_ipc[testcase] - 1 if not (math.isnan(ipc[testcase]) or math.isnan(base_ipc[testcase])) else .0
            for testcase in self.ALL_TESTCASES
        }
        diff["GEOMEAN"] = ipc["GEOMEAN"] / base_ipc["GEOMEAN"] - 1
        return diff

    def generate_ipc_report_line(self, base: "XiangShanAction | None" = None, *, is_base: bool = False) -> str:
        ''' Generate a markdown table line for the IPC report. '''
        md = f"| {self.run_id_str(is_base=is_base)} "
        md += "| " + " | ".join(f"{func()}" for func in self.META.values()) + " "
        md += "| " + " | ".join(f"{ipc}" for ipc in self.get_ipc().values()) + " "
        md += "|\n"
        if not is_base and base is not None:
            diff = self.get_diff(base).values()
            md += f"| {self.run_id} diff "
            md += "| " * (len(self.META) + 1)
            md += " | ".join(f"{d:.2%}" for d in diff) + " "
            md += "|\n"
        return md

    @staticmethod
    def generate_ipc_report(base: "XiangShanAction | None" = None, accumulative_base: bool = False, *actions: "XiangShanAction") -> str:
        ''' Generate a markdown IPC report for the given actions. '''
        md = "# IPC Report"
        md += "\n\n"
        md += "| Run # "
        md += "| " + " | ".join(f"{meta}" for meta in actions[0].META.keys()) + " "
        md += "| " + " | ".join(actions[0].ALL_TESTCASES) + " "
        md += "| **GEOMEAN** |\n"
        md += "| :---: " * (len(actions[0].ALL_TESTCASES) + len(actions[0].META) + 2) + "|\n"
        if base is not None:
            md += base.generate_ipc_report_line(base, is_base=True)
        for action in actions:
            md += action.generate_ipc_report_line(base)
            if accumulative_base:
                base = action

        return md
