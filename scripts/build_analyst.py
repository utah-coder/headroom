#!/usr/bin/env python3
"""Build the analyst's Lambda bundle without Docker.

pip fetches prebuilt Linux x86_64 wheels for Python 3.12, whatever machine this
runs on, so it works from Windows, CI or CloudShell. Keep this file compatible
with old Python versions: CloudShell's system python3 may be older than the
project's, and it only needs to run pip and copy files.

Output: infra/build/analyst, or $HEADROOM_ANALYST_BUNDLE. In CloudShell that is
under /tmp, because the 1 GB home directory can't hold the bundle and cdk.out.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "infra", "build", "analyst")
LAMBDA_UNZIPPED_LIMIT_MB = 250
PLATFORMS = [
    "manylinux2014_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux_2_27_x86_64",
    "manylinux_2_28_x86_64",
]
PRUNE_DIRS = {"__pycache__", "tests", "test"}


def size_mb(path):
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(folder, name))
    return total / 1_000_000


def prune(path):
    for folder, dirs, files in os.walk(path, topdown=True):
        for name in list(dirs):
            if name in PRUNE_DIRS:
                shutil.rmtree(os.path.join(folder, name))
                dirs.remove(name)
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                os.remove(os.path.join(folder, name))


def main():
    out = os.path.abspath(os.environ.get("HEADROOM_ANALYST_BUNDLE", DEFAULT_OUT))
    if out == ROOT or ROOT.startswith(out + os.sep) or out == os.path.dirname(out):
        sys.exit("Refusing to build into {}: it would delete the repo.".format(out))
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)

    command = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
               "--target", out, "--only-binary=:all:", "--implementation", "cp",
               "--python-version", "3.12", "--abi", "cp312", "--abi", "abi3", "--abi", "none",
               "-r", os.path.join(ROOT, "analyst", "requirements.txt")]
    for platform in PLATFORMS:
        command += ["--platform", platform]
    print("==> Fetching Linux wheels for Python 3.12")
    subprocess.check_call(command)

    prune(out)

    print("==> Copying the analyst, the engine and the grid data")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "tests")
    shutil.copytree(os.path.join(ROOT, "analyst"), os.path.join(out, "analyst"), ignore=ignore)
    shutil.copytree(os.path.join(ROOT, "sim"), os.path.join(out, "sim"), ignore=ignore)
    shutil.copytree(os.path.join(ROOT, "data", "raw"), os.path.join(out, "data", "raw"))

    size = size_mb(out)
    print("==> Bundle: {} ({:.0f} MB unzipped)".format(out, size))
    if size >= LAMBDA_UNZIPPED_LIMIT_MB:
        sys.exit("Bundle is over Lambda's {} MB limit.".format(LAMBDA_UNZIPPED_LIMIT_MB))


if __name__ == "__main__":
    main()
