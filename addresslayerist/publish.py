"""Publish build/site/ to the orphan gh-pages branch (single commit, force-push).

The tile pyramid is hundreds of thousands of files. To avoid copying them all
into a worktree, this stages them in place with a temporary index and
GIT_WORK_TREE, builds an orphan commit with git plumbing, and force-pushes it.
Each publish replaces the branch tip, so repository history never grows.
"""

import os
import re
import subprocess
import time
from datetime import date

# Publishing is the last step of a long build, so a blip here is the most
# expensive failure there is: the streets layer lost a finished 150k-tile
# pyramid on 2026-07-27 to a push that could not resolve github.com. Retry the
# push rather than the build. The same rule as layerkit.publish, which the gap
# layers use; it is repeated here so the engine keeps its two dependencies.
PUSH_RETRIES = 2
PUSH_WAIT = 300
# Only network failures are worth retrying. A rejected push or a bad credential
# fails the same way every time, and should surface now instead of ten minutes
# from now.
_NETWORK_ERR = re.compile(
    r"could not resolve host|unable to access|failed to connect"
    r"|connection timed out|operation timed out|connection reset",
    re.I,
)


def publish(cfg):
    """Commit build/site/ as a single orphan commit on gh-pages and force-push."""
    if not os.path.isdir(cfg.site_dir):
        raise RuntimeError(f"No site to publish: {cfg.site_dir}. Run 'site' first.")
    git_dir = os.path.join(cfg.project_dir, ".git")
    if not os.path.isdir(git_dir):
        raise RuntimeError(
            f"No git repo at {cfg.project_dir}. Run 'git init' and add an "
            f"'origin' remote before publishing."
        )

    index = os.path.join(cfg.build_dir, ".gh-pages-index")
    env = {
        **os.environ,
        "GIT_DIR": git_dir,
        "GIT_WORK_TREE": cfg.site_dir,
        "GIT_INDEX_FILE": index,
    }
    if os.path.exists(index):
        os.remove(index)

    print("Staging site files ...")
    _git(cfg, ["add", "-A"], env)
    tree = _git(cfg, ["write-tree"], env).strip()
    commit = _git(
        cfg, ["commit-tree", tree, "-m", f"site {date.today().isoformat()}"], env
    ).strip()
    _git(cfg, ["update-ref", "refs/heads/gh-pages", commit], env)

    print("Force-pushing gh-pages ...")
    _push(cfg, env)

    os.remove(index)
    print("Published to the gh-pages branch.")


def _push(cfg, env):
    """Force-push gh-pages, retrying a network failure.

    The commit is already written to refs/heads/gh-pages locally by the time we
    get here, so a retry costs one round-trip -- and giving up leaves the build
    on disk for a later ``publish`` rather than requiring a rebuild.
    """
    for attempt in range(PUSH_RETRIES + 1):
        try:
            _git(cfg, ["push", "--force", "origin", "gh-pages"], env)
            return
        except RuntimeError as e:
            if not _NETWORK_ERR.search(str(e)) or attempt == PUSH_RETRIES:
                raise
            print(f"  push failed ({e.args[0].splitlines()[-1]});"
                  f" retrying in {PUSH_WAIT}s")
            time.sleep(PUSH_WAIT)


def _git(cfg, args, env):
    result = subprocess.run(
        ["git", *args],
        cwd=cfg.project_dir,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout
