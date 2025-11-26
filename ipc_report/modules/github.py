''' Simple Github API wrapper '''

import os
from typing import IO, Any
import re
import logging

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

    def list_jobs(
        self,
        owner: str,
        repo: str,
        run_id: int,
    ) -> dict:
        ''' List jobs for a specific workflow run. '''
        return self.api.get(
            f"repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
        )

    def get_log(
        self,
        owner: str,
        repo: str,
        *,
        job_id: str | int,
        run_id: int | None = None,
        force_download: bool = False,
        group_filters: dict[str, re.Pattern] | list[re.Pattern] | None = None,
    ) -> dict[str, str]:
        ''' Get logs for a specific job in a workflow run. '''

        if isinstance(job_id, str):
            if run_id is None:
                raise ValueError("run_id must be specified if job_id is a string")
            # try find job_id by name
            jobs = self.list_jobs(owner, repo, run_id)["jobs"]
            for job in jobs:
                if job["name"] == job_id:
                    job_id = int(job["id"])
                    break

        if not isinstance(job_id, int):
            raise ValueError(f"job_id must be an integer or a job name string, provided '{job_id}' not found in workflow run #{run_id}")

        path = f"actions_logs/{owner}/{repo}/{job_id}.txt"
        if force_download or not self.api.cache_exists(path):
            try:
                content = self.api.get_raw(
                    f"repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
                    stream=True
                ).content
            except Exception as e:
                logging.error(f"Failed to download logs for job {job_id}: {e}")
                return {}

            with self.api.cache_open(path, "wb") as f:
                f.write(content)

        with self.api.cache_open(path, "rb") as f:
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
                log_groups[current_title] = []
            elif current_title is not None:
                log_groups[current_title].append(line)

        for title in log_groups:
            log_groups[title] = "\n".join(log_groups[title])

        return log_groups

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

class Issues(ApiGroup):
    def list_comments(
        self,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> dict:
        ''' List review comments on a issue '''
        return self.api.get(
            f"repos/{owner}/{repo}/issues/{issue_number}/comments"
        )
    
    def create_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str
    ) -> dict:
        ''' Create a review comment for a issue '''
        return self.api.post(
            f"repos/{owner}/{repo}/issues/{issue_number}/comments",
            json = {"body": body}
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
        self.issues = Issues(self)
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
    
    def post(self, endpoint: str, **kwargs) -> dict:
        return self.__request("post", endpoint, **kwargs).json()

    def cache_open(self, path: str, mode: str) -> IO[Any]:
        ''' Open a file in the cache directory. '''
        path = os.path.join(self.__cachedir, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return open(path, mode) # pylint: disable=unspecified-encoding

    def cache_exists(self, path: str) -> bool:
        ''' Check if a file exists in the cache directory. '''
        path = os.path.join(self.__cachedir, path)
        return os.path.exists(path)
