import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Settings are read at import time: point the console at throwaway folders first
_tmp = tempfile.mkdtemp(prefix='console-test-')
os.environ.setdefault('CONSOLE_DB', os.path.join(_tmp, 'console.db'))
os.environ.setdefault('RESULTS_DIR', os.path.join(_tmp, 'results'))
os.environ.setdefault('VIEW_PASSWORD', 'view-secret')
os.environ.setdefault('CONTROL_PASSWORD', 'control-secret')
os.environ.setdefault('SECRET_KEY', 'test-secret')

import pytest  # noqa: E402

from console import db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(str(tmp_path / 'test.db'))
    db.init(connection)
    yield connection
    connection.close()
