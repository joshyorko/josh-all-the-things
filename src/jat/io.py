import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from robocorp import log
from robocorp.tasks import get_output_dir

from .models import OperationResult

RequestT = TypeVar("RequestT", bound=BaseModel)


@log.suppress
def load_request(model: type[RequestT], argv: list[str]) -> RequestT:
    try:
        index = argv.index("--json-input")
    except ValueError as error:
        raise ValueError("task requires --json-input PATH") from error
    request_args = argv[index:]
    if len(request_args) != 2:
        raise ValueError("unexpected arguments after --json-input PATH")
    path = Path(request_args[1])
    try:
        return model.model_validate(json.loads(path.read_text()))
    except ValidationError as error:
        message = f"invalid request: {format_validation_error(error)}"
    # Raise after leaving the handler so no rejected input survives as context.
    raise ValueError(message)


def format_validation_error(error: ValidationError) -> str:
    """Render validation issues without rejected inputs or validator context."""
    issues = []
    for issue in error.errors(include_url=False, include_context=False, include_input=False):
        location = (
            "<extra field>" if issue.get("type") == "extra_forbidden"
            else ".".join(str(part) for part in issue.get("loc", ()) or ())
        )
        issues.append(f"{location or '<request>'}: {issue.get('msg', 'invalid value')}")
    return "; ".join(issues)


def write_result(result: OperationResult) -> Path:
    destination = Path(get_output_dir()) / "result.json"
    result.write(destination)
    return destination
