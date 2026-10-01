"""Starter workflows shipped with Calliope, independent of installation data."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from calliope.comfyui.parser import parse_dynamic_inputs, parse_dynamic_outputs

SEEDVR2_NAME = "SeedVR2 Video Upscale — 8GB"
SEEDVR2_PATH = (
    Path(__file__).resolve().parent.parent / "presets" / "SeedVR2_Video_Upscale_8GB_API.json"
)


def install_builtin_workflows(conn: sqlite3.Connection) -> None:
    """Add the starter preset once; preserve user edits and disabled workflows."""
    if conn.execute(
        "SELECT 1 FROM workflows WHERE name = ? AND kind = 'video'", (SEEDVR2_NAME,)
    ).fetchone():
        return
    graph = json.loads(SEEDVR2_PATH.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO workflows "
        "(name, kind, workflow_json, input_schema, output_schema, description) "
        "VALUES (?, 'video', ?, ?, ?, ?)",
        (
            SEEDVR2_NAME,
            json.dumps(graph),
            json.dumps(parse_dynamic_inputs(graph)),
            json.dumps(parse_dynamic_outputs(graph)),
            "Enhance existing clips with SeedVR2 3B Q4, CPU offload and VAE tiling. "
            "Preserves original audio and frame rate. Requires ComfyUI-SeedVR2_VideoUpscaler.",
        ),
    )
    conn.commit()
