#!/usr/bin/env python3
"""Compatibility entry point for the Edge visual capture driver."""
from pathlib import Path
import subprocess

script = Path(__file__).with_name("capture.mjs")
raise SystemExit(subprocess.call(["node", str(script)]))
