"""The bundled enhancement must remain usable across installs and preserve timing."""

import json
from contextlib import closing
from pathlib import Path

import pytest

from calliope.agent.video_agent import _get_workflow
from calliope.comfyui.builtin import SEEDVR2_NAME, SEEDVR2_PATH, install_builtin_workflows
from calliope.comfyui.patcher import patch_workflow
from calliope.config import settings
from calliope.db import get_db
from calliope.enhancement import enqueue_enhancements
from calliope.queue.manager import queue_manager


def test_preset_appears_on_startup_and_preserves_user_edits(client):
    preset = next(w for w in client.get("/api/workflows").json() if w["name"] == SEEDVR2_NAME)
    assert preset["is_enabled"]
    assert preset["kind"] == "video"
    assert preset["purpose"] == "enhancement"
    assert any(i["role"] == "video" for i in preset["input_schema"])
    assert preset["output_schema"][0]["kind"] == "video"
    assert (
        client.patch(
            f"/api/workflows/{preset['id']}",
            json={"is_enabled": False, "description": "My changes"},
        ).status_code
        == 200
    )
    with closing(get_db(settings.db_path)) as conn:
        install_builtin_workflows(conn)
    workflows = client.get("/api/workflows").json()
    assert len([w for w in workflows if w["name"] == SEEDVR2_NAME]) == 1
    updated = next(w for w in workflows if w["id"] == preset["id"])
    assert not updated["is_enabled"]
    assert updated["description"] == "My changes"


def test_seedvr2_receives_original_and_keeps_audio_fps(client, tmp_path):
    previous = queue_manager.paused
    queue_manager.paused = True
    try:
        preset = next(w for w in client.get("/api/workflows").json() if w["name"] == SEEDVR2_NAME)
        project = client.post("/api/projects", json={"title": "SeedVR2 QA"}).json()
        scene = client.post(f"/api/projects/{project['id']}/scenes", json={"order_index": 1}).json()
        clip = scene["clips"][0]
        original = tmp_path / "original.mp4"
        original.write_bytes(b"test clip")
        assert (
            client.patch(
                f"/api/projects/{project['id']}/clips/{clip['id']}",
                json={"clip_path": str(original)},
            ).status_code
            == 200
        )
        jobs, _ = enqueue_enhancements(
            project["id"],
            preset["id"],
            clip_ids=[clip["id"]],
            input_values={"1": "wrong-source.mp4", "5": 1080, "6": 9},
        )
        payload = json.loads(jobs[0]["payload_json"])
        graph = patch_workflow(preset["workflow_json"], payload["input_values"])
        assert graph["1"]["inputs"]["file"] == str(original)
        assert graph["5"]["inputs"]["value"] == 1080
        assert graph["6"]["inputs"]["value"] == 9
        assert graph["8"]["inputs"]["audio"] == ["2", 1]
        assert graph["8"]["inputs"]["fps"] == ["2", 2]
        assert payload["output_node_id"] == "9"
    finally:
        queue_manager.paused = previous


def test_importable_example_matches_bundled_preset():
    example = Path(__file__).resolve().parents[2] / "example_ComfyUI_workflows" / SEEDVR2_PATH.name
    assert json.loads(example.read_text(encoding="utf-8")) == json.loads(
        SEEDVR2_PATH.read_text(encoding="utf-8")
    )


def test_enhancement_is_not_selected_for_video_generation(client):
    assert _get_workflow() is None
    generator = client.post(
        "/api/workflows", json={"name": "Generator", "kind": "video", "workflow_json": {}}
    ).json()
    assert _get_workflow()["id"] == generator["id"]
    preset = next(w for w in client.get("/api/workflows").json() if w["name"] == SEEDVR2_NAME)
    with pytest.raises(ValueError, match="Use Video"):
        _get_workflow(preset["id"])
