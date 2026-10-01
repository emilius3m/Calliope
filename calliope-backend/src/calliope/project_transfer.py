"""Whole-project export to a .zip and import back — into this install or another one.

Archive layout::

    manifest.json   format, version, app version, whether videos are included
    project.json    project row, beats, characters, locations, items, scenes (with
                    character_ids / item_ids), clips, the names of the workflows they
                    use, the file map, and which prompt drafts were current
    files/<kind>/…  every image the project references; rendered clips, scene videos
                    and final exports only when videos are included

Import creates a NEW project: ids are remapped, files land under assets/<new id>/,
workflows are matched by name (missing ones are reported and left unset), the
continuity plan is re-pointed at the new clips, and prompt drafts that were
current at export are kept current. Agent chats, canvases, jobs and memories are
not part of a project export.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from calliope import __version__
from calliope.config import settings
from calliope.db import _rebase_path, get_db, row_to_dict

logger = logging.getLogger(__name__)

FORMAT = "calliope-project"
VERSION = 1
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT | AUDIO_EXT


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-") or "project"


def _is_media(value: Any) -> bool:
    return isinstance(value, str) and Path(value.strip()).suffix.lower() in MEDIA_EXT


def _resolve_file(value: str) -> Path | None:
    """The file a stored path points at: as stored, relative to the backend, or rebased."""
    raw = Path(value.strip())
    candidates = [raw] if raw.is_absolute() else [settings.data_dir.parent / raw, Path.cwd() / raw]
    rebased = _rebase_path(value, settings.data_dir, settings.assets_dir)
    if rebased:
        candidates.append(Path(rebased))
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def _json(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


# ── export ──────────────────────────────────────────────────────────────────


class _FileSet:
    """Collects referenced files once each, under stable archive names."""

    def __init__(self, include_videos: bool):
        self.include_videos = include_videos
        self.map: dict[str, str] = {}  # stored path -> archive name
        self.sources: dict[str, Path] = {}  # archive name -> file on disk
        self.skipped_videos = 0
        self.missing: list[str] = []

    def add(self, value: Any, kind: str) -> None:
        if not _is_media(value) or value in self.map:
            return
        if Path(value).suffix.lower() in VIDEO_EXT and not self.include_videos:
            self.skipped_videos += 1
            return
        src = _resolve_file(value)
        if src is None:
            self.missing.append(value)
            return
        existing = next((a for a, p in self.sources.items() if p == src), None)
        if existing:
            self.map[value] = existing
            return
        arc = f"files/{kind}/{len(self.sources) + 1:04d}_{src.name}"
        self.map[value] = arc
        self.sources[arc] = src


def _settings_media(settings_json: Any) -> list[str]:
    data = _json(settings_json)
    values = (data or {}).get("input_values") if isinstance(data, dict) else None
    return [v for v in (values or {}).values() if _is_media(v)]


async def export_project(project_id: int, *, include_videos: bool = False) -> tuple[Path, str]:
    """Write the project archive to a temp file. Returns (path, download filename)."""
    conn = get_db(settings.db_path)
    try:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not project:
            raise ValueError(f"Project {project_id} not found")
        project = row_to_dict(project)

        def rows(sql: str, *args: Any) -> list[dict[str, Any]]:
            return [row_to_dict(r) for r in conn.execute(sql, args).fetchall()]

        beats = rows("SELECT * FROM story_beats WHERE project_id = ? ORDER BY order_index, id", project_id)
        characters = rows("SELECT * FROM characters WHERE project_id = ? ORDER BY id", project_id)
        locations = rows("SELECT * FROM locations WHERE project_id = ? ORDER BY id", project_id)
        items = rows("SELECT * FROM items WHERE project_id = ? ORDER BY id", project_id)
        scenes = rows("SELECT * FROM scenes WHERE project_id = ? ORDER BY order_index, id", project_id)
        for sc in scenes:
            sc["character_ids"] = [
                r["character_id"]
                for r in conn.execute(
                    "SELECT character_id FROM scene_characters WHERE scene_id = ? ORDER BY character_id",
                    (sc["id"],),
                ).fetchall()
            ]
            sc["item_ids"] = [
                r["item_id"]
                for r in conn.execute(
                    "SELECT item_id FROM scene_items WHERE scene_id = ? ORDER BY item_id", (sc["id"],)
                ).fetchall()
            ]
        clips = rows("SELECT * FROM clips WHERE project_id = ? ORDER BY scene_id, order_index, id", project_id)

        wf_ids: set[int] = set()
        for r in [*scenes, *clips]:
            if r.get("workflow_id"):
                wf_ids.add(int(r["workflow_id"]))
            vs = _json(r.get("video_settings_json"))
            if isinstance(vs, dict) and isinstance(vs.get("form_workflow_id"), int):
                wf_ids.add(vs["form_workflow_id"])
            enhancement = _json(r.get("enhancement_settings_json"))
            if isinstance(enhancement, dict) and isinstance(enhancement.get("workflow_id"), int):
                wf_ids.add(enhancement["workflow_id"])
        workflows = {
            str(r["id"]): r["name"]
            for r in conn.execute(
                f"SELECT id, name FROM workflows WHERE id IN ({','.join('?' * len(wf_ids)) or 'NULL'})",
                tuple(wf_ids),
            ).fetchall()
        }
    finally:
        conn.close()

    files = _FileSet(include_videos)
    files.add(project.get("cover_path"), "image")
    for c in characters:
        files.add(c.get("portrait_path"), "characters")
        files.add(c.get("sheet_path"), "characters")
    for loc in locations:
        files.add(loc.get("reference_image_path"), "locations")
    for it in items:
        files.add(it.get("reference_image_path"), "items")
    for sc in scenes:
        files.add(sc.get("env_image_path"), "locations")
        files.add(sc.get("video_path"), "video")
        for v in _settings_media(sc.get("video_settings_json")):
            files.add(v, "inputs")
    for cl in clips:
        files.add(cl.get("clip_path"), "video")
        files.add(cl.get("enhanced_path"), "video")
        files.add(cl.get("enhancement_source_path"), "video")
        for v in _settings_media(cl.get("enhancement_settings_json")):
            files.add(v, "inputs")
        for v in _settings_media(cl.get("video_settings_json")):
            files.add(v, "inputs")
    extras: list[str] = []
    if include_videos:
        export_dir = settings.assets_dir / str(project_id) / "export"
        if export_dir.is_dir():
            for f in sorted(export_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in VIDEO_EXT:
                    arc = f"files/export/{f.name}"
                    files.sources[arc] = f
                    extras.append(arc)

    fresh = await _fresh_drafts(project_id, project, clips)
    payload = {
        "project": project,
        "story_beats": beats,
        "characters": characters,
        "locations": locations,
        "items": items,
        "scenes": scenes,
        "clips": clips,
        "workflows": workflows,
        "files": files.map,
        "extras": extras,
        "fresh_draft_clip_ids": fresh,
    }
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "app_version": __version__,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_title": project.get("title"),
        "include_videos": include_videos,
        "file_count": len(files.sources),
        "skipped_videos": files.skipped_videos,
        "missing_files": files.missing,
    }

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    name = f"{_slug(project.get('title') or '')}-{stamp}{'-video' if include_videos else ''}.zip"
    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="calliope-export-")
    os.close(fd)
    out = Path(tmp)
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1), zipfile.ZIP_DEFLATED)
        zf.writestr("project.json", json.dumps(payload, ensure_ascii=False, indent=1), zipfile.ZIP_DEFLATED)
        for arc, src in files.sources.items():
            # media is already compressed: store it as is
            zf.write(src, arc, zipfile.ZIP_STORED)
    return out, name


async def _fresh_drafts(project_id: int, project: dict[str, Any], clips: list[dict[str, Any]]) -> list[int]:
    """Clip ids whose prompt draft is current — so the import can keep them current.

    Only with a stored continuity plan: without one, computing freshness would
    write a plan into the project being exported.
    """
    has_draft = [
        int(c["id"])
        for c in clips
        if isinstance(_json(c.get("video_settings_json")), dict)
        and (_json(c.get("video_settings_json")) or {}).get("prompt_draft")
    ]
    if not has_draft or not project.get("continuity_json"):
        return []
    from calliope.agent.video_agent import client_prompt_states

    try:
        states = await client_prompt_states(project_id, has_draft)
    except Exception as exc:  # never block an export on this
        logger.warning("Draft freshness check failed during export (%s)", exc)
        return []
    return [s["clip_id"] for s in states if s.get("draft_fresh")]


# ── import ──────────────────────────────────────────────────────────────────


def _insert(conn, table: str, row: dict[str, Any]) -> int:
    """Insert the columns this database has (archives from other versions stay importable)."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall() if r[1] != "id"]
    data = {k: row[k] for k in cols if k in row}
    names = ", ".join(data)
    marks = ", ".join(f":{k}" for k in data)
    cur = conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", data)
    return int(cur.lastrowid)


