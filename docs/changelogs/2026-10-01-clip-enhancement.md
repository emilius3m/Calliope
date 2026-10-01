# 2026-10-01 — Separate clip enhancement

The Video stage now has **Edit**, **Enhance** and **Film** views. Enhancement is a subsequent ComfyUI pass, with its own workflow and inputs saved per clip. It receives each clip's original video automatically and preserves the generation prompt, workflow, references and original media.

- **Enhance clip** processes the selected shot; **Enhance all** processes rendered shots in timeline order with the displayed setup, skipping missing sources and busy clips.
- Workflows with multiple video outputs expose a result selector. Only the selected video is downloaded as the enhanced version, excluding intermediate videos, comparison videos and stills.
- Original and enhanced previews appear side by side. Each shot has an Original/Enhanced choice for film assembly; completed enhancements are selected automatically.
- Regenerating an original invalidates its old enhancement. A late enhancement result is preserved for comparison without selecting it for a different original.
- Film jobs record their actual ordered input files, so changing versions invalidates the previous export. Existing 1080p export behavior is retained.
- Database migration is additive. Project archives, imports and moved asset folders preserve enhancement media and settings.
- The supplied Wan API workflow has corrected sampler connections, dynamic frame count/dimensions, direct audio links and current Wan/RIFE input names. Its original model requirements, crop and frame-rate settings are retained.

Validation: backend tests cover independent settings, batch source assignment, transactional enqueue, stale results, output selection, migration, archive round trips and film input selection. Frontend type checks and production build pass. Real GPU restoration is dependent on the workflow's models being installed.
