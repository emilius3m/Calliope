"""Post-processing must preserve originals, source snapshots and film choices."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from calliope.comfyui.client import ComfyUIClient
from calliope.config import settings
from calliope.db import get_db, migrate_db, rebase_stale_asset_paths
from calliope.enhancement import enqueue_enhancements
from calliope.events.bus import event_bus
from calliope.export.runner import collect_clips, run_export
from calliope.project_transfer import export_project, import_project
from calliope.queue.manager import queue_manager
from calliope.queue.worker import QueueWorker
from calliope.routers.canvas import _project_path_referenced

GRAPH = {
    "1": {
        "class_type": "VHS_LoadVideo",
        "inputs": {"video": ""},
        "_meta": {"title": "Original (Input:video)"},
    },
    "2": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": ""},
        "_meta": {"title": "Enhance prompt (Input:prompt)"},
    },
    "3": {
        "class_type": "VHS_VideoCombine",
        "inputs": {},
        "_meta": {"title": "Intermediate (Output:video)"},
    },
    "9": {
        "class_type": "VHS_VideoCombine",
        "inputs": {},
        "_meta": {"title": "Restored & Upscaled (Output:video)"},
    },
}


@pytest.fixture()
def setup(client, tmp_path):
    was_paused = queue_manager.paused
    queue_manager.paused = True
    pid = client.post("/api/projects", json={"title": "Enhancement QA"}).json()["id"]
    scene = client.post(f"/api/projects/{pid}/scenes", json={"order_index": 1}).json()
    clip = scene["clips"][0]
    original = tmp_path / "original.mp4"
    original.write_bytes(b"original bytes")
    generated = {"prompt_draft": "ORIGINAL H3 PROMPT", "input_values": {"20": "original ref"}}
    client.patch(
        f"/api/projects/{pid}/clips/{clip['id']}",
        json={
            "clip_path": str(original),
            "workflow_id": 71,
            "video_settings": generated,
        },
    )
    wid = client.post(
        "/api/workflows",
        json={
            "name": "Restore Enhance Upscale",
            "kind": "video",
            "workflow_json": GRAPH,
        },
    ).json()["id"]
    yield client, pid, clip["id"], wid, original, generated
    queue_manager.paused = was_paused


def read_clip(pid, cid):
    conn = get_db(settings.db_path)
    try:
        return dict(
            conn.execute(
                "SELECT * FROM clips WHERE id = ? AND project_id = ?", (cid, pid)
            ).fetchone()
        )
    finally:
        conn.close()


def complete(job, output):
    QueueWorker()._apply_outputs_to_entities(job, json.loads(job["payload_json"]), [str(output)])
    queue_manager.mark_done(job["id"], [str(output)])


def test_enhancement_is_independent_and_source_is_automatic(setup):
    client, pid, cid, wid, original, generated = setup
    response = client.post(
        f"/api/jobs/projects/{pid}/enhance-videos",
        json={
            "workflow_id": wid,
            "clip_ids": [cid],
            "input_values": {"1": "wrong-source.mp4", "2": "restore"},
        },
    )
    assert response.status_code == 200
    job = response.json()["jobs"][0]
    assert job["kind"] == "enhancement"
    assert job["payload"]["input_values"] == {"1": str(original), "2": "restore"}
    assert job["payload"]["output_node_id"] == "9"
    clip = read_clip(pid, cid)
    assert clip["clip_path"] == str(original)
    assert clip["workflow_id"] == 71
    assert json.loads(clip["video_settings_json"]) == generated
    assert original.read_bytes() == b"original bytes"


def test_result_and_film_version_preserve_generation(setup, tmp_path):
    client, pid, cid, wid, original, generated = setup
    jobs, _ = enqueue_enhancements(pid, wid, clip_ids=[cid])
    enhanced = tmp_path / "enhanced.mp4"
    enhanced.write_bytes(b"enhanced")
    complete(jobs[0], enhanced)
    clip = read_clip(pid, cid)
    assert clip["clip_path"] == str(original)
    assert json.loads(clip["video_settings_json"]) == generated
    assert clip["use_enhanced"] == 1
    assert collect_clips(pid)[0][0]["clip_path"] == str(enhanced)
    r = client.patch(f"/api/projects/{pid}/clips/{cid}", json={"use_enhanced": False})
    assert r.json()["film_path"] == str(original)
    assert collect_clips(pid)[0][0]["clip_path"] == str(original)
    r = client.patch(f"/api/projects/{pid}/clips/{cid}", json={"use_enhanced": True})
    assert r.json()["film_path"] == str(enhanced)


def test_new_original_invalidates_old_enhancement_and_late_results(setup, tmp_path):
    client, pid, cid, wid, original, _ = setup
    jobs, _ = enqueue_enhancements(pid, wid, clip_ids=[cid])
    replacement = tmp_path / "replacement.mp4"
    replacement.write_bytes(b"replacement")
    client.patch(f"/api/projects/{pid}/clips/{cid}", json={"clip_path": str(replacement)})
    complete(jobs[0], tmp_path / "old-source-enhancement.mp4")
    clip = read_clip(pid, cid)
    assert clip["use_enhanced"] == 0
    assert clip["enhancement_source_path"] == str(original)
    assert collect_clips(pid)[0][0]["clip_path"] == str(replacement)
    assert (
        client.patch(f"/api/projects/{pid}/clips/{cid}", json={"use_enhanced": True}).status_code
        == 400
    )


def test_batch_snapshots_each_clip_and_skips_missing_or_busy(setup, tmp_path):
    client, pid, cid, wid, original, _ = setup
    scene = client.post(f"/api/projects/{pid}/scenes", json={"order_index": 2}).json()
    second = scene["clips"][0]["id"]
    source = tmp_path / "second.mp4"
    source.write_bytes(b"second")
    client.patch(
        f"/api/projects/{pid}/clips/{second}",
        json={"clip_path": str(source), "chain_from_prev": True},
    )
    missing = client.post(f"/api/projects/{pid}/scenes", json={"order_index": 3}).json()["clips"][
        0
    ]["id"]
    jobs, skipped = enqueue_enhancements(pid, wid)
    assert [json.loads(j["payload_json"])["source_path"] for j in jobs] == [
        str(original),
        str(source),
    ]
    assert [j["clip_id"] for j in jobs] == [cid, second]
    assert skipped[0]["clip_id"] == missing
    with pytest.raises(ValueError, match="already being processed"):
        enqueue_enhancements(pid, wid, clip_ids=[cid])


def test_invalid_selection_rolls_back_entire_batch(setup, tmp_path):
    client, pid, cid, wid, _, _ = setup
    missing = client.post(f"/api/projects/{pid}/scenes", json={"order_index": 2}).json()["clips"][
        0
    ]["id"]
    with pytest.raises(ValueError, match="missing"):
        enqueue_enhancements(pid, wid, clip_ids=[cid, missing])
    assert not queue_manager.list_jobs(pid)
    for payload in ({"clip_ids": []}, {"clip_ids": [999]}, {"output_node_id": "100"}):
        assert (
            client.post(
                f"/api/jobs/projects/{pid}/enhance-videos", json={"workflow_id": wid, **payload}
            ).status_code
            == 400
        )


def test_worker_keeps_selected_video_not_intermediate_or_thumbnail(setup, tmp_path, monkeypatch):
    _, pid, cid, wid, _, _ = setup
    jobs, _ = enqueue_enhancements(pid, wid, clip_ids=[cid], output_node_id="9")
    old_dry = settings.dry_run
    settings.dry_run = False

    async def health(self):
        return True

    async def prepare(self, graph):
        return graph

    async def queue(self, graph):
        return "prompt"

    async def poll(self, *args):
        return {
            "status": {"completed": True},
            "outputs": {
                "3": {"gifs": [{"filename": "intermediate.mp4"}]},
                "9": {
                    "images": [{"filename": "metadata.png"}],
                    "gifs": [{"filename": "final.mp4"}],
                },
                "10": {"gifs": [{"filename": "comparison.mp4"}]},
            },
        }

    async def download(self, filename, *, dest, **kwargs):
        Path(dest).write_bytes(b"processed")

    monkeypatch.setattr(ComfyUIClient, "health", health)
    monkeypatch.setattr(ComfyUIClient, "prepare_media_inputs", prepare)
    monkeypatch.setattr(ComfyUIClient, "queue_prompt", queue)
    monkeypatch.setattr(ComfyUIClient, "download_image", download)
    monkeypatch.setattr(QueueWorker, "_poll_history", poll)
    try:
        outputs = asyncio.run(QueueWorker()._run_job(jobs[0]))
        assert len(outputs) == 1 and outputs[0].endswith("final.mp4")
        assert read_clip(pid, cid)["enhanced_path"] == outputs[0]
    finally:
        settings.dry_run = old_dry


def test_dry_run_is_video_and_export_records_chosen_sources(setup, tmp_path):
    _, pid, cid, wid, original, _ = setup
    jobs, _ = enqueue_enhancements(pid, wid, clip_ids=[cid])
    outputs = asyncio.run(QueueWorker()._run_job(jobs[0]))
    assert outputs[0].endswith(".mp4")
    assert read_clip(pid, cid)["clip_path"] == str(original)
    export = queue_manager.enqueue(project_id=pid, kind="export")
    asyncio.run(run_export(export, {}, event_bus, dry_run=True))
    payload = json.loads(queue_manager.get_job(export["id"])["payload_json"])
    assert payload["clip_sources"] == [{"clip_id": cid, "path": outputs[0]}]


def test_migration_adds_columns_to_existing_clips(setup):
    _, pid, cid, _, original, generated = setup
    conn = get_db(settings.db_path)
    for column in (
        "enhanced_path",
        "enhancement_source_path",
        "enhancement_settings_json",
        "use_enhanced",
    ):
        conn.execute(f"ALTER TABLE clips DROP COLUMN {column}")
    conn.commit()
    conn.close()
    asyncio.run(migrate_db(settings.db_path))
    clip = read_clip(pid, cid)
    assert clip["use_enhanced"] == 0 and clip["enhanced_path"] is None
    assert clip["clip_path"] == str(original)
    assert json.loads(clip["video_settings_json"]) == generated


def test_project_archive_carries_versions_and_independent_setup(setup, tmp_path):
    _, pid, cid, wid, _, _ = setup
    jobs, _ = enqueue_enhancements(pid, wid, clip_ids=[cid], input_values={"2": "Enhance only"})
    output = tmp_path / "enhanced.mp4"
    output.write_bytes(b"enhanced")
    complete(jobs[0], output)
    archive, _ = asyncio.run(export_project(pid, include_videos=True))
    try:
        imported = asyncio.run(import_project(archive))
    finally:
        archive.unlink()
    clips, _ = collect_clips(imported["project_id"])
    clip = read_clip(imported["project_id"], clips[0]["id"])
    assert clip["use_enhanced"] == 1
    assert clip["enhancement_source_path"] == clip["clip_path"]
    assert Path(clip["enhanced_path"]).read_bytes() == b"enhanced"
    assert json.loads(clip["enhancement_settings_json"])["input_values"]["2"] == "Enhance only"


def test_moved_install_preserves_enhancement_and_film_snapshots(setup, tmp_path):
    _, pid, cid, wid, _, _ = setup
    original = tmp_path / "old" / "assets" / str(pid) / "video" / "original.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    conn = get_db(settings.db_path)
    conn.execute("UPDATE clips SET clip_path = ? WHERE id = ?", (str(original), cid))
    conn.commit()
    conn.close()
    jobs, _ = enqueue_enhancements(pid, wid, clip_ids=[cid])
    enhanced = original.parent.parent / "enhancement" / "final.mp4"
    complete(jobs[0], enhanced)
    export = queue_manager.enqueue(
        project_id=pid,
        kind="export",
        payload={"clip_sources": [{"clip_id": cid, "path": str(enhanced)}]},
    )
    new_data = tmp_path / "new" / "data"
    new_assets = new_data / "assets"
    conn = get_db(settings.db_path)
    try:
        rebase_stale_asset_paths(conn, new_data, new_assets)
        conn.commit()
        clip = dict(conn.execute("SELECT * FROM clips WHERE id = ?", (cid,)).fetchone())
        assert clip["enhancement_source_path"] == clip["clip_path"]
        assert clip["use_enhanced"] == 1
        source = str(new_assets / str(pid) / "video" / "original.mp4")
        result = str(new_assets / str(pid) / "enhancement" / "final.mp4")
        assert clip["clip_path"] == source and clip["enhanced_path"] == result
        assert _project_path_referenced(conn, result)
        enhancement_payload = json.loads(
            conn.execute("SELECT payload_json FROM jobs WHERE id = ?", (jobs[0]["id"],)).fetchone()[
                "payload_json"
            ]
        )
        assert enhancement_payload["source_path"] == source
        assert enhancement_payload["input_values"]["1"] == source
        export_payload = json.loads(
            conn.execute("SELECT payload_json FROM jobs WHERE id = ?", (export["id"],)).fetchone()[
                "payload_json"
            ]
        )
        assert export_payload["clip_sources"] == [{"clip_id": cid, "path": result}]
        # The old source remains project-owned even after another original is generated.
        conn.execute("UPDATE clips SET clip_path = ? WHERE id = ?", ("replacement.mp4", cid))
        assert _project_path_referenced(conn, source)
    finally:
        conn.close()
