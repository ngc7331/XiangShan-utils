''' XiangShan Utils '''
import re
import logging

from .github import GitHub

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

class XiangShanAction:
    ''' Parser class for XiangShan action runs '''

    BASICS_TESTCASES = [
        "coremark",
        "linux",
        "microbench",
        "povray",
        "copy-and-run",
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
        logs: dict[str, str],
        branch: str | None = None,
        commit_sha: str | None = None,
        pull_request: int | None = None,
        updated_at: str | None = None,
    ):
        self.run_id = run_id
        self.logs = {
            k: XiangShanLog(v)
            for k, v in sorted(logs.items())
        }
        self.branch = branch
        self.commit_sha = commit_sha
        self.pull_request = pull_request
        self.updated_at = updated_at
        logging.debug(
            f"Parsing logs from run {self.run_id_str()}\n"
            f"... Branch {self.branch_str()}\n"
            f"... Commit {self.commit_sha_str()}\n"
            f"... PR {self.pull_request_str()}\n"
            f"... Updated at {self.updated_at_str()}\n"
            f"... Testcases: {', '.join(self.logs.keys())}"
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

        logs = {}
        try:
            basics_filters = {
                testcase: re.compile(rf".*--ci {testcase}.*")
                for testcase in XiangShanAction.BASICS_TESTCASES
            }
            basics_filters["copy-and-run"] = re.compile(r".*copy_and_run.*") # fix
            logs = api.actions.get_log(
                "OpenXiangShan",
                "XiangShan",
                job_id="EMU - Basics",
                run_id=run_id,
                group_filters=basics_filters,
            )
            performance_filters = {
                testcase: re.compile(rf".*--ci {testcase}.*")
                for testcase in XiangShanAction.PERFORMANCE_TESTCASES
            }
            logs.update(api.actions.get_log(
                "OpenXiangShan",
                "XiangShan",
                job_id="EMU - Performance",
                run_id=run_id,
                group_filters=performance_filters,
            ))
        except ValueError as e:
            if re.match(r"Job .+ not found in run \d+ logs", e.args[0]):
                logging.warning(e)
            else:
                raise e

        return XiangShanAction(
            run_id,
            logs,
            branch=meta["head_branch"],
            commit_sha=meta["head_sha"],
            pull_request=meta["pull_requests"][0]["number"] if len(meta["pull_requests"]) > 0 else None,
            updated_at=meta["updated_at"],
        )

    def get_ipc(self) -> dict[str, float]:
        ''' Get IPC for all test cases in the action logs. '''
        ipc = {}

        for testcase in self.ALL_TESTCASES:
            if testcase not in self.logs:
                ipc[testcase] = float("NaN")
                continue
            try:
                ipc[testcase] = self.logs[testcase].get_ipc()
            except ValueError as e:
                logging.warning(e)
                ipc[testcase] = float("NaN")

        return ipc

    def get_improvement(self, base: "XiangShanAction") -> dict[str, float]:
        ''' Get IPC improvement compared to the base action. '''
        # XiangShanAction.get_ipc() ensures we have IPC values for all test cases
        ipc = self.get_ipc()
        base_ipc = base.get_ipc()

        return {
            testcase: ipc[testcase] / base_ipc[testcase] - 1
            for testcase in self.ALL_TESTCASES
        }

    def generate_ipc_report_line(self, base: "XiangShanAction | None" = None, *, is_base: bool = False) -> str:
        ''' Generate a markdown table line for the IPC report. '''
        md = f"| {self.run_id_str(is_base=is_base)} "
        md += f"| {self.branch_str()} "
        md += f"| {self.commit_sha_str()} "
        md += f"| {self.pull_request_str()} "
        md += f"| {self.updated_at_str()} "
        md += "| " + " | ".join(f"{ipc}" for ipc in self.get_ipc().values()) + " |\n"
        if base is not None:
            md += f"| {self.run_id} improvement | | | | | "
            md += " | ".join(f"{improvement:.2%}" for improvement in self.get_improvement(base).values()) + " |\n"
        return md

    @staticmethod
    def generate_ipc_report(base: "XiangShanAction | None" = None, *actions: "XiangShanAction") -> str:
        ''' Generate a markdown IPC report for the given actions. '''
        md = "# IPC Report\n"
        md += "\n"
        md += "| Run # | Branch | Commit | PR | Updated | " + " | ".join(XiangShanAction.ALL_TESTCASES) + " |\n"
        md += "| :---: " * (len(XiangShanAction.ALL_TESTCASES) + 5) + "|\n"
        if base is not None:
            md += base.generate_ipc_report_line(is_base=True)
        for action in actions:
            md += action.generate_ipc_report_line(base)

        return md
