"""Builds a small synthetic dataset once per test run and points the API at it."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    data = tmp_path_factory.mktemp("demo")
    py = sys.executable
    subprocess.run([py, str(REPO / "pipeline/python/make_synthetic.py"), "--preset", "demo",
                    "--per-season", "8", "--sample-dt", "6", "--data-dir", str(data)], check=True)
    subprocess.run([py, str(REPO / "pipeline/python/flatten.py"), "--data-dir", str(data)], check=True)
    os.environ["FN_DEMO_DIR"] = str(data)
    sys.path.insert(0, str(REPO / "api"))
    from fastapi.testclient import TestClient
    from fnlab.main import app
    return TestClient(app)
