"""JSON mappings of the OGC API – Processes facade (Part 1: Core 1.0), as pure functions.

Nothing here touches the request, the policy or storage; ``ogc_api`` supplies
absolute URLs and expiring file links, so every mapping can be tested alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ObjectDoesNotExist

from .models import SimulationModel, SimulationRun

CONF = "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/"
CONFORMANCE_CLASSES = [CONF + name for name in ("core", "ogc-process-description", "json", "oas30", "job-list")]
REL = "http://www.opengis.net/def/rel/ogc/1.0/"
EXCEPTIONS = "http://www.opengis.net/def/exceptions/ogcapi-processes-1/1.0/"
OPENAPI_TYPE = "application/vnd.oai.openapi+json;version=3.0"

NO_SUCH_PROCESS = EXCEPTIONS + "no-such-process"
NO_SUCH_JOB = EXCEPTIONS + "no-such-job"
RESULT_NOT_READY = EXCEPTIONS + "result-not-ready"

#: ``type`` of exceptions without a process-specific URI: the HTTP status semantics (RFC 9110).
STATUS_TYPES = {
    400: "https://www.rfc-editor.org/rfc/rfc9110#section-15.5.1",
    401: "https://www.rfc-editor.org/rfc/rfc9110#section-15.5.2",
    403: "https://www.rfc-editor.org/rfc/rfc9110#section-15.5.4",
    404: "https://www.rfc-editor.org/rfc/rfc9110#section-15.5.5",
    406: "https://www.rfc-editor.org/rfc/rfc9110#section-15.5.7",
    409: "https://www.rfc-editor.org/rfc/rfc9110#section-15.5.10",
    415: "https://www.rfc-editor.org/rfc/rfc9110#section-15.5.16",
    429: "https://www.rfc-editor.org/rfc/rfc6585#section-4",
    500: "https://www.rfc-editor.org/rfc/rfc9110#section-15.6.1",
}

#: Reserved inputs TOSCA adds to every process; all other names are model parameters.
SEED, TITLE, VISIBILITY = "tosca_seed", "tosca_title", "tosca_visibility"
VISIBILITIES = ("private", "organization")

#: OGC status -> the run ``execution_state`` values it covers.
STATUS_STATES = {
    "accepted": [SimulationRun.ExecutionState.QUEUED],
    "running": [SimulationRun.ExecutionState.RUNNING],
    "successful": [SimulationRun.ExecutionState.SUCCEEDED, SimulationRun.ExecutionState.IMPORTED],
    "failed": [SimulationRun.ExecutionState.FAILED],
}
_STATE_STATUS = {state: status for status, states in STATUS_STATES.items() for state in states}


def link(href: str, rel: str, *, type: str = "application/json", title: str | None = None) -> dict:
    data = {"href": href, "rel": rel, "type": type}
    if title:
        data["title"] = title
    return data


def exception_body(status: int, *, title: str, detail: str | None = None, type: str | None = None, **extra) -> dict:
    """The OGC exception document (RFC 7807 shaped); ``type`` is the only required member."""
    body = {"type": type or STATUS_TYPES.get(status, STATUS_TYPES[500]), "title": title, "status": status}
    if detail:
        body["detail"] = detail
    return {**body, **extra}


# -- processes ------------------------------------------------------------------


def _process_head(model: SimulationModel) -> dict:
    return {
        "id": model.key,
        "title": model.title,
        "description": model.summary,
        "version": model.runner_version or "unversioned",
        "jobControlOptions": ["async-execute"],
        "outputTransmission": ["reference"],
    }


def process_summary(model: SimulationModel, *, self_href: str) -> dict:
    return {**_process_head(model), "links": [link(self_href, "self", title="Process description")]}


def process_inputs(model: SimulationModel) -> dict:
    """One input per top-level ``parameter_schema`` property, plus the ``tosca_*`` inputs.

    A property is optional (``minOccurs: 0``) unless the schema requires it and
    the model has no default for it: defaults are filled in before validation.
    """
    schema = model.parameter_schema or {}
    required = set(schema.get("required") or [])
    defaults = model.default_parameters or {}
    inputs: dict = {}
    for name, prop in (schema.get("properties") or {}).items():
        if name.startswith("tosca_") or not isinstance(prop, dict):
            continue
        prop = dict(prop)
        if name in defaults:
            prop.setdefault("default", defaults[name])
        item = {"title": prop.get("title") or name, "schema": prop}
        if prop.get("description"):
            item["description"] = prop["description"]
        if name not in required or name in defaults:
            item["minOccurs"] = 0
        inputs[name] = item
    inputs[SEED] = {
        "title": "Seed",
        "description": "Random seed of the run.",
        "schema": {"type": "integer", "minimum": 0},
        "minOccurs": 0,
    }
    inputs[TITLE] = {
        "title": "Run title",
        "schema": {"type": "string", "maxLength": 255},
        "minOccurs": 0,
    }
    inputs[VISIBILITY] = {
        "title": "Run visibility",
        "description": "'organization' shares the run with the model's organization.",
        "schema": {"type": "string", "enum": list(VISIBILITIES), "default": "private"},
        "minOccurs": 0,
    }
    return inputs


def process_outputs(output_specs: list[dict]) -> dict:
    """Outputs by role, from what finished runs delivered: ``[{"role", "title", "mime"}]``."""
    return {
        spec["role"]: {
            "title": spec["title"],
            "schema": {"type": "string", "format": "uri", "contentMediaType": spec["mime"]},
        }
        for spec in output_specs
    }


def process_description(model: SimulationModel, *, self_href: str, execute_href: str, output_specs: list[dict]) -> dict:
    return {
        **_process_head(model),
        "inputs": process_inputs(model),
        "outputs": process_outputs(output_specs),
        "links": [
            link(self_href, "self", title="Process description"),
            link(execute_href, REL + "execute", title="Execute the process"),
        ],
    }


# -- execution ------------------------------------------------------------------


class ExecuteBodyError(Exception):
    """The execute request body cannot be used; the message is safe to show."""


@dataclass
class ExecuteRequest:
    parameters: dict = field(default_factory=dict)
    seed: int | None = None
    title: str = ""
    visibility: str = "private"
    response: str = "raw"  # result style the client asked for: "raw" | "document"


def _plain(name: str, value):
    """An input value: a plain value or ``{"value": ...}``; links are not supported."""
    if isinstance(value, dict):
        if "href" in value:
            raise ExecuteBodyError(f"Input '{name}': inputs by reference (href) are not supported.")
        if "value" in value:
            return value["value"]
    return value


def _input_value(name: str, value):
    if isinstance(value, list):
        return [_plain(name, item) for item in value]
    return _plain(name, value)


def parse_execute_body(body) -> ExecuteRequest:
    """The OGC execute document as a TOSCA run request; raises ``ExecuteBodyError``."""
    if not isinstance(body, dict):
        raise ExecuteBodyError("The request body must be a JSON object.")
    inputs = body.get("inputs", {})
    if not isinstance(inputs, dict):
        raise ExecuteBodyError("'inputs' must be a JSON object.")
    style = body.get("response", "raw")
    if style not in ("raw", "document"):
        raise ExecuteBodyError("'response' must be 'raw' or 'document'.")
    request = ExecuteRequest(response=style)
    for name, raw in inputs.items():
        value = _input_value(name, raw)
        if name == SEED:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ExecuteBodyError(f"Input '{SEED}' must be a non-negative integer.")
            request.seed = value
        elif name == TITLE:
            if not isinstance(value, str) or len(value) > 255:
                raise ExecuteBodyError(f"Input '{TITLE}' must be a string of at most 255 characters.")
            request.title = value
        elif name == VISIBILITY:
            if value not in VISIBILITIES:
                raise ExecuteBodyError(f"Input '{VISIBILITY}' must be one of: {', '.join(VISIBILITIES)}.")
            request.visibility = value
        elif name.startswith("tosca_"):
            raise ExecuteBodyError(f"Input '{name}' is reserved.")
        else:
            request.parameters[name] = value
    return request


# -- jobs -----------------------------------------------------------------------


def job_status(run: SimulationRun) -> str:
    return _STATE_STATUS[run.execution_state]


def _job_of(run: SimulationRun):
    try:
        return run.job
    except ObjectDoesNotExist:
        return None


def status_info(run: SimulationRun, *, job_href: str, results_href: str) -> dict:
    """``statusInfo`` of a run; ``run.model`` should be loaded. Never exposes the runner's error text."""
    status = job_status(run)
    job = _job_of(run)
    info = {
        "jobID": str(run.pk),
        "processID": run.model.key,
        "type": "process",
        "status": status,
        "created": run.created_at.isoformat(),
        "updated": run.updated_at.isoformat(),
        "links": [link(job_href, "self", title="Job status")],
    }
    message = "The simulation run failed." if status == "failed" else (job.message if job else "")
    if message:
        info["message"] = message
    if job and job.started_at:
        info["started"] = job.started_at.isoformat()
    if job and job.finished_at:
        info["finished"] = job.finished_at.isoformat()
    if status == "successful":
        info["links"].append(link(results_href, REL + "results", title="Job results"))
    return info


def results_href(base_href: str, style: str) -> str:
    """The results URL carrying the requested style; ``raw`` is the default and stays implicit."""
    return base_href + "?response=document" if style == "document" else base_href


def results_document(files: list[dict]) -> dict:
    """``response=document``: ``{role: {"href", "type", "expires"?}}`` from ``[{"role", "url", "mime", "expires_at"}]``."""
    document = {}
    for item in files:
        entry = {"href": item["url"], "type": item["mime"]}
        if item.get("expires_at"):
            entry["expires"] = item["expires_at"].isoformat()
        document[item["role"]] = entry
    return document


def results_link_header(files: list[dict]) -> str:
    """``response=raw``: one link-value per output, for a single ``Link`` header (RFC 8288)."""
    return ", ".join(
        f'<{item["url"]}>; rel="item"; type="{item["mime"]}"; title="{item["role"]}"' for item in files
    )
