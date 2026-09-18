"""Read-only matrix metadata normalization for RunBoard.

The detector identifies the currently running research cell.  This module
adds the surrounding matrix/job context without treating a cell round as the
progress of the whole job.  It consumes marked JSON emitted by the server
provider so the parser remains testable without SSH or a live server.
"""

from dataclasses import dataclass, field
import json
from pathlib import PurePosixPath
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


RELIABLE = "RELIABLE"
INFERRED = "INFERRED"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MatrixMetadata:
    job_name: Optional[str] = None
    matrix_completed: Optional[int] = None
    matrix_total: Optional[int] = None
    current_cell: Optional[int] = None
    method: Optional[str] = None
    matrix_version: Optional[str] = None
    field_sources: Dict[str, str] = field(default_factory=dict)

    @property
    def matrix_progress_percent(self) -> Optional[float]:
        if self.matrix_completed is None or self.matrix_total in (None, 0):
            return None
        return 100.0 * self.matrix_completed / self.matrix_total


def matrix_root_from_cell(cell_root: str) -> Optional[str]:
    """Return ``.../matrix`` for a cell rooted at ``.../matrix/cells/name``."""
    path = PurePosixPath(cell_root)
    if path.parent.name != "cells":
        return None
    return str(path.parent.parent)


def _text(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _integer(value: Any) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _cell_value(cell: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in cell and cell[key] is not None:
            return cell[key]
    return None


def _cell_identity(cell: Dict[str, Any]) -> Optional[str]:
    return _text(_cell_value(cell, "run_id", "runId", "cell_id", "cellId", "id", "name"))


def _cell_index(cell: Dict[str, Any]) -> Optional[int]:
    return _integer(_cell_value(cell, "index", "cell_index", "cellIndex"))


def _plan_version(plan: Dict[str, Any]) -> Optional[str]:
    return _text(_cell_value(
        plan,
        "matrix_version", "matrixVersion", "version",
        "parent_matrix_version", "parentMatrixVersion",
    ))


def _completion_identity(value: Dict[str, Any]) -> Optional[str]:
    identity = _text(_cell_value(value, "run_id", "runId", "cell_id", "cellId", "id"))
    if identity:
        return "id:" + identity
    index = _integer(_cell_value(value, "index", "cell_index", "cellIndex"))
    return "index:" + str(index) if index is not None else None


def _completion_is_success(value: Dict[str, Any]) -> bool:
    status = _text(value.get("status"))
    return bool(status and status.lower().startswith("completed"))


def _safe_matrix_version(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return safe[:64] or None


def _job_name(plan: Dict[str, Any], matrix_version: Optional[str], overrides: Dict[str, str]) -> Tuple[Optional[str], str]:
    explicit = _text(_cell_value(plan, "jobName", "job_name"))
    if explicit:
        return explicit, RELIABLE
    if matrix_version and _text(overrides.get(matrix_version)):
        return _text(overrides[matrix_version]), RELIABLE
    fallback = _safe_matrix_version(matrix_version)
    if fallback:
        return fallback, INFERRED
    return None, UNKNOWN


def _events(text: str) -> Iterable[Tuple[str, int, str, Dict[str, Any]]]:
    """Yield marker, root id, source token and JSON object for each block."""
    marker: Optional[str] = None
    root_id: Optional[int] = None
    source = "default"
    buffer: List[str] = []

    def flush() -> Optional[Tuple[str, int, str, Dict[str, Any]]]:
        if marker is None or root_id is None:
            return None
        content = "\n".join(buffer).strip()
        if not content:
            return None
        try:
            value = json.loads(content)
        except (TypeError, ValueError):
            return None
        return marker, root_id, source, value if isinstance(value, dict) else {}

    for line in text.splitlines():
        fields = line.split()
        if len(fields) in {2, 3} and fields[0] in {"__RUNBOARD_MATRIX_PLAN__", "__RUNBOARD_MATRIX_COMPLETION__"}:
            event = flush()
            if event:
                yield event
            marker = fields[0]
            try:
                root_id = int(fields[1])
            except ValueError:
                marker = None
                root_id = None
            source = fields[2] if len(fields) == 3 else "default"
            buffer = []
        elif marker is not None:
            buffer.append(line)
    event = flush()
    if event:
        yield event


def parse_matrix_observations(text: str, active_run_ids: Dict[int, str],
                              display_name_overrides: Optional[Dict[str, str]] = None) -> Dict[int, MatrixMetadata]:
    """Normalize marked plan/completion JSON keyed by detector root PID.

    A matrix total is taken only from the explicit plan cell list.  Completed
    count is taken only from successful completion-manifest observations.
    Current-cell identity is joined by the active cell run id discovered from
    the process working directory.  Missing joins remain unknown.
    """
    overrides = display_name_overrides or {}
    plans: Dict[int, Dict[str, Dict[str, Any]]] = {}
    completions: Dict[int, Dict[str, List[Dict[str, Any]]]] = {}
    for marker, root_id, source, value in _events(text):
        if marker == "__RUNBOARD_MATRIX_PLAN__":
            plans.setdefault(root_id, {})[source] = value
        else:
            completions.setdefault(root_id, {}).setdefault(source, []).append(value)

    result: Dict[int, MatrixMetadata] = {}
    for root_id, candidates in plans.items():
        active_run_id = active_run_ids.get(root_id)

        def candidate_score(item: Tuple[str, Dict[str, Any]]) -> Tuple[bool, int]:
            _source, candidate = item
            raw_cells = candidate.get("cells")
            raw_cells = raw_cells if isinstance(raw_cells, list) else candidate.get("planned_cells")
            cells = raw_cells if isinstance(raw_cells, list) else []
            contains_active = any(
                isinstance(cell, dict) and active_run_id and _cell_identity(cell) == active_run_id
                for cell in cells
            )
            return contains_active, len(cells)

        source, plan = max(candidates.items(), key=candidate_score)
        raw_cells = plan.get("cells")
        cells = raw_cells if isinstance(raw_cells, list) else plan.get("planned_cells")
        cells = cells if isinstance(cells, list) else []
        matrix_total = len(cells) if cells else _integer(plan.get("matrix_total"))
        matrix_version = _plan_version(plan)
        job_name, job_source = _job_name(plan, matrix_version, overrides)
        current_cell: Optional[int] = None
        method: Optional[str] = None
        cell_source = UNKNOWN
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            if active_run_id and _cell_identity(cell) == active_run_id:
                current_cell = _cell_index(cell)
                method = _text(_cell_value(cell, "method_name", "methodName", "method", "method_id", "methodId"))
                cell_source = RELIABLE
                break

        selected_ids = {
            _cell_identity(cell) for cell in cells
            if isinstance(cell, dict) and _cell_identity(cell)
        }
        selected_version = _plan_version(plan)
        manifest_candidates: List[Dict[str, Any]] = []
        for candidate_source, candidate in candidates.items():
            candidate_version = _plan_version(candidate)
            if selected_version and candidate_version and candidate_version != selected_version:
                continue
            raw_candidate_cells = candidate.get("cells")
            raw_candidate_cells = (
                raw_candidate_cells if isinstance(raw_candidate_cells, list)
                else candidate.get("planned_cells")
            )
            candidate_cells = raw_candidate_cells if isinstance(raw_candidate_cells, list) else []
            candidate_ids = {
                _cell_identity(cell) for cell in candidate_cells
                if isinstance(cell, dict) and _cell_identity(cell)
            }
            if candidate_source != source and selected_ids and not selected_ids.intersection(candidate_ids):
                continue
            manifest_candidates.extend(completions.get(root_id, {}).get(candidate_source, []))

        seen: set[str] = set()
        completed = 0
        for ordinal, manifest in enumerate(manifest_candidates):
            if not _completion_is_success(manifest):
                continue
            identity = _completion_identity(manifest) or "manifest:" + str(ordinal)
            if identity in seen:
                continue
            seen.add(identity)
            completed += 1

        sources = {
            "job_name": job_source,
            "matrix_total": RELIABLE if cells or matrix_total is not None else UNKNOWN,
            "matrix_completed": RELIABLE if completed else UNKNOWN,
            "current_cell": cell_source,
            "method": cell_source,
            "matrix_version": RELIABLE if matrix_version else UNKNOWN,
        }
        result[root_id] = MatrixMetadata(
            job_name=job_name,
            matrix_completed=completed,
            matrix_total=matrix_total,
            current_cell=current_cell,
            method=method,
            matrix_version=matrix_version,
            field_sources=sources,
        )
    return result
