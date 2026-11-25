"""Checks before a run (and while the runner is idle, for the home page)."""

import os
import re
import subprocess

import httpx

from . import config
from .config import Implementation

APP_STACK = re.compile(r'^research-(ar|repo|qb)-')

_checksums: dict = {'key': None, 'result': None}


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def git(impl: Implementation, *args: str) -> subprocess.CompletedProcess:
    # The checkout belongs to the host user, not to root in this container
    return _run(['git', '-c', 'safe.directory=*', '-C', impl.directory, *args])


def commit(impl: Implementation) -> str | None:
    result = git(impl, 'rev-parse', 'HEAD')
    return result.stdout.strip() if result.returncode == 0 else None


def checkout(impl: Implementation) -> tuple[bool, str]:
    if not os.path.isdir(impl.directory):
        return False, f'{impl.directory} tidak ada'
    described = git(impl, 'describe', '--tags', '--exact-match', 'HEAD')
    tag = described.stdout.strip()
    if described.returncode != 0 or tag != impl.tag:
        return False, f'HEAD bukan tag {impl.tag} ({tag or described.stderr.strip()})'
    status = git(impl, 'status', '--porcelain', '--untracked-files=no')
    if status.returncode != 0 or status.stdout.strip():
        return False, 'checkout berisi perubahan yang belum di-commit'
    return True, f'{impl.tag} ({(commit(impl) or "")[:8]})'


def images(impl: Implementation) -> tuple[bool, str]:
    listed = _run(impl.compose('config', '--images'))
    if listed.returncode != 0:
        return False, listed.stderr.strip()[:200]
    missing = [image for image in listed.stdout.split()
               if _run(['docker', 'image', 'inspect', image]).returncode != 0]
    if missing:
        return False, 'image belum ada: ' + ', '.join(missing) + ' (jalankan compose build)'
    return True, ', '.join(listed.stdout.split())


def running_stacks() -> list[str]:
    listed = _run(['docker', 'ps', '--format', '{{.Names}}'])
    return sorted(name for name in listed.stdout.split() if APP_STACK.match(name))


def monitoring() -> tuple[bool, str]:
    problems = []
    try:
        if httpx.get(f'{config.PROMETHEUS_URL}/-/ready', timeout=5).status_code != 200:
            problems.append('Prometheus belum siap')
    except httpx.HTTPError as error:
        problems.append(f'Prometheus: {error}')
    try:
        if httpx.get(f'{config.INFLUX_URL}/ping', timeout=5).status_code != 204:
            problems.append('InfluxDB tidak membalas ping')
    except httpx.HTTPError as error:
        problems.append(f'InfluxDB: {error}')
    return (False, '; '.join(problems)) if problems else (True, 'Prometheus dan InfluxDB siap')


def dataset() -> tuple[bool, str]:
    """sha256sum -c SHA256SUMS in the data folder, cached until a file changes"""
    sums = os.path.join(config.DATA_DIR, 'SHA256SUMS')
    if not os.path.isfile(sums):
        return False, f'{sums} tidak ada'
    with open(sums, encoding='utf-8') as file:
        names = [line.split(maxsplit=1)[1].strip().lstrip('*') for line in file if line.strip()]
    key = tuple((name, os.path.getmtime(os.path.join(config.DATA_DIR, name)))
                for name in names if os.path.exists(os.path.join(config.DATA_DIR, name)))
    if _checksums['key'] != key:
        result = _run(['sha256sum', '-c', 'SHA256SUMS'], cwd=config.DATA_DIR, timeout=120)
        ok = result.returncode == 0
        _checksums.update(key=key, result=(ok, f'{len(names)} file cocok' if ok else result.stdout.strip()[-300:]))
    return _checksums['result']


def preflight(impls: list[Implementation], expect_stopped: bool = True) -> list[dict]:
    results = []

    def add(name, check):
        try:
            ok, detail = check()
        except Exception as error:  # a check that cannot run is a failed check
            ok, detail = False, str(error)
        results.append({'name': name, 'ok': ok, 'detail': detail})

    add('monitoring', monitoring)
    add('dataset', dataset)
    if expect_stopped:
        add('stack lain', lambda: (not (names := running_stacks()), ', '.join(names) or 'tidak ada yang berjalan'))
    for impl in impls:
        add(f'checkout {impl.key}', lambda impl=impl: checkout(impl))
        add(f'image {impl.key}', lambda impl=impl: images(impl))
    return results
