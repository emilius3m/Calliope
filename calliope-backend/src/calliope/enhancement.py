"""Post-generation video processing, independent of screenplay/render settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from calliope.comfyui.parser import parse_dynamic_inputs, parse_dynamic_outputs
from calliope.comfyui.roles import input_has_role
from calliope.config import settings
from calliope.db import get_db


def enhancement_current(clip: dict[str, Any]) -> bool:
    return bool(
        clip.get("enhanced_path")
        and clip.get("clip_path")
        and clip.get("enhancement_source_path") == clip.get("clip_path")
    )


def film_clip_path(clip: dict[str, Any]) -> str | None:
    if clip.get("use_enhanced") and enhancement_current(clip):
        return clip["enhanced_path"]
    return clip.get("clip_path")


MIN_TRIM_SEC = 0.5


def trim_current(clip: dict[str, Any]) -> tuple[float, float] | None:
    """(start, end) in seconds while the trim still belongs to the current original.

    A re-render replaces clip_path, which retires the trim without touching it.
    The enhanced version shares the original's timeline, so the same range applies.
    """
    start, end = clip.get("trim_start"), clip.get("trim_end")
    if start is None or end is None or not clip.get("clip_path"):
        return None
    if clip.get("trim_source_path") != clip.get("clip_path"):
        return None
    return float(start), float(end)


def trim_columns(clip: dict[str, Any], start: float | None, end: float | None) -> dict[str, Any]:
    """Column values that store a trim of the clip's current original; both None clears it."""
    if start is None and end is None:
        return {"trim_start": None, "trim_end": None, "trim_source_path": None}
    if not clip.get("clip_path"):
        raise ValueError("Generate this clip before trimming it")
    start = float(start or 0)
    if end is None or start < 0 or float(end) - start < MIN_TRIM_SEC:
        raise ValueError(f"A trim needs 0 <= start and at least {MIN_TRIM_SEC} s before its end")
    return {
        "trim_start": round(start, 3),
        "trim_end": round(float(end), 3),
        "trim_source_path": clip["clip_path"],
    }


def enqueue_enhancements(
    project_id: int,
    workflow_id: int,
    *,
    clip_ids: list[int] | None = None,
    input_values: dict[str, Any] | None = None,
    output_node_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Snapshot each original source and atomically enqueue a shot or a batch.

    The source is assigned by the server, never the preceding clip or a stale
    video slot from the generation form. No LLM calls or generation drafts.
    """
    if clip_ids is not None and not clip_ids:
        raise ValueError("Select at least one clip")
    conn = get_db(settings.db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone():
            raise ValueError("Project not found")
        workflow = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        if not workflow or not workflow["is_enabled"] or workflow["kind"] != "video":
            raise ValueError("Choose an enabled video workflow")
        graph = json.loads(workflow["workflow_json"])
        inputs = parse_dynamic_inputs(graph)
        video_input = next((i for i in inputs if input_has_role(i, "video")), None)
        if not video_input:
            raise ValueError("Enhancement workflow needs an (Input:video) node")
        outputs = [o for o in parse_dynamic_outputs(graph) if o["kind"] == "video"]
        if not outputs:
            raise ValueError("Enhancement workflow needs an (Output:video) node")
        chosen_output = output_node_id or outputs[-1]["nodeId"]
        if chosen_output not in {o["nodeId"] for o in outputs}:
            raise ValueError("Choose a video output from this workflow")
        # Remove the automatically assigned source from the independent setup.
        values = {
            str(k): v
            for k, v in (input_values or {}).items()
            if str(k) != video_input["nodeId"] and v is not None
        }
        setup = {
            "workflow_id": workflow_id,
            "input_values": values,
            "output_node_id": chosen_output,
        }
        rows = conn.execute(
            "SELECT c.*, s.order_index AS scene_order_index FROM clips c "
            "JOIN scenes s ON s.id = c.scene_id WHERE c.project_id = ? "
            "ORDER BY s.order_index, c.order_index, c.id",
            (project_id,),
        ).fetchall()
        if clip_ids is not None:
            selected = set(clip_ids)
            if selected - {r["id"] for r in rows}:
                raise ValueError("Clip not found in this project")
            rows = [r for r in rows if r["id"] in selected]
        jobs: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in rows:
            source = row["clip_path"]
            reason = None
            if not source or not Path(source).is_file():
                reason = "Original video is missing — generate this clip first"
            elif conn.execute(
                "SELECT id FROM jobs WHERE clip_id = ? AND kind IN ('video', 'enhancement') "
                "AND status IN ('pending', 'running')",
                (row["id"],),
            ).fetchone():
                reason = "This clip is already being processed"
            if reason:
                if clip_ids is not None:
                    raise ValueError(reason)
                skipped.append({"clip_id": row["id"], "reason": reason})
                continue
            resolved = {**values, video_input["nodeId"]: source}
            prompt = next(
                (str(values.get(i["nodeId"], "")) for i in inputs if input_has_role(i, "prompt")),
                "",
            )
            payload = {
                "input_values": resolved,
                "source_path": source,
                "output_node_id": chosen_output,
                "prompt": prompt,
            }
            cur = conn.execute(
                "INSERT INTO jobs (project_id, scene_id, clip_id, kind, workflow_id, "
                "status, payload_json) VALUES (?, ?, ?, 'enhancement', ?, 'pending', ?)",
                (project_id, row["scene_id"], row["id"], workflow_id, json.dumps(payload)),
            )
            job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (cur.lastrowid,)).fetchone()
            jobs.append(dict(job_row))
            conn.execute(
                "UPDATE clips SET enhancement_settings_json = ? WHERE id = ?",
                (json.dumps(setup), row["id"]),
            )
        if not jobs:
            raise ValueError("No rendered clips available for enhancement")
        conn.commit()
        return jobs, skipped
    finally:
        conn.close()
