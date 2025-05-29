''' Simple Github API wrapper '''

import os
from typing import IO, Any
import re
import zipfile

import requests

class ApiGroup:
    ''' Internal API group base class. '''
    def __init__(self, api: "GitHub"):
        self.api = api

class Actions(ApiGroup):
    ''' Internal Actions API group. '''
    def list_runs(
        self,
        owner: str,
        repo: str,
        head_sha: str | None = None,
    ) -> dict:
        ''' List workflow runs for a repository. '''
        return self.api.get(
            f"repos/{owner}/{repo}/actions/runs",
            params={
                "head_sha": head_sha,
            }
        )

    def get_run(
        self,
        owner: str,
        repo: str,
        run_id: int,
    ) -> dict:
        ''' Get a specific workflow run by its ID. '''
        return self.api.get(
            f"repos/{owner}/{repo}/actions/runs/{run_id}",
        )

    def get_log(
        self,
        owner: str,
        repo: str,
        run_id: int,
        job_name: str,
        force_download: bool = False,
        group_filters: dict[str, re.Pattern] | list[re.Pattern] | None = None,
    ) -> dict[str, str]:
        ''' Get logs for a specific job in a workflow run. '''

        path = f"actions_logs/{owner}/{repo}/{run_id}.zip"
        if force_download or not self.api.cache_exists(path):
            content = self.api.get_raw(
                f"repos/{owner}/{repo}/actions/runs/{run_id}/logs",
                stream=True
            ).content

            with self.api.cache_open(path, "wb") as f:
                f.write(content)

        with zipfile.ZipFile(self.api.cache_open(path, "rb")) as zf:
            for name in zf.filelist:
                if not re.match(rf"\d+_{re.escape(job_name)}\.txt", name.filename):
                    continue

                with zf.open(name) as f:
                    log = f.read().decode("utf-8")

                # drop line prefix (timestamp)
                log = re.sub(
                    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ",
                    "",
                    log,
                    flags=re.MULTILINE
                )

                log_groups = {}
                current_title = None
                in_params = False
                for line in log.splitlines():
                    if match := re.match(r"^##\[group\](.+)$", line):
                        current_title = None
                        _current_title = match.group(1).strip()
                        if group_filters is None:
                            current_title = _current_title
                        elif isinstance(group_filters, list):
                            if not any(pattern.match(_current_title) for pattern in group_filters):
                                continue
                            current_title = _current_title
                        elif isinstance(group_filters, dict):
                            matched_title = None
                            for title, pattern in group_filters.items():
                                if pattern.match(_current_title):
                                    matched_title = title
                                    break
                            if matched_title is None:
                                continue
                            current_title = matched_title
                        in_params = True
                        log_groups[current_title] = []
                    elif re.match(r"^##\[endgroup\]$", line):
                        in_params = False
                    elif not in_params and current_title is not None:
                        log_groups[current_title].append(line)

                for title in log_groups:
                    log_groups[title] = "\n".join(log_groups[title])

                return log_groups

            raise ValueError(f"Job {job_name} not found in run {run_id} logs")

class PullRequests(ApiGroup):
    def get(
        self,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> dict:
        ''' Get a specific pull request by its number. '''
        return self.api.get(
            f"repos/{owner}/{repo}/pulls/{pull_number}",
        )

class GitHub:
    ''' Simple GitHub API wrapper. '''
    def __init__(
        self,
        token: str,
        url: str = "api.github.com",
        proto: str = "https",
        port: int = 443,
        cachedir: str = "./.github_cache",
    ):
        self.__token = token
        self.__url = url
        self.__proto = proto
        self.__port = port
        self.__cachedir = cachedir

        self.actions = Actions(self)
        self.pull_requests = PullRequests(self)

    def __request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        resp = requests.request(
            method,
            f"{self.__proto}://{self.__url}:{self.__port}/{endpoint}",
            timeout=30,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.__token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            **kwargs
        )
        resp.raise_for_status()
        return resp

    def get_raw(self, endpoint: str, **kwargs) -> requests.Response:
        ''' Get raw content from a GitHub API endpoint. '''
        return self.__request("get", endpoint, **kwargs)

    def get(self, endpoint: str, **kwargs) -> dict:
        ''' Get JSON content from a GitHub API endpoint. '''
        return self.__request("get", endpoint, **kwargs).json()

    def cache_open(self, path: str, mode: str) -> IO[Any]:
        ''' Open a file in the cache directory. '''
        path = os.path.join(self.__cachedir, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return open(path, mode) # pylint: disable=unspecified-encoding

    def cache_exists(self, path: str) -> bool:
        ''' Check if a file exists in the cache directory. '''
        path = os.path.join(self.__cachedir, path)
        return os.path.exists(path)
