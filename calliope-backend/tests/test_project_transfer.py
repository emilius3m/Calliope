"""Project export (.zip, with or without videos) and import into a new project."""
from __future__ import annotations

import io
import json
import zipfile

from calliope.config import settings
from calliope.db import get_db


def _file(rel: str, data: bytes = b"x") -> str:
    path = settings.assets_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _seed(client) -> dict:
    """A small but complete board: beat, cast, place, prop, scene, 2 clips, plan, draft."""
    pid = client.post("/api/projects", json={"title": "Il Mestiere"}).json()["id"]
    sheet = _file(f"{pid}/image/leo.png", b"PNG-leo")
    place = _file(f"{pid}/image/piazza.png", b"PNG-piazza")
    prop = _file(f"{pid}/image/lente.png", b"PNG-lente")
    video = _file(f"{pid}/video/clip1.mp4", b"MP4-clip")
    final = _file(f"{pid}/export/film.mp4", b"MP4-film")
    conn = get_db(settings.db_path)
    try:
        wf = conn.execute(
            "INSERT INTO workflows (name, kind, workflow_json) VALUES ('wf-2ref', 'video', '{}')"
        ).lastrowid
        beat = conn.execute(
            "INSERT INTO story_beats (project_id, order_index, title) VALUES (?, 1, 'Inizio')", (pid,)
        ).lastrowid
        char = conn.execute(
            "INSERT INTO characters (project_id, name, sheet_path) VALUES (?, 'Leo', ?)", (pid, sheet)
        ).lastrowid
        loc = conn.execute(
            "INSERT INTO locations (project_id, name, reference_image_path) VALUES (?, 'Piazza', ?)",
            (pid, place),
        ).lastrowid
        item = conn.execute(
            "INSERT INTO items (project_id, name, reference_image_path) VALUES (?, 'Lente', ?)", (pid, prop)
        ).lastrowid
        scene = conn.execute(
            """INSERT INTO scenes (project_id, beat_id, order_index, heading, duration_sec,
               location_id, env_image_path, video_path) VALUES (?, ?, 1, 'EXT. PIAZZA', 12, ?, ?, ?)""",
            (pid, beat, loc, place, video),
        ).lastrowid
        conn.execute("INSERT INTO scene_characters VALUES (?, ?)", (scene, char))
        conn.execute("INSERT INTO scene_items VALUES (?, ?)", (scene, item))
        settings_json = json.dumps(
            {"input_values": {"101": sheet, "150": video, "7": "a text value"},
             "form_workflow_id": wf, "prompt_draft": "subject_definitions: ...",
             "prompt_draft_meta": {"based_on": "old", "source": "mcp"}}
        )
        c1 = conn.execute(
            """INSERT INTO clips (scene_id, project_id, order_index, description, duration_sec,
               workflow_id, clip_path, video_settings_json) VALUES (?, ?, 1, 'uno', 6, ?, ?, ?)""",
            (scene, pid, wf, video, settings_json),
        ).lastrowid
        c2 = conn.execute(
            """INSERT INTO clips (scene_id, project_id, order_index, description, duration_sec, workflow_id)
               VALUES (?, ?, 2, 'due', 6, ?)""",
            (scene, pid, wf),
        ).lastrowid
        plan = {"overview": {}, "requirements": {}, "source": "client", "based_on": "x",
                "shots": [{"clip_id": c1, "action": "uno"}, {"clip_id": c2, "action": "due"}]}
        conn.execute("UPDATE projects SET continuity_json = ? WHERE id = ?", (json.dumps(plan), pid))
        conn.commit()
    finally:
        conn.close()
    return {"pid": pid, "video": video, "final": final}


def _export(client, pid: int, videos: bool) -> zipfile.ZipFile:
    r = client.get(f"/api/projects/{pid}/export", params={"include_videos": videos})
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    return zipfile.ZipFile(io.BytesIO(r.content))


