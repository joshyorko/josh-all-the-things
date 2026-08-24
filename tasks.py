#!/usr/bin/env python3

from robocorp.tasks import task

from jat.io import load_request, write_result
from jat.legacy import LegacyBashService
from jat.models import BuildRequest, RestoreRequest, ServeRequest


def _service() -> LegacyBashService:
    return LegacyBashService()


def _finish(result) -> None:
    write_result(result)
    if not result.success:
        raise RuntimeError(result.diagnostics or f"{result.operation} failed")


@task
def Build(json_input: str):
    _finish(_service().build(load_request(BuildRequest, ["--json-input", json_input])))


@task
def Restore(json_input: str):
    _finish(_service().restore(load_request(RestoreRequest, ["--json-input", json_input])))


@task
def Serve(json_input: str):
    _finish(_service().serve(load_request(ServeRequest, ["--json-input", json_input])))


@task
def Doctor():
    _finish(_service().doctor())
