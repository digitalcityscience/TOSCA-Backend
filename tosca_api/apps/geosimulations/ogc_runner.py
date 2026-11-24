"""Client for runners that speak OGC API – Processes, Part 1: Core 1.0 (OGC 18-062r2).

All HTTP to ``ogc-api-processes`` runners lives here; ``jobs.py`` decides what
happens to the job. Design: ``docs/development/geosimulations-ogc-api-processes-design.md``.

Nothing a runner answers is trusted. Every URL is checked before a request:
http(s) only, on the runner's ``base_url`` origin, or (package downloads only)
on one of its ``result_hosts``. The runner's Bearer token goes to the
``base_url`` origin only. Redirects are followed by hand and checked the same
way; bodies are read with a size cap. Error texts name no URL.

Every exchange has a wall-clock budget (``JSON_DEADLINE_SECONDS``, or the
download budget for packages): a runner that trickles bytes cannot hold a
worker, or the runner row locked during dispatch, longer than that plus one
socket read timeout.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from time import monotonic
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import requests
import urllib3
from django.conf import settings
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .packages import PackageLimits

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 30
JSON_DEADLINE_SECONDS = 30  # describe, execute, status, results document
DOWNLOAD_DEADLINE_SECONDS = 20 * 60  # capped below the worker's lock timeout, see _download_budget
CHUNK_BYTES = 64 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_REDIRECTS = 3
RESERVED_INPUTS = frozenset({"tosca_seed", "tosca_run", "tosca_upload"})
PACKAGE_MEDIA_TYPES = ("application/vnd.tosca.simulation-package+zip", "application/zip")
DEFAULT_PORTS = {"http": 80, "https": 443}
UNFETCHABLE = "The runner pointed to a link TOSCA does not fetch."


class RunnerError(Exception):
    """A definite problem with the runner's answer; the job (or the sync) fails."""


class TemporaryRunnerError(Exception):
    """Network error, timeout or an error status worth asking again later."""


class JobUnknown(Exception):
    """The runner answered ``404`` for the job's status."""


# -- URLs and requests ------------------------------------------------------------


def _origin(url: str) -> tuple[str, str, int | None]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "", "", None
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), port or DEFAULT_PORTS.get(scheme)


def on_runner_origin(runner, url: str) -> bool:
    """Whether ``url`` has the scheme, host and port of the runner's ``base_url``."""
    return _origin(url) == _origin(runner.base_url) and bool(_origin(url)[1])


def _join(base: str, reference: str) -> str:
    """``urljoin`` for links a runner sent; an unparsable one is a ``RunnerError``."""
    try:
        joined = urljoin(base, reference)
        urlsplit(joined).port  # noqa: B018 - raises ValueError for a bad port or IPv6 literal
    except ValueError:
        raise RunnerError(UNFETCHABLE) from None
    return joined


def on_result_host(runner, url: str, hosts) -> bool:
    """Whether ``url`` is on one of ``hosts`` (``host`` or ``host:port`` entries).

    An entry without a port allows only the default port of the URL's scheme.
    The scheme must be https unless the runner itself is reached over plain
    http (development).
    """
    scheme, host, port = _origin(url)
    if not host or (scheme != "https" and _origin(runner.base_url)[0] != "http"):
        return False
    for entry in hosts:
        if not isinstance(entry, str):
            continue
        name, sep, entry_port = entry.strip().lower().partition(":")
        if sep and not entry_port.isdigit():
            continue
        if name == host and port == (int(entry_port) if sep else DEFAULT_PORTS.get(scheme)):
            return True
    return False


def _check_url(runner, url: str, hosts) -> None:
    scheme, host, _ = _origin(url)
    try:
        username = urlsplit(url).username
    except ValueError:
        raise RunnerError(UNFETCHABLE) from None
    if (
        scheme not in ("http", "https")
        or not host
        or username is not None
        or any(char in url for char in "\\ \t\r\n")
    ):
        raise RunnerError(UNFETCHABLE)
    if not (on_runner_origin(runner, url) or on_result_host(runner, url, hosts)):
        logger.warning("Runner %s pointed to a host that is not allowed: %s", runner.pk, host)
        raise RunnerError("The runner pointed to a host that is not allowed for it.")


def _json_deadline() -> float:
    return monotonic() + JSON_DEADLINE_SECONDS


def _download_budget() -> float:
    """Seconds a package download may take: 20 minutes, at most two thirds of the worker's lock timeout."""
    lock_timeout = settings.GEOSIMULATIONS_WORKER["lock_timeout_seconds"]
    return min(DOWNLOAD_DEADLINE_SECONDS, lock_timeout * 2 / 3)