def _import(client, zf: zipfile.ZipFile) -> dict:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for n in zf.namelist():
            out.writestr(n, zf.read(n))
    r = client.post("/api/projects/import", files={"file": ("p.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    return r.json()


def test_export_without_videos_ships_images_only(client):
    seed = _seed(client)
    zf = _export(client, seed["pid"], videos=False)
    names = zf.namelist()
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "calliope-project" and manifest["include_videos"] is False
    assert not any(n.endswith(".mp4") for n in names)
    assert sum(n.startswith("files/") for n in names) == 3  # leo, piazza, lente (piazza once)
    data = json.loads(zf.read("project.json"))
    assert data["scenes"][0]["item_ids"] and data["scenes"][0]["character_ids"]
    assert data["workflows"] == {str(data["clips"][0]["workflow_id"]): "wf-2ref"}


def test_import_creates_a_remapped_copy(client):
    seed = _seed(client)
    result = _import(client, _export(client, seed["pid"], videos=True))
    new = result["project_id"]
    assert new != seed["pid"] and result["title"] == "Il Mestiere (2)"
    assert result["scenes"] == 1 and result["clips"] == 2 and result["missing_workflows"] == []

    conn = get_db(settings.db_path)
    try:
        char = conn.execute("SELECT * FROM characters WHERE project_id = ?", (new,)).fetchone()
        assert char["sheet_path"].startswith(str(settings.assets_dir / str(new)))
        assert open(char["sheet_path"], "rb").read() == b"PNG-leo"
        scene = conn.execute("SELECT * FROM scenes WHERE project_id = ?", (new,)).fetchone()
        loc = conn.execute("SELECT id FROM locations WHERE project_id = ?", (new,)).fetchone()
        beat = conn.execute("SELECT id FROM story_beats WHERE project_id = ?", (new,)).fetchone()
        assert scene["location_id"] == loc["id"] and scene["beat_id"] == beat["id"]
        assert open(scene["video_path"], "rb").read() == b"MP4-clip"
        assert conn.execute("SELECT COUNT(*) FROM scene_characters WHERE scene_id = ?", (scene["id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM scene_items WHERE scene_id = ?", (scene["id"],)).fetchone()[0] == 1
        clips = conn.execute("SELECT * FROM clips WHERE project_id = ? ORDER BY order_index", (new,)).fetchall()
        vs = json.loads(clips[0]["video_settings_json"])
        assert vs["input_values"]["101"] == char["sheet_path"]
        assert vs["input_values"]["7"] == "a text value"
        assert vs["prompt_draft"].startswith("subject_definitions")
        plan = json.loads(conn.execute("SELECT continuity_json FROM projects WHERE id = ?", (new,)).fetchone()[0])
        assert [s["clip_id"] for s in plan["shots"]] == [c["id"] for c in clips]
        assert plan["source"] == "client"
    finally:
        conn.close()
    assert (settings.assets_dir / str(new) / "export" / "film.mp4").read_bytes() == b"MP4-film"
    # the source project is untouched
    assert client.get(f"/api/projects/{seed['pid']}").json()["title"] == "Il Mestiere"


def test_import_without_videos_drops_video_references(client):
    seed = _seed(client)
    new = _import(client, _export(client, seed["pid"], videos=False))["project_id"]
    conn = get_db(settings.db_path)
    try:
        scene = conn.execute("SELECT * FROM scenes WHERE project_id = ?", (new,)).fetchone()
        assert scene["video_path"] is None
        clip = conn.execute("SELECT * FROM clips WHERE project_id = ? ORDER BY order_index", (new,)).fetchone()
        assert clip["clip_path"] is None
        values = json.loads(clip["video_settings_json"])["input_values"]
        assert "150" not in values  # the unshipped video input is dropped, not left dangling
    finally:
        conn.close()


def test_import_rejects_other_archives(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    r = client.post("/api/projects/import", files={"file": ("x.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 422
    r = client.post("/api/projects/import", files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert r.status_code == 422
    assert client.get("/api/projects/999/export").status_code == 404


H3_PROMPT = (
    "subject_definitions:\n<Subject 1> is Leo.\n\nsummary:\n[reference generation] Leo.\n\n"
    "retention_analysis:\n<Subject 1>: fully_preserved.\n\ndetailed_description:\n[Shot 1] Leo.\n\n"
    "overall_soundscape:\nN/A\n\nnon_diegetic_music:\nN/A"
)


def test_current_h3_drafts_stay_current_after_import(client):
    import asyncio

    from calliope.agent.video_agent import client_prompt_states, save_client_prompts

    seed = _seed(client)
    pid = seed["pid"]
    wf_json = {
        str(n): {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": f"Ref {n} (Input:image)"}}
        for n in (148, 149)
    }
    conn = get_db(settings.db_path)
    try:
        wf = conn.execute(
            "INSERT INTO workflows (name, kind, workflow_json, prompt_profile) VALUES ('h3-2ref', 'video', ?, 'minimax_h3_ref')",
            (json.dumps(wf_json),),
        ).lastrowid
        conn.execute("UPDATE clips SET workflow_id = ?, video_settings_json = NULL WHERE project_id = ?", (wf, pid))
        clip_ids = [r[0] for r in conn.execute("SELECT id FROM clips WHERE project_id = ? ORDER BY order_index", (pid,))]
        conn.commit()
    finally:
        conn.close()
    saved = asyncio.run(save_client_prompts(pid, {clip_ids[0]: H3_PROMPT}))
    assert saved.get("errors") in ({}, None), saved
    assert asyncio.run(client_prompt_states(pid, clip_ids[:1]))[0]["draft_fresh"]

    result = _import(client, _export(client, pid, videos=False))
    assert result["drafts_kept_current"] == 1
    conn = get_db(settings.db_path)
    try:
        new_ids = [r[0] for r in conn.execute(
            "SELECT id FROM clips WHERE project_id = ? ORDER BY order_index", (result["project_id"],))]
    finally:
        conn.close()
    states = asyncio.run(client_prompt_states(result["project_id"], new_ids))
    assert [s["draft_fresh"] for s in states] == [True, False]
