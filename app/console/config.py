"""Settings of the research console, read from the environment (the .env of docker compose).

The defaults follow the VPS layout in the README: /opt/research holds the three
checkouts, the data folder, the results folder, and this repository (console/).
"""

import os
from dataclasses import dataclass


def env(name: str, default: str = '') -> str:
    return os.environ.get(name, default)


def env_ints(name: str, default: str) -> tuple[int, ...]:
    return tuple(int(value) for value in env(name, default).split(',') if value.strip())


ROOT = env('RESEARCH_ROOT', '/opt/research')
DATA_DIR = env('DATA_DIR', f'{ROOT}/data')
RESULTS_DIR = env('RESULTS_DIR', f'{ROOT}/results')
K6_DIR = env('K6_DIR', f'{ROOT}/console/k6')
DUMP = env('DUMP', f'{DATA_DIR}/academico-research.sql.gz')
DB_PATH = env('CONSOLE_DB', '/data/console.db')

K6_IMAGE = env('K6_IMAGE', 'grafana/k6:1.3.0')
# Host user that owns the results folder; k6 writes its JSON summary as this user
K6_USER = env('K6_USER', '1000:1000')
INFLUX_URL = env('INFLUX_URL', 'http://127.0.0.1:8086')
INFLUX_DB = env('INFLUX_DB', 'k6')
PROMETHEUS_URL = env('PROMETHEUS_URL', 'http://127.0.0.1:9090')

# Phases of the load and stress runs (table 3.6), passed to k6 so the console knows them
WARMUP_S = int(env('WARMUP_S', '60'))
RAMP_S = int(env('RAMP_S', '30'))
SUSTAIN_S = int(env('SUSTAIN_S', '60'))
THINK_S = env('THINK_S', '1')
LOAD_VUS = env_ints('LOAD_VUS', '25,50,75')
STRESS_VUS = env_ints('STRESS_VUS', '75,125,175,225,275')

# Validity of a run: k6 must not be CPU bound and the VPS must not lose CPU to steal
K6_CPU_LIMIT = float(env('K6_CPU_LIMIT', '0.80'))
STEAL_LIMIT = float(env('STEAL_LIMIT', '0.05'))
# An invalid run is repeated at once until it has run this many times
MAX_ATTEMPTS = int(env('MAX_ATTEMPTS', '3'))
# Idle seconds between the dataset check and the start of k6
SETTLE_S = float(env('SETTLE_S', '5'))
K6_TIMEOUT_S = int(env('K6_TIMEOUT_S', '3600'))

VIEW_PASSWORD = env('VIEW_PASSWORD')
CONTROL_PASSWORD = env('CONTROL_PASSWORD')
SECRET_KEY = env('SECRET_KEY')
# Times are stored in UTC and shown with this offset (7 = WIB)
TZ_OFFSET_HOURS = float(env('TZ_OFFSET_HOURS', '7'))


@dataclass(frozen=True)
class Implementation:
    key: str
    name: str
    directory: str
    port: int
    tag: str

    @property
    def base_url(self) -> str:
        return f'http://127.0.0.1:{self.port}'

    def compose(self, *args: str) -> list[str]:
        return ['docker', 'compose', '--project-directory', self.directory,
                '-f', f'{self.directory}/docker-compose.research.yml', *args]


IMPLEMENTATIONS = {
    'ar': Implementation('ar', 'Active Record', f'{ROOT}/academico-ar', 8001, env('TAG_AR', 'v1-ar')),
    'repo': Implementation('repo', 'Repository', f'{ROOT}/academico-repo', 8002, env('TAG_REPO', 'v1-repo')),
    'qb': Implementation('qb', 'Query Builder', f'{ROOT}/academico-qb', 8003, env('TAG_QB', 'v1-qb')),
}