def _open(runner, url: str, *, deadline: float, method="GET", hosts=(), accept="application/json", body=None,
          headers=None):
    """Send one request (streamed); ``GET`` redirects are followed on the same allow-list.

    ``deadline`` (``time.monotonic()``) bounds the whole exchange, redirects included.
    """
    for _ in range(MAX_REDIRECTS + 1):
        _check_url(runner, url, hosts)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TemporaryRunnerError("The runner took too long to answer.")
        sent = {"Accept": accept, **(headers or {})}
        token = runner.get_token() if on_runner_origin(runner, url) else ""
        if token:
            sent["Authorization"] = f"Bearer {token}"
        try:
            response = requests.request(
                method,
                url,
                json=body,
                headers=sent,
                timeout=(min(CONNECT_TIMEOUT_SECONDS, remaining), min(READ_TIMEOUT_SECONDS, remaining)),
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            logger.warning("Runner %s: %s %s failed: %s", runner.pk, method, _without_query(url), exc.__class__.__name__)
            raise TemporaryRunnerError(f"Could not reach the runner ({exc.__class__.__name__}).") from None
        if method != "GET" or not response.is_redirect:
            return response
        location = response.headers.get("Location", "")
        response.close()
        url = _join(url, location)
    raise RunnerError("The runner redirected too often.")


def _without_query(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "[unparsable link]"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _expect_ok(response) -> None:
    if response.status_code == 200:
        return
    response.close()
    if response.status_code >= 500 or response.status_code in (408, 429):
        raise TemporaryRunnerError(f"The runner answered HTTP {response.status_code}.")
    raise RunnerError(f"The runner answered HTTP {response.status_code}.")


def _chunks(response, deadline: float, too_long: str):
    """The body in pieces of at most ``CHUNK_BYTES``, each returned as soon as it arrives.

    ``read1`` (not ``iter_content``, which waits for a full chunk) lets the
    deadline be checked after every piece, so a trickle cannot stretch it.
    """
    raw = response.raw
    try:
        while True:
            if monotonic() > deadline:
                raise TemporaryRunnerError(too_long)
            if isinstance(raw, urllib3.response.BaseHTTPResponse):
                chunk = raw.read1(CHUNK_BYTES, decode_content=True)
            else:  # file-like stand-ins
                chunk = raw.read(CHUNK_BYTES)
            if not chunk:
                return
            yield chunk
    except (requests.RequestException, urllib3.exceptions.HTTPError, OSError) as exc:
        raise TemporaryRunnerError(f"The runner's answer broke off ({exc.__class__.__name__}).") from None


def _read_json(response, deadline: float):
    body = bytearray()
    with response:
        for chunk in _chunks(response, deadline, "The runner took too long to answer."):
            body.extend(chunk)
            if len(body) > MAX_JSON_BYTES:
                raise RunnerError("The runner's answer is too large.")
    try:
        return json.loads(body)
    except ValueError:
        raise RunnerError("The runner did not answer with JSON.") from None


def _save(response, target: Path, limit: int, deadline: float) -> None:
    """Stream the body to ``target``, refusing more than ``limit`` bytes."""
    with response:
        declared = response.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > limit:
            raise RunnerError(f"The package is larger than {limit} bytes.")
        size = 0
        with target.open("wb") as handle:
            for chunk in _chunks(response, deadline, "Downloading the package took too long."):
                size += len(chunk)
                if size > limit:
                    raise RunnerError(f"The package is larger than {limit} bytes.")
                handle.write(chunk)


# -- processes --------------------------------------------------------------------


def describe_process(runner, process_id: str) -> dict | None:
    """``GET /processes/{id}``; ``None`` when the runner does not offer it."""
    deadline = _json_deadline()
    response = _open(runner, f"{runner.base_url}/processes/{quote(process_id, safe='')}", deadline=deadline)
    if response.status_code == 404:
        response.close()
        return None
    _expect_ok(response)
    description = _read_json(response, deadline)
    if not isinstance(description, dict):
        raise RunnerError("The process description is not a JSON object.")
    return description


def _by_id(items) -> dict:
    # Core 1.0 uses a map; some servers still send a list of {"id": ...}.
    if isinstance(items, list):
        return {item.get("id"): item for item in items if isinstance(item, dict)}
    return items if isinstance(items, dict) else {}


def _many(max_occurs) -> bool:
    return max_occurs == "unbounded" or (isinstance(max_occurs, int) and max_occurs > 1)


def _package_output(outputs: dict) -> str:
    for media_type in PACKAGE_MEDIA_TYPES:
        for name, spec in outputs.items():
            schema = spec.get("schema") if isinstance(spec, dict) else None
            if isinstance(schema, dict) and schema.get("contentMediaType") == media_type:
                return name
    if len(outputs) != 1:
        return ""
    # A single output is taken as the package unless it declares another media type.
    name, spec = next(iter(outputs.items()))
    schema = spec.get("schema") if isinstance(spec, dict) else None
    declared = schema.get("contentMediaType") if isinstance(schema, dict) else None
    return "" if declared else name


def model_fields(description: dict) -> dict:
    """``SimulationModel`` fields for a process description; raises ``RunnerError``.

    Each input becomes a property of ``parameter_schema``, required unless
    ``minOccurs: 0``, an array when ``maxOccurs > 1``. ``tosca_*`` inputs are
    TOSCA's own (seed, run, upload target) and stay out of the schema.
    """
    if "async-execute" not in (description.get("jobControlOptions") or []):
        raise RunnerError("The process does not offer asynchronous execution.")
    properties, required, defaults, declared = {}, [], {}, {}
    for name, spec in _by_id(description.get("inputs")).items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            raise RunnerError("The process has an invalid input description.")
        min_occurs, max_occurs = spec.get("minOccurs", 1), spec.get("maxOccurs", 1)
        if not (isinstance(min_occurs, int) and min_occurs >= 0) or not (
            max_occurs == "unbounded" or (isinstance(max_occurs, int) and max_occurs >= 1)
        ):
            raise RunnerError(f"Input {name!r} has invalid minOccurs / maxOccurs.")
        declared[name] = max_occurs
        if name.startswith("tosca_"):
            if name not in RESERVED_INPUTS:
                raise RunnerError(f"Input {name!r} uses the reserved tosca_ prefix.")
            continue
        schema = spec.get("schema") if isinstance(spec.get("schema"), dict) else {}
        if _many(max_occurs):
            prop = {"type": "array", "items": schema}
            if min_occurs:
                prop["minItems"] = min_occurs
            if isinstance(max_occurs, int):
                prop["maxItems"] = max_occurs
        else:
            prop = dict(schema)
            if "default" in schema:
                defaults[name] = schema["default"]
        for key in ("title", "description"):
            if isinstance(spec.get(key), str) and key not in prop:
                prop[key] = spec[key]
        properties[name] = prop
        if min_occurs:
            required.append(name)
    parameter_schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        parameter_schema["required"] = required
    try:
        Draft202012Validator.check_schema(parameter_schema)
    except SchemaError as exc:
        raise RunnerError(f"The process inputs are not a valid JSON Schema: {exc.message}") from None

    push = "tosca_upload" in declared
    package_output = _package_output(_by_id(description.get("outputs")))
    if not push and not package_output:
        raise RunnerError("The process has no tosca_upload input and no recognisable package output.")
    return {
        "parameter_schema": parameter_schema,
        "default_parameters": defaults,
        "runner_version": str(description.get("version") or "")[:50],
        "transfer_mode": "push" if push else "pull",
        "accepts_seed": "tosca_seed" in declared,
        "package_output": package_output[:100],
        "package_transmission": "reference" if "reference" in (description.get("outputTransmission") or []) else "value",
        "process_inputs": declared,
    }


# -- execution ----------------------------------------------------------------------


def _qualified(value):
    # A bare object would read as a qualified value or a link, a bare array as
    # several values of one input.
    return {"value": value} if isinstance(value, (dict, list)) else value


def execute_request(job, *, upload: dict | None = None) -> dict:
    """The body of ``POST /processes/{id}/execution`` for a run job.

    ``upload`` (push) is the presigned PUT target; it is sent as
    ``tosca_upload: {"value": {"url", ...}}``, never as a top-level ``href``,
    which a server would try to ``GET``.
    """
    run = job.run
    model = run.model
    declared = model.process_inputs or {}
    inputs = {}
    for name, value in run.parameters.items():
        if name.startswith("tosca_"):
            continue
        if isinstance(value, list) and _many(declared.get(name, 1)):
            inputs[name] = [_qualified(item) for item in value]
        else:
            inputs[name] = _qualified(value)
    if "tosca_seed" in declared and run.seed is not None:
        inputs["tosca_seed"] = run.seed
    if "tosca_run" in declared:
        inputs["tosca_run"] = {"value": {"key": run.key, "title": run.title, "job_id": str(job.pk)}}
    if "tosca_upload" in declared and upload is not None:
        inputs["tosca_upload"] = {"value": upload}
    body = {"inputs": inputs, "response": "document"}
    if model.transfer_mode == "pull" and model.package_output:
        body["outputs"] = {model.package_output: {"transmissionMode": model.package_transmission}}
        if model.package_transmission == "value":
            # A by-value package comes back as the bytes themselves, streamed
            # into storage, instead of base64 inside a JSON document.
            body["response"] = "raw"
    return body


def execute(job, body: dict) -> tuple[str, str]:
    """Start the job asynchronously; returns its status URL and the runner's job id.

    ``TemporaryRunnerError`` when the runner could not be reached in time or
    answered ``408``, ``429`` or ``5xx`` (nothing ran, so it can be sent
    again); ``RunnerError`` when it refused the job for good (other ``4xx``) or
    answered in a way that may have run the process, which must not be retried.
    """
    runner = job.runner
    url = f"{runner.base_url}/processes/{quote(job.run.model.key, safe='')}/execution"
    deadline = _json_deadline()
    response = _open(runner, url, deadline=deadline, method="POST", body=body, headers={"Prefer": "respond-async"})
    status = response.status_code
    if status == 200:
        response.close()
        raise RunnerError("The runner ran the process synchronously instead of accepting an asynchronous job.")
    if not 200 <= status < 300:
        response.close()
        if 400 <= status < 500 and status not in (408, 429):
            raise RunnerError(f"The runner refused the job (HTTP {status}).")
        raise TemporaryRunnerError(f"The runner answered HTTP {status}.")
    location = response.headers.get("Location", "")
    if status != 201 or not location:
        response.close()
        raise RunnerError(f"The runner answered HTTP {status} without a job location.")
    try:
        status_url = _join(runner.base_url + "/", location)
    except RunnerError:
        response.close()
        raise RunnerError("The runner's job location is not a valid link.") from None
    if not on_runner_origin(runner, status_url):
        response.close()
        logger.warning("Runner %s returned a job location off its origin: %s", runner.pk, _without_query(status_url))
        raise RunnerError("The runner's job location is not on the runner's own address.")
    # The job exists at the runner now; a broken or slow body must not make
    # TOSCA send it again, so the id falls back to the location's last segment.
    try:
        info = _read_json(response, deadline)
    except (RunnerError, TemporaryRunnerError) as exc:
        logger.warning(
            "Runner %s: could not read the execute answer for %s (%s); using its location",
            runner.pk,
            _without_query(status_url),
            exc,
        )
        info = {}
    job_id = info.get("jobID") if isinstance(info, dict) else None
    if not isinstance(job_id, str) or not job_id:
        job_id = urlsplit(status_url).path.rstrip("/").rsplit("/", 1)[-1]
    return status_url[:1000], job_id[:255]


def job_status(job) -> dict:
    """The job's ``statusInfo`` (``GET`` its status URL); ``{}`` when the answer is no object.

    Raises ``JobUnknown`` on ``404`` and ``TemporaryRunnerError`` on other errors.
    """
    deadline = _json_deadline()
    response = _open(job.runner, job.status_url, deadline=deadline)
    if response.status_code == 404:
        response.close()
        raise JobUnknown
    if response.status_code != 200:
        response.close()
        raise TemporaryRunnerError(f"The runner answered HTTP {response.status_code}.")
    try:
        info = _read_json(response, deadline)
    except RunnerError as exc:
        raise TemporaryRunnerError(str(exc)) from None
    return info if isinstance(info, dict) else {}


def _results_url(status_url: str) -> str:
    try:
        parts = urlsplit(status_url)
    except ValueError:
        raise RunnerError(UNFETCHABLE) from None
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/results", "", ""))


def fetch_package(job, target: Path) -> None:
    """Download a successful pull job's package to ``target``.

    By value the bytes come from ``/results`` (``raw``); by reference
    ``/results`` names an ``href`` on the runner's origin or a result host.
    A refused href (expired signature) is asked for again once. The whole
    transfer has one download budget; each results document also has the
    JSON budget.
    """
    runner, model = job.runner, job.run.model
    limit = PackageLimits.from_settings().max_package_bytes
    results = _results_url(job.status_url)
    deadline = monotonic() + _download_budget()
    if model.package_transmission != "reference":
        response = _open(runner, results, deadline=deadline, accept="*/*")
        _expect_ok(response)
        if "json" in response.headers.get("Content-Type", ""):
            response.close()
            raise RunnerError("The runner sent a JSON document instead of the package.")
        _save(response, target, limit, deadline)
        return
    for attempt in range(2):
        document_deadline = min(deadline, _json_deadline())
        response = _open(runner, results, deadline=document_deadline)
        _expect_ok(response)
        document = _read_json(response, document_deadline)
        output = document.get(model.package_output) if isinstance(document, dict) else None
        href = output.get("href") if isinstance(output, dict) else None
        if not isinstance(href, str) or not href:
            raise RunnerError("The runner's results do not link the package.")
        response = _open(runner, _join(results, href), deadline=deadline, hosts=runner.result_hosts or (), accept="*/*")
        if response.status_code in (401, 403) and attempt == 0:
            response.close()
            continue
        _expect_ok(response)
        _save(response, target, limit, deadline)
        return
