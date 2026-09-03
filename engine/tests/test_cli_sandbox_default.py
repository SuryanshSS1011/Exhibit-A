"""The container sandbox is the default; the host path must be requested explicitly.

An untrusted checkout's suite runs its own ``conftest.py``, so choosing the host
executor is a decision to execute that repository's code here. It is therefore opt-in,
and callers that cannot be trusted to choose (the web route) never get a say.
"""

from __future__ import annotations

import argparse

from exhibit_a import cli
from exhibit_a.executor.docker_exec import DockerExecutor
from exhibit_a.executor.local_exec import LocalExecutor


def test_sandbox_is_the_default():
    assert cli._use_sandbox(argparse.Namespace())
    assert cli._use_sandbox(argparse.Namespace(no_sandbox=False))


def test_legacy_docker_flag_is_accepted_but_no_longer_decides():
    assert cli._use_sandbox(argparse.Namespace(docker=False))
    assert cli._use_sandbox(argparse.Namespace(docker=True))


def test_host_executor_requires_an_explicit_opt_out():
    assert not cli._use_sandbox(argparse.Namespace(no_sandbox=True))
    assert not cli._use_sandbox(argparse.Namespace(no_sandbox=True, docker=True))


def test_build_engine_maps_the_choice_onto_the_executor():
    assert isinstance(cli._build_engine(use_docker=True, offline=True).executor, DockerExecutor)
    assert isinstance(cli._build_engine(use_docker=False, offline=True).executor, LocalExecutor)
