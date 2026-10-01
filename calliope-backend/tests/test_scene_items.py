"""Scene items (props): linked to scenes, carried by the script, wired into video prompts."""
from __future__ import annotations

import asyncio
import json

from calliope.agent import video_agent
from calliope.agent.harness.registry import ToolContext
from calliope.agent.prompts import build_minimax_h3_ref_messages, scene_video_prompt
from calliope.agent.script_agent import _persist_scenes, script_brief
from calliope.comfyui.parser import parse_dynamic_inputs
from calliope.config import settings
from calliope.db import get_db, scene_items

SCOPE = {"heading": "INT. CLINIC - DAY", "action": "The stethoscope lies on the desk.", "duration_sec": 6}
LEO = {"id": 1, "name": "Leo", "appearance": "small ochre owl", "sheet_path": "leo.png"}
CLINIC = {"name": "Clinic", "description": "pastel walls"}
STETHO = {"id": 7, "name": "Stethoscope", "description": "silver stethoscope", "reference_image_path": "stetho.png"}


def _slots(n: int) -> list[dict]:
    wf = {
        str(100 + i): {
            "class_type": "LoadImage",
            "inputs": {"image": ""},
            "_meta": {"title": f"Ref {i + 1} (Input:image)"},
        }
        for i in range(n)
    }
    return parse_dynamic_inputs(wf)


def _project_with_item(client) -> tuple[int, int, int]:
    pid = client.post("/api/projects", json={"title": "props"}).json()["id"]
    item = client.post(f"/api/projects/{pid}/items", json={"name": "Stethoscope"}).json()
    scene = client.post(f"/api/projects/{pid}/scenes", json={"order_index": 1, "heading": "S1"}).json()
    return pid, item["id"], scene["id"]


def test_scene_items_roundtrip_and_project_scoping(client):
    pid, item_id, sid = _project_with_item(client)
    other = client.post("/api/projects", json={"title": "other"}).json()["id"]
    foreign = client.post(f"/api/projects/{other}/items", json={"name": "Sword"}).json()["id"]

    r = client.patch(f"/api/projects/{pid}/scenes/{sid}", json={"item_ids": [item_id, foreign]})
    assert r.status_code == 200
    assert r.json()["item_ids"] == [item_id]  # another project's item is ignored
    assert r.json()["items"][0]["name"] == "Stethoscope"

    # unrelated edits keep the items; an empty list clears them
    r = client.patch(f"/api/projects/{pid}/scenes/{sid}", json={"heading": "NEW"})
    assert r.json()["item_ids"] == [item_id]
    r = client.patch(f"/api/projects/{pid}/scenes/{sid}", json={"item_ids": []})
    assert r.json()["item_ids"] == []

    listed = client.get(f"/api/projects/{pid}/scenes").json()["scenes"]
    assert listed[0]["item_ids"] == []


def test_items_fill_slots_after_characters_and_location():
    # 3 slots: Leo, the clinic, then the prop
    subjects, paths, _ = video_agent.resolve_h3_references(
        _slots(3), {}, [LEO], CLINIC, "clinic.png", [STETHO]
    )
    assert [s["kind"] for s in subjects] == ["character", "location", "item"]
    assert paths == ["leo.png", "clinic.png", "stetho.png"]
    assert subjects[2]["appearance"] == "silver stethoscope"
    assert video_agent._text_only_props([STETHO], subjects) == []

    # 2 slots: the prop never pushes out the setting — it goes to text
    subjects, paths, _ = video_agent.resolve_h3_references(
        _slots(2), {}, [LEO], CLINIC, "clinic.png", [STETHO]
    )
    assert [s["kind"] for s in subjects] == ["character", "location"]
    props = video_agent._text_only_props([STETHO], subjects)
    assert props == [{"name": "Stethoscope", "appearance": "silver stethoscope"}]
    user = build_minimax_h3_ref_messages(SCOPE, subjects, props=props)[1]["content"]
    assert "Props present" in user and "- Stethoscope: silver stethoscope" in user


def test_no_items_keeps_prompts_and_hash_unchanged():
    subjects, _, _ = video_agent.resolve_h3_references(_slots(2), {}, [LEO], CLINIC, "clinic.png")
    user = build_minimax_h3_ref_messages(SCOPE, subjects)[1]["content"]
    assert "Props present" not in user
    assert scene_video_prompt(SCOPE, [LEO], CLINIC) == scene_video_prompt(SCOPE, [LEO], CLINIC, [])

    clip = {"heading": "H", "action": "A", "character_ids": [1]}
    base = video_agent._clip_prompt_hash(clip)
    assert video_agent._clip_prompt_hash({**clip, "item_ids": []}) == base
    assert video_agent._clip_prompt_hash({**clip, "item_ids": [7]}) != base


def test_prose_and_base_prompts_name_the_props():
    assert "Stethoscope (silver stethoscope)" in scene_video_prompt(SCOPE, [LEO], CLINIC, [STETHO])

    class _Dead:
        async def chat(self, messages, **kw):
            raise RuntimeError("offline")

        async def close(self):
            return None

    orig = video_agent.LLMClient
    video_agent.LLMClient = type("S", (), {"for_role": staticmethod(lambda role, **kw: _Dead())})
    try:
        text = asyncio.run(video_agent._h3_base_rewrite(SCOPE, [LEO], CLINIC, items=[STETHO], timeout=1))
    finally:
        video_agent.LLMClient = orig
    assert "Stethoscope: silver stethoscope" in text


def test_script_brief_lists_items_and_content_saves_item_ids(client):
    pid, item_id, _ = _project_with_item(client)
    brief = script_brief(pid)
    assert brief["items"] == [{"id": item_id, "name": "Stethoscope"}]
    assert f"id={item_id} Stethoscope" in brief["user"] and "item_ids" in brief["user"]

    conn = get_db(settings.db_path)
    try:
        created = _persist_scenes(
            conn, pid, [{"order_index": 2, "heading": "S2", "character_ids": [], "item_ids": [item_id]}]
        )
        conn.commit()
        assert [i["id"] for i in scene_items(conn, created[0]["id"])] == [item_id]
    finally:
        conn.close()


def test_update_scene_tool_sets_items(client):
    from calliope.agent.harness.plugins.script import t_list_scenes, t_update_scene

    pid, item_id, sid = _project_with_item(client)
    ctx = ToolContext(session_id=None, project_id=pid, origin="mcp")
    out = asyncio.run(t_update_scene(ctx, {"scene_id": sid, "item_ids": [item_id]}))
    assert "scene" in out, out
    scenes = asyncio.run(t_list_scenes(ctx, {}))
    assert scenes[0]["items"] == [{"id": item_id, "name": "Stethoscope"}]
    assert json.dumps(scenes)  # tool output stays serializable
