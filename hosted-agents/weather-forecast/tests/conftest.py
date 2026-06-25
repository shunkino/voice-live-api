"""conftest.py – path setup for the weather-forecast test suite.

Adds the hosted-agents/weather-forecast directory to sys.path so that
``from agent.xxx import Yyy`` imports work when pytest is run from any
working directory.
"""
import os
import sys

# Resolve: <repo>/hosted-agents/weather-forecast
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)
