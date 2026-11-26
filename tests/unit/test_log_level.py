"""The log defaults to INFO. DEBUG is opt-in: the environment for a one-off
run, or the Settings checkbox for a frozen app you cannot easily hand an
environment to."""

from __future__ import annotations

import logging

from core.runtime import log_level


def test_info_by_default() -> None:
    assert log_level({}, verbose=False) == logging.INFO


def test_the_settings_checkbox_turns_debug_on() -> None:
    assert log_level({}, verbose=True) == logging.DEBUG


def test_the_environment_wins_either_way() -> None:
    assert log_level({"TK_LOG_LEVEL": "DEBUG"}, verbose=False) == logging.DEBUG
    assert log_level({"TK_LOG_LEVEL": "warning"}, verbose=True) == logging.WARNING


def test_a_bad_environment_value_falls_back() -> None:
    assert log_level({"TK_LOG_LEVEL": "loud"}, verbose=False) == logging.INFO
