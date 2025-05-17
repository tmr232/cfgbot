import subprocess
from pathlib import Path
from subprocess import CalledProcessError
from typing import Iterable
from functools import partial
from concurrent.futures.thread import ThreadPoolExecutor
import json
import typer
import tempfile
from git import Repo
import os
import glob
from more_itertools import chunked
import rich
import structlog

log = structlog.get_logger()
app = typer.Typer()

FILES_PER_GROUP = 50
SCAN_CODEBASE_PATH = r"C:\Code\sandbox\function-graph-overview\scripts\scan-codebase.ts"
PROJECTS_TO_SCAN = (
    "kubernetes/kubernetes",
    "microsoft/typescript-go",
    "microsoft/typescript",
    "python/cpython",
    "golang/go",
    "django/django",
    "pypy/pypy",
    "facebook/react",
)


def iter_file_groups(root, files_per_group):
    yield from chunked(
        filter(
            lambda path: os.path.isfile(os.path.join(root, path)),
            glob.iglob("**/*", root_dir=root, recursive=True),
        ),
        files_per_group,
    )


def scan_files(
    root: str, gh_project: str, git_ref: str, files: list[str]
) -> str | None:
    with tempfile.NamedTemporaryFile("rt+") as outfile:
        try:
            subprocess.check_call(
                [
                    "bun",
                    SCAN_CODEBASE_PATH,
                    "--root",
                    root,
                    "--project",
                    gh_project,
                    "--ref",
                    git_ref,
                    "--out",
                    outfile.name,
                    *files,
                ]
            )
            outfile.seek(0)
            return outfile.read()
        except CalledProcessError as e:
            log.error("Failed processing files", exception=e, files=files)
            return None


def merge_indices(indices: Iterable[str]) -> str:
    json_indices = map(json.loads, indices)
    merged_index = next(json_indices)
    for json_index in json_indices:
        merged_index["content"]["functions"].extend(json_index["content"]["functions"])
    return json.dumps(merged_index)


def scan_project(gh_project: str) -> str:
    repo_url = f"https://github.com/{gh_project}"
    with tempfile.TemporaryDirectory() as workdir:
        repo_dir = os.path.join(workdir, "repo")
        log.info("Cloning", repo_url=repo_url)
        with Repo.clone_from(repo_url, repo_dir, depth=1) as repo:
            log.info("Clone complete", repo_url=repo_url)
            return scan_repo(gh_project, repo)


def scan_repo(gh_project: str, repo: Repo) -> str:
    commit_hash = repo.head.commit.hexsha
    repo_dir = repo.working_dir
    with ThreadPoolExecutor(max_workers=2) as executor:
        return merge_indices(
            filter(
                None,
                executor.map(
                    partial(scan_files, repo_dir, gh_project, commit_hash),
                    iter_file_groups(repo_dir, FILES_PER_GROUP),
                ),
            )
        )


def index_projects(out_dir: Path):
    os.makedirs(out_dir, exist_ok=True)
    for gh_project in PROJECTS_TO_SCAN:
        log.info("Indexing", project=gh_project)
        out_path = out_dir / f"{gh_project.replace("/","_")}.json"
        if out_path.exists():
            log.info("Index already exists. Moving to next project.")
            continue
        out_path.write_text(scan_project(gh_project))


@app.command()
def main(out_dir: Path):
    index_projects(out_dir)
