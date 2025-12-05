''' Generate IPC report from GitHub Actions logs '''

import argparse
import logging

from modules.github import GitHub
from modules.xiangshan import XiangShanAction
from modules.utils import confirm

def post(api: GitHub, issue_number: int, report: str, post_new: bool):
    if " nan " in report:
        logging.warning("Seems report have invalid results, please check the log.")
        if not confirm("Continue post"):
            logging.info("Abort.")
            return

    logging.info(f"Posting report to #{issue_number}")

    if not post_new:
        # try update existing comment if no post_new is asked
        whoami = api.user.get()["login"]
        comments = api.issues.list_comments("OpenXiangShan", "XiangShan", issue_number)
        for c in comments:
            if c["user"]["login"] == whoami:
                logging.info(f"Find comments id {c['id']} from bot, updating it...")
                api.issues.update_comment("OpenXiangShan", "XiangShan", c["id"], report)
                return

    api.issues.create_comment("OpenXiangShan", "XiangShan", issue_number, report)

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-t", "--token", type=str, required=True
    )

    parser.add_argument(
        "-b", "--base", type=str
    )

    parser.add_argument(
        "--auto-base", action="store_true"
    )

    parser.add_argument(
        "-o", "--output", type=str, default="ipc_report.md"
    )

    parser.add_argument(
        "-p", "--post", type=int,
    )

    parser.add_argument(
        "--post-new", action="store_true"
    )

    parser.add_argument(
        "--logging-level", type=str, default="INFO",
    )

    parser.add_argument(
        "actions", type=str, nargs="+"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=args.logging_level.upper(),
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    token: str = args.token
    base_id: str | None = args.base
    auto_base: bool = args.auto_base
    actions_ids: list[str] = reversed(args.actions)
    output_file: str = args.output

    api = GitHub(token)

    actions: list[XiangShanAction] = []
    for id in actions_ids:
        actions.append(XiangShanAction.from_github_api(api, id))

    if base_id is None and auto_base:
        if actions[0].pull_request == "unknown":
            raise ValueError(f"Action run #{actions[0].run_id} is not triggered by pull_request, cannot find base branch automatically")

        logging.info(f"Trying to get base branch of action {actions[0].run_id} (PR#{actions[0].pull_request})")
        resp = api.pull_requests.get("OpenXiangShan", "XiangShan", actions[0].pull_request)
        base_id = resp["base"]["sha"]
        logging.info(f"... found base branch SHA {base_id}, branch {resp['base']['ref']}")

    if base_id is not None:
        base = XiangShanAction.from_github_api(api, base_id)
    else:
        logging.warning("No base action run provided, IPC for base will not be calculated")
        base = None

    # generate markdown
    logging.info(f"Generating IPC report for {len(actions)} actions")
    report = XiangShanAction.generate_ipc_report(base, *actions)
    with open(output_file, "w") as f:
        f.write(report)
    logging.info(f"IPC report written to {output_file}")

    if args.post:
        post(api, args.post, report, args.post_new)

if __name__ == "__main__":
    main()
