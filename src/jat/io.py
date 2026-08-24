import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel
from robocorp.tasks import get_output_dir

from .models import OperationResult

RequestT = TypeVar("RequestT", bound=BaseModel)


def load_request(model: type[RequestT], argv: list[str]) -> RequestT:
    try:
        index = argv.index("--json-input")
    except ValueError as error:
        raise ValueError("task requires --json-input PATH") from error
    request_args = argv[index:]
    if len(request_args) != 2:
        raise ValueError("unexpected arguments after --json-input PATH")
    path = Path(request_args[1])
    return model.model_validate(json.loads(path.read_text()))


def write_result(result: OperationResult) -> Path:
    destination = Path(get_output_dir()) / "result.json"
    result.write(destination)
    return destination
