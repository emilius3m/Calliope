"""Multi-panel character sheets must be declared as their own <Picture N> in H3 prompts.

Named only inside a subject line, a sheet reads as several characters and H3
puts every panel on screen; declared with its panels, it gives one character.
"""

from __future__ import annotations

import asyncio

import pytest
from PIL import Image

from calliope.agent import video_agent
from calliope.agent.harness.plugins.story import t_update_asset
from calliope.agent.harness.registry import ToolContext
from calliope.agent.prompts import (
    MINIMAX_H3_REF_SYSTEM,
    build_minimax_h3_ref_messages,
    minimax_h3_ref_fallback,
)
from calliope.comfyui.parser import parse_dynamic_inputs
from calliope.config import settings
from calliope.db import get_db

SCENE = {
    "heading": "INT. CLINIC - DAY",
    "action": "Sofia listens to Leo's heart.",
    "duration_sec": 6,
}
CLINIC = {"name": "Clinic", "description": "pastel walls"}


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


@pytest.fixture()
def pictures(tmp_path):
    sheet = tmp_path / "sofia_sheet.png"
    Image.new("RGB", (1376, 768), "white").save(sheet)
    single = tmp_path / "leo_single.png"
    Image.new("RGB", (566, 708), "white").save(single)
    return str(sheet), str(single)


def test_auto_layout_reads_the_picture_shape(pictures):
    sheet, single = pictures
    assert video_agent.character_sheet_panels({}, sheet) == video_agent.DEFAULT_SHEET_PANELS
    assert video_agent.character_sheet_panels({}, single) is None
    assert video_agent.character_sheet_panels({}, "missing.png") is None


def test_user_choice_wins_over_the_shape(pictures):
    sheet, single = pictures
    assert video_agent.character_sheet_panels({"reference_layout": "single"}, sheet) is None
    custom = {"reference_layout": "sheet", "reference_panels": "a front view and a head close-up"}
    assert video_agent.character_sheet_panels(custom, single) == "a front view and a head close-up"


def test_only_sheet_subjects_carry_the_marker(pictures):
    sheet, single = pictures
    cast = [
        {"id": 1, "name": "Leo", "appearance": "small owl", "sheet_path": single},
        {"id": 2, "name": "Sofia", "appearance": "doctor in a white coat", "sheet_path": sheet},
    ]
    subjects, paths, _ = video_agent.resolve_h3_references(
        _slots(3), {}, cast, CLINIC, "clinic.png"
    )
    assert paths == [single, sheet, "clinic.png"]
    assert "sheet" not in subjects[0] and "sheet" not in subjects[2]
    assert subjects[1]["sheet"] == video_agent.DEFAULT_SHEET_PANELS


def test_rewrite_is_told_to_declare_the_sheet(pictures):
    sheet, _ = pictures
    subjects = [
        {
            "index": 1,
            "kind": "character",
            "name": "Sofia",
            "appearance": "doctor",
            "path": sheet,
            "sheet": "a front view and a back view",
        }
    ]
    user = build_minimax_h3_ref_messages(SCENE, subjects, setting=CLINIC)[1]["content"]
    assert (
        "CHARACTER SHEET of this one character (panels: a front view and a back view)"
        in user
    )
    assert "declare it on its own line before the subject" in MINIMAX_H3_REF_SYSTEM


def test_fallback_declares_the_sheet_and_one_character():
    subjects = [
        {
            "index": 1,
            "kind": "character",
            "name": "Leo",
            "appearance": "small owl",
            "path": "leo.png",
        },
        {
            "index": 2,
            "kind": "character",
            "name": "Sofia",
            "appearance": "doctor in a white coat",
            "path": "sofia.png",
            "sheet": "a front view and a head close-up",
        },
    ]
    text = minimax_h3_ref_fallback(SCENE, subjects, setting=CLINIC)
    assert '<Subject 1> is the character "Leo" in <Picture 1>, small owl.' in text
    assert (
        "<Picture 2> is a character sheet of Sofia with a front view and a head close-up;" in text
    )
    assert "<Subject 2> is Sofia, the one character shown in every panel of <Picture 2>" in text
    assert "<Picture 2> (character sheet for <Subject 2>): partially_preserved" in text
    assert "[Shot 1] <Subject 2> appears once, as one single character" in text


def test_reference_signature_changes_only_with_sheets():
    single = [{"index": 1, "kind": "character"}]
    with_sheet = [{"index": 1, "kind": "character", "sheet": "panels"}]
    base = video_agent._reference_signature(["a.png"], [])
    assert video_agent._reference_signature(["a.png"], [], single) == base == "img=a.png"
    assert video_agent._reference_signature(["a.png"], [], with_sheet) == "img=a.png|sheets=1"


def test_layout_is_editable_over_rest_and_mcp(client):
    pid = client.post("/api/projects", json={"title": "sheets"}).json()["id"]
    cid = client.post(f"/api/projects/{pid}/characters", json={"name": "Sofia"}).json()["id"]
    r = client.patch(
        f"/api/projects/{pid}/characters/{cid}",
        json={"reference_layout": "sheet", "reference_panels": "five panels"},
    )
    assert r.status_code == 200
    assert (r.json()["reference_layout"], r.json()["reference_panels"]) == ("sheet", "five panels")
    assert (
        client.patch(
            f"/api/projects/{pid}/characters/{cid}", json={"reference_layout": "panels"}
        ).status_code
        == 422
    )
    ctx = ToolContext(session_id=9_999_412, project_id=pid)
    out = asyncio.run(
        t_update_asset("character", ctx, {"character_id": cid, "reference_layout": "single"})
    )
    assert out["ok"] is not False
    conn = get_db(settings.db_path)
    try:
        row = conn.execute(
            "SELECT reference_layout FROM characters WHERE id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    assert row["reference_layout"] == "single"