def _unique_title(conn, title: str) -> str:
    taken = {r["title"] for r in conn.execute("SELECT title FROM projects").fetchall()}
    if title not in taken:
        return title
    n = 2
    while f"{title} ({n})" in taken:
        n += 1
    return f"{title} ({n})"


async def import_project(archive: Path) -> dict[str, Any]:
    """Create a new project from an export archive. Returns a summary."""
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a zip archive") from exc
    with zf:
        try:
            manifest = json.loads(zf.read("manifest.json"))
            data = json.loads(zf.read("project.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError("Not a Calliope project archive (manifest.json / project.json missing)") from exc
        if manifest.get("format") != FORMAT:
            raise ValueError("Not a Calliope project archive")
        if int(manifest.get("version") or 0) > VERSION:
            raise ValueError(
                f"Archive format v{manifest.get('version')} is newer than this Calliope supports (v{VERSION})"
            )
        names = set(zf.namelist())

        conn = get_db(settings.db_path)
        new_dir: Path | None = None
        try:
            src = data["project"]
            project_row = {k: v for k, v in src.items() if k not in ("id", "continuity_json", "cover_path")}
            project_row["title"] = _unique_title(conn, src.get("title") or "Imported project")
            pid = _insert(conn, "projects", project_row)
            new_dir = settings.assets_dir / str(pid)

            # files: only archive names this export wrote, flattened under assets/<pid>/<kind>/
            path_map: dict[str, str] = {}
            written: dict[str, str] = {}
            for arc in [*set(data.get("files", {}).values()), *data.get("extras", [])]:
                if arc in written or arc not in names or not arc.startswith("files/"):
                    continue
                parts = arc.split("/")
                kind = re.sub(r"[^a-z]", "", parts[1].lower()) if len(parts) > 2 else "misc"
                dest = new_dir / (kind or "misc") / Path(parts[-1]).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(arc) as fin, open(dest, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                written[arc] = str(dest)
            for old, arc in data.get("files", {}).items():
                if arc in written:
                    path_map[old] = written[arc]

            def media(value: Any) -> Any:
                """A stored path, re-pointed at the imported copy; dropped if not shipped."""
                if not _is_media(value):
                    return value
                return path_map.get(value)

            # workflows by name
            wf_map: dict[int, int] = {}
            missing_wf: list[str] = []
            for old_id, name in (data.get("workflows") or {}).items():
                row = conn.execute(
                    "SELECT id FROM workflows WHERE name = ? ORDER BY is_enabled DESC, id LIMIT 1", (name,)
                ).fetchone()
                if row:
                    wf_map[int(old_id)] = int(row["id"])
                else:
                    missing_wf.append(name)

            def settings_json(raw: Any) -> str | None:
                vs = _json(raw)
                if not isinstance(vs, dict):
                    return None
                values = vs.get("input_values")
                if isinstance(values, dict):
                    vs["input_values"] = {
                        k: media(v) for k, v in values.items() if not (_is_media(v) and media(v) is None)
                    }
                if isinstance(vs.get("form_workflow_id"), int):
                    mapped = wf_map.get(vs["form_workflow_id"])
                    if mapped:
                        vs["form_workflow_id"] = mapped
                    else:
                        vs.pop("form_workflow_id")
                if isinstance(vs.get("workflow_id"), int):
                    mapped = wf_map.get(vs["workflow_id"])
                    if mapped:
                        vs["workflow_id"] = mapped
                    else:
                        vs.pop("workflow_id")
                return json.dumps(vs, ensure_ascii=False)

            cover = media(src.get("cover_path"))
            if cover:
                conn.execute("UPDATE projects SET cover_path = ? WHERE id = ?", (cover, pid))

            beat_map, char_map, loc_map, item_map, scene_map, clip_map = {}, {}, {}, {}, {}, {}
            for b in data.get("story_beats", []):
                beat_map[b["id"]] = _insert(conn, "story_beats", {**b, "project_id": pid})
            for c in data.get("characters", []):
                char_map[c["id"]] = _insert(
                    conn,
                    "characters",
                    {**c, "project_id": pid, "portrait_path": media(c.get("portrait_path")),
                     "sheet_path": media(c.get("sheet_path"))},
                )
            for loc in data.get("locations", []):
                loc_map[loc["id"]] = _insert(
                    conn, "locations",
                    {**loc, "project_id": pid, "reference_image_path": media(loc.get("reference_image_path"))},
                )
            for it in data.get("items", []):
                item_map[it["id"]] = _insert(
                    conn, "items",
                    {**it, "project_id": pid, "reference_image_path": media(it.get("reference_image_path"))},
                )
            for sc in data.get("scenes", []):
                sid = _insert(
                    conn,
                    "scenes",
                    {
                        **sc,
                        "project_id": pid,
                        "beat_id": beat_map.get(sc.get("beat_id")),
                        "location_id": loc_map.get(sc.get("location_id")),
                        "workflow_id": wf_map.get(sc.get("workflow_id")),
                        "env_image_path": media(sc.get("env_image_path")),
                        "video_path": media(sc.get("video_path")),
                        "video_settings_json": settings_json(sc.get("video_settings_json")),
                    },
                )
                scene_map[sc["id"]] = sid
                for cid in sc.get("character_ids") or []:
                    if cid in char_map:
                        conn.execute(
                            "INSERT OR IGNORE INTO scene_characters (scene_id, character_id) VALUES (?, ?)",
                            (sid, char_map[cid]),
                        )
                for iid in sc.get("item_ids") or []:
                    if iid in item_map:
                        conn.execute(
                            "INSERT OR IGNORE INTO scene_items (scene_id, item_id) VALUES (?, ?)",
                            (sid, item_map[iid]),
                        )
            for cl in data.get("clips", []):
                if cl.get("scene_id") not in scene_map:
                    continue
                clip_map[cl["id"]] = _insert(
                    conn,
                    "clips",
                    {
                        **cl,
                        "project_id": pid,
                        "scene_id": scene_map[cl["scene_id"]],
                        "workflow_id": wf_map.get(cl.get("workflow_id")),
                        "clip_path": media(cl.get("clip_path")),
                        "enhanced_path": media(cl.get("enhanced_path")),
                        "enhancement_source_path": media(cl.get("enhancement_source_path")),
                        "enhancement_settings_json": settings_json(cl.get("enhancement_settings_json")),
                        "use_enhanced": int(bool(cl.get("use_enhanced") and media(cl.get("enhanced_path")))),
                        "video_settings_json": settings_json(cl.get("video_settings_json")),
                    },
                )

            plan = _json(src.get("continuity_json"))
            if isinstance(plan, dict):
                shots = []
                for shot in plan.get("shots") or []:
                    if isinstance(shot, dict) and shot.get("clip_id") in clip_map:
                        shots.append({**shot, "clip_id": clip_map[shot["clip_id"]]})
                plan["shots"] = shots
                conn.execute(
                    "UPDATE projects SET continuity_json = ? WHERE id = ?",
                    (json.dumps(plan, ensure_ascii=False), pid),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            if new_dir is not None:
                shutil.rmtree(new_dir, ignore_errors=True)
            raise
        conn.close()

    fresh = [clip_map[c] for c in data.get("fresh_draft_clip_ids") or [] if c in clip_map]
    kept = await _rebase_plan_and_drafts(pid, fresh) if isinstance(plan, dict) else 0
    return {
        "ok": True,
        "project_id": pid,
        "title": project_row["title"],
        "scenes": len(scene_map),
        "clips": len(clip_map),
        "files": len(written),
        "include_videos": bool(manifest.get("include_videos")),
        "missing_workflows": missing_wf,
        "drafts_kept_current": kept,
        "missing_files_at_export": manifest.get("missing_files") or [],
    }


async def _rebase_plan_and_drafts(project_id: int, fresh_clip_ids: list[int]) -> int:
    """Point the plan at this board and re-stamp the drafts that were current.

    File paths and clip ids changed on import, which alone would make every
    plan and draft look stale.
    """
    from calliope.agent.continuity import basis_hash, load_board
    from calliope.agent.video_agent import client_prompt_states

    conn = get_db(settings.db_path)
    try:
        raw = conn.execute("SELECT continuity_json FROM projects WHERE id = ?", (project_id,)).fetchone()
        plan = _json(raw["continuity_json"]) if raw else None
        if isinstance(plan, dict):
            plan["based_on"] = basis_hash(load_board(project_id))
            conn.execute(
                "UPDATE projects SET continuity_json = ? WHERE id = ?",
                (json.dumps(plan, ensure_ascii=False), project_id),
            )
            conn.commit()
    finally:
        conn.close()
    if not fresh_clip_ids:
        return 0
    try:
        states = await client_prompt_states(project_id, fresh_clip_ids)
    except Exception as exc:
        logger.warning("Could not re-stamp imported prompt drafts (%s)", exc)
        return 0
    conn = get_db(settings.db_path)
    kept = 0
    try:
        for state in states:
            row = conn.execute(
                "SELECT video_settings_json FROM clips WHERE id = ?", (state["clip_id"],)
            ).fetchone()
            vs = _json(row["video_settings_json"]) if row else None
            if not isinstance(vs, dict) or not vs.get("prompt_draft") or not state.get("based_on"):
                continue
            meta = dict(vs.get("prompt_draft_meta") or {})
            meta["based_on"] = state["based_on"]
            vs["prompt_draft_meta"] = meta
            conn.execute(
                "UPDATE clips SET video_settings_json = ? WHERE id = ?",
                (json.dumps(vs, ensure_ascii=False), state["clip_id"]),
            )
            kept += 1
        conn.commit()
    finally:
        conn.close()
    return kept
