"""Clip trims are non-destructive in/out points that belong to one rendered original."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from calliope.agent.harness.plugins.script import t_update_clip
from calliope.agent.harness.registry import ToolContext
from calliope.config import settings
from calliope.db import get_db, migrate_db
from calliope.events.bus import event_bus
from calliope.export.runner import apply_trims, build_ffmpeg_cmd, collect_clips, run_export
from calliope.project_transfer import export_project, import_project
from calliope.queue.manager import queue_manager


@pytest.fixture()
def clip(client, tmp_path):
    pid = client.post("/api/projects", json={"title": "Trim QA"}).json()["id"]
    scene = client.post(f"/api/projects/{pid}/scenes", json={"order_index": 1}).json()
    cid = scene["clips"][0]["id"]
    original = tmp_path / "original.mp4"
    original.write_bytes(b"original bytes")
    client.patch(f"/api/projects/{pid}/clips/{cid}", json={"clip_path": str(original)})
    return client, pid, cid, original


def patch(client, pid, cid, body):
    return client.patch(f"/api/projects/{pid}/clips/{cid}", json=body)


def test_trim_is_stored_without_touching_the_original(clip):
    client, pid, cid, original = clip
    res = patch(client, pid, cid, {"trim": {"start": 0.4, "end": 4.25}})
    assert res.status_code == 200
    assert res.json()["trim"] == {"start": 0.4, "end": 4.25}
    assert res.json()["clip_path"] == str(original)
    assert original.read_bytes() == b"original bytes"
    cleared = patch(client, pid, cid, {"trim": None}).json()
    assert cleared["trim"] is None and cleared["trim_start"] is None


@pytest.mark.parametrize(
    "trim",
    [{"start": 2.0, "end": 2.2}, {"start": 3.0, "end": 1.0}, {"start": -1.0, "end": 3.0}],
)
def test_invalid_ranges_are_rejected(clip, trim):
    client, pid, cid, _ = clip
    assert patch(client, pid, cid, {"trim": trim}).status_code in (400, 422)


def test_trim_needs_a_rendered_video(client):
    pid = client.post("/api/projects", json={"title": "No video"}).json()["id"]
    scene = client.post(f"/api/projects/{pid}/scenes", json={"order_index": 1}).json()
    cid = scene["clips"][0]["id"]
    res = patch(client, pid, cid, {"trim": {"start": 0, "end": 3}})
    assert res.status_code == 400


def test_new_render_retires_the_trim(clip, tmp_path):
    client, pid, cid, _ = clip
    patch(client, pid, cid, {"trim": {"start": 0.5, "end": 3}})
    rerender = tmp_path / "rerender.mp4"
    rerender.write_bytes(b"new")
    assert patch(client, pid, cid, {"clip_path": str(rerender)}).json()["trim"] is None


def test_export_cuts_each_input_and_times_crossfades_on_kept_ranges():
    clips = [{"clip_path": "a.mp4", "trim": (1.0, 4.0)}, {"clip_path": "b.mp4", "trim": None}]
    probes = apply_trims(
        clips, [{"duration": 6.0, "has_audio": True}, {"duration": 5.0, "has_audio": True}]
    )
    assert probes[0]["duration"] == 3.0
    cmd = build_ffmpeg_cmd(clips, probes, "out.mp4")
    first = cmd.index("a.mp4")
    assert cmd[first - 5 : first + 1] == ["-ss", "1.000", "-t", "3.000", "-i", "a.mp4"]
    # the untrimmed clip is read whole
    assert cmd[cmd.index("b.mp4") - 2 : cmd.index("b.mp4")] == ["a.mp4", "-i"]
    # crossfade offset = kept 3.0 s - 0.5 s fade
    assert "offset=2.500" in " ".join(cmd)


def test_trim_end_past_the_video_keeps_what_exists():
    probes = apply_trims([{"trim": (2.0, 99.0)}], [{"duration": 5.0, "has_audio": False}])
    assert probes[0]["duration"] == 3.0


def test_export_records_the_trim_in_its_sources(clip):
    client, pid, cid, original = clip
    patch(client, pid, cid, {"trim": {"start": 0.5, "end": 3}})
    clips, _ = collect_clips(pid)
    assert clips[0]["trim"] == (0.5, 3.0)
    was_paused = queue_manager.paused
    queue_manager.paused = True
    try:
        export = queue_manager.enqueue(project_id=pid, kind="export")
        asyncio.run(run_export(export, {}, event_bus, dry_run=True))
    finally:
        queue_manager.paused = was_paused
    payload = json.loads(queue_manager.get_job(export["id"])["payload_json"])
    assert payload["clip_sources"] == [{"clip_id": cid, "path": str(original), "trim": [0.5, 3.0]}]


def test_mcp_update_clip_trims_and_clears(clip):
    client, pid, cid, _ = clip
    ctx = ToolContext(session_id=9_999_311, project_id=pid)
    out = asyncio.run(t_update_clip(ctx, {"clip_id": cid, "trim_start": 0.3, "trim_end": 4}))
    assert out["clip"]["trim"] == {"start": 0.3, "end": 4.0}
    assert "trim_source_path" not in out["clip"]
    out = asyncio.run(t_update_clip(ctx, {"clip_id": cid, "trim_end": 3.5}))
    assert out["clip"]["trim"] == {"start": 0.3, "end": 3.5}
    out = asyncio.run(t_update_clip(ctx, {"clip_id": cid, "clear_trim": True}))
    assert out["clip"]["trim"] is None
    bad = asyncio.run(t_update_clip(ctx, {"clip_id": cid, "trim_start": 1}))
    assert bad["ok"] is False


def test_migration_adds_trim_columns(clip):
    client, pid, cid, original = clip
    conn = get_db(settings.db_path)
    for column in ("trim_start", "trim_end", "trim_source_path"):
        conn.execute(f"ALTER TABLE clips DROP COLUMN {column}")
    conn.commit()
    conn.close()
    asyncio.run(migrate_db(settings.db_path))
    res = patch(client, pid, cid, {"trim": {"start": 0, "end": 2}})
    assert res.json()["trim"] == {"start": 0.0, "end": 2.0}


def test_project_archive_keeps_trim_only_with_its_video(clip):
    client, pid, cid, _ = clip
    patch(client, pid, cid, {"trim": {"start": 0.5, "end": 3}})
    for include_videos, expected in ((True, (0.5, 3.0)), (False, None)):
        archive, _ = asyncio.run(export_project(pid, include_videos=include_videos))
        try:
            imported = asyncio.run(import_project(archive))
        finally:
            archive.unlink()
        scenes = client.get(f"/api/projects/{imported['project_id']}/scenes").json()["scenes"]
        clips = scenes[0]["clips"]
        trim = clips[0]["trim"]
        assert (trim and (trim["start"], trim["end"])) == expected
        if include_videos:
            assert Path(clips[0]["clip_path"]).read_bytes() == b"original bytes"
