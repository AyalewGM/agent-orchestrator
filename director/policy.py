"""Operator-owned repository registry; model output cannot enable projects."""
from pathlib import Path
import tomllib


def projects():
    return tomllib.loads((Path(__file__).resolve().parents[1] / 'projects.toml').read_text())['projects']
