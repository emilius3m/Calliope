<script lang="ts">
  import { useQueryClient } from "@tanstack/svelte-query";
  import {
    assetUrl,
    jobsApi,
    projects,
    type Job,
    type Workflow,
  } from "$lib/api";
  import { t } from "$lib/i18n.svelte";
  import { toast } from "$lib/toast";
  import { normalizeInputRole } from "$lib/comfy/parser";
  import type { AssetOption } from "$lib/assetPicker";
  import type { FilmstripClip } from "./SceneFilmstrip.svelte";
  import SceneFilmstrip from "./SceneFilmstrip.svelte";
  import SafeMedia from "../SafeMedia.svelte";
  import ComfyDynamicForm from "../ComfyDynamicForm.svelte";
  import JobRow from "../JobRow.svelte";
  import Button from "../ui/Button.svelte";
  import Icon from "../ui/Icon.svelte";

  interface Props {
    projectId: number;
    clips: FilmstripClip[];
    selectedClipId: number | null;
    workflows: Workflow[];
    jobs: Job[];
    assetOptions: AssetOption[];
    statusOfClip: (id: number) => string;
    thumbForClip: (
      id: number,
    ) => { kind: "image" | "video"; src: string } | null;
    formatClock: (sec: number) => string;
    onSelectClip: (id: number) => void;
    onStep: (dir: -1 | 1) => void;
  }
  let {
    projectId,
    clips,
    selectedClipId,
    workflows,
    jobs,
    assetOptions,
    statusOfClip,
    thumbForClip,
    formatClock,
    onSelectClip,
    onStep,
  }: Props = $props();
  const client = useQueryClient();
  const eligible = $derived(
    workflows.filter(
      (w) =>
        w.kind === "video" &&
        w.is_enabled &&
        w.input_schema.some(
          (i) => normalizeInputRole(i.role ?? null) === "video",
        ) &&
        w.output_schema.some((o) => o.kind === "video"),
    ),
  );
  const entry = $derived(
    clips.find((c) => c.clip.id === selectedClipId) ?? null,
  );
  let workflowId = $state<number | null>(null);
  let values = $state<Record<string, string | number>>({});
  let outputId = $state("");
  let hydratedClip = $state<number | null>(null);
  let busy = $state(false);
  let choosingVersion = $state(false);
  const saveTimers = new Map<number, ReturnType<typeof setTimeout>>();
  const workflow = $derived(eligible.find((w) => w.id === workflowId) ?? null);
  const videoOutputs = $derived(
    workflow?.output_schema.filter((o) => o.kind === "video") ?? [],
  );
  const sourceNode = $derived(
    workflow?.input_schema.find(
      (i) => normalizeInputRole(i.role ?? null) === "video",
    ),
  );
  const editableInputs = $derived(
    (workflow?.input_schema ?? [])
      .filter((i) => i.nodeId !== sourceNode?.nodeId)
      .map((i) =>
        normalizeInputRole(i.role ?? null) === "prompt"
          ? { ...i, required: false }
          : i,
      ),
  );
  const validOutput = $derived(videoOutputs.some((o) => o.nodeId === outputId));
  const missingInput = $derived(
    editableInputs.some(
      (i) =>
        i.required &&
        (values[i.nodeId] === undefined ||
          String(values[i.nodeId]).trim() === ""),
    ),
  );
  const clipJobs = $derived(
    jobs
      .filter((j) => j.kind === "enhancement" && j.clip_id === selectedClipId)
      .sort((a, b) => b.id - a.id),
  );
  const activeJob = $derived(
    clipJobs.find((j) => j.status === "pending" || j.status === "running"),
  );
  const renderedCount = $derived(clips.filter((c) => c.clip.clip_path).length);
  const generationBusy = $derived(
    jobs.some(
      (j) =>
        j.clip_id === selectedClipId &&
        j.kind === "video" &&
        (j.status === "pending" || j.status === "running"),
    ),
  );
  const originalUrl = $derived(assetUrl(entry?.clip.clip_path));
  const enhancedUrl = $derived(assetUrl(entry?.clip.enhanced_path));

  $effect(() => {
    if (!entry || eligible.length === 0 || hydratedClip === entry.clip.id)
      return;
    const saved = entry.clip.enhancement_settings;
    const preferred =
      eligible.find((w) => w.id === saved?.workflow_id) ??
      eligible.find((w) => /enhance|restore|upscale/i.test(w.name)) ??
      eligible[0];
    workflowId = preferred.id;
    values = { ...(saved?.input_values ?? {}) };
    const outputs = preferred.output_schema.filter((o) => o.kind === "video");
    outputId = outputs.some((o) => o.nodeId === saved?.output_node_id)
      ? saved!.output_node_id!
      : (outputs.at(-1)?.nodeId ?? "");
    hydratedClip = entry.clip.id;
  });

  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["jobs", projectId] }),
      client.invalidateQueries({ queryKey: ["scenes", projectId] }),
    ]);
  }

  function saveSetup() {
    if (!entry || workflowId == null) return;
    // Capture the clip and its own setup now, before a user switches shots.
    const id = entry.clip.id;
    const setup = {
      workflow_id: workflowId,
      input_values: { ...values },
      output_node_id: outputId,
    };
    clearTimeout(saveTimers.get(id));
    saveTimers.set(
      id,
      setTimeout(() => {
        saveTimers.delete(id);
        projects
          .updateClip(projectId, id, { enhancement_settings: setup })
          .then(() =>
            client.invalidateQueries({ queryKey: ["scenes", projectId] }),
          )
          .catch((err) => toast.error(String(err)));
      }, 350),
    );
  }

  function changeWorkflow(id: number) {
    workflowId = id;
    values = {};
    outputId =
      eligible
        .find((w) => w.id === id)
        ?.output_schema.filter((o) => o.kind === "video")
        .at(-1)?.nodeId ?? "";
    saveSetup();
  }

  async function enhance(all: boolean) {
    if (!workflow || !validOutput || busy || (!all && !entry)) return;
    busy = true;
    for (const [id, timer] of saveTimers) {
      if (all || id === entry?.clip.id) {
        clearTimeout(timer);
        saveTimers.delete(id);
      }
    }
    try {
      const res = await jobsApi.enhanceVideos(projectId, {
        workflow_id: workflow.id,
        input_values: { ...values },
        output_node_id: outputId,
        clip_ids: all ? undefined : [entry!.clip.id],
      });
      await refresh();
      toast.success(t("enhance.queued", { count: res.jobs.length }));
      if (res.skipped.length)
        toast.info(t("enhance.skipped", { count: res.skipped.length }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      busy = false;
    }
  }

  async function selectVersion(enhanced: boolean) {
    if (!entry || choosingVersion) return;
    choosingVersion = true;
    try {
      await projects.updateClip(projectId, entry.clip.id, {
        use_enhanced: enhanced,
      });
      await refresh();
    } catch (err) {
      toast.error(String(err));
    } finally {
      choosingVersion = false;
    }
  }
</script>

<section class="enhancement">
  <div class="intro">
    <div>
      <h3>{t("enhance.title")}</h3>
      <p>{t("enhance.lead")}</p>
    </div>
    <span class="count"
      >{t("enhance.rendered", {
        count: renderedCount,
        total: clips.length,
      })}</span
    >
  </div>
  <div class="workspace">
    <div class="comparison">
      <div class="players">
        <article>
          <h4>{t("enhance.original")} <span>{entry?.label ?? ""}</span></h4>
          <div class="player">
            {#if originalUrl}<SafeMedia
                src={originalUrl}
                kind="video"
                label={t("enhance.unavailable")}
              />
            {:else}<p>{t("enhance.generateFirst")}</p>{/if}
          </div>
          {#if originalUrl}<a href={originalUrl} download
              >{t("enhance.downloadOriginal")}</a
            >{/if}
        </article>
        <article>
          <h4>{t("enhance.result")}</h4>
          <div class="player">
            {#if enhancedUrl}<SafeMedia
                src={enhancedUrl}
                kind="video"
                label={t("enhance.unavailable")}
              />
            {:else}<p>
                {activeJob ? t("enhance.processing") : t("enhance.noResult")}
              </p>{/if}
          </div>
          {#if enhancedUrl}<a href={enhancedUrl} download
              >{t("enhance.downloadResult")}</a
            >{/if}
        </article>
      </div>
      {#if entry?.clip.enhanced_path && !entry.clip.enhancement_current}
        <p class="notice">{t("enhance.stale")}</p>
      {/if}
      {#if entry}
        <div class="version">
          <span>{t("enhance.filmVersion")}</span>
          <Button
            size="sm"
            variant={!entry.clip.use_enhanced ? "primary" : "secondary"}
            disabled={choosingVersion}
            onclick={() => selectVersion(false)}>{t("enhance.original")}</Button
          >
          <Button
            size="sm"
            variant={entry.clip.use_enhanced ? "primary" : "secondary"}
            disabled={choosingVersion || !entry.clip.enhancement_current}
            onclick={() => selectVersion(true)}>{t("enhance.result")}</Button
          >
        </div>
      {/if}
      <SceneFilmstrip
        {clips}
        {selectedClipId}
        {statusOfClip}
        {thumbForClip}
        {formatClock}
        {onSelectClip}
        {onStep}
      />
      {#each clipJobs.slice(0, 3) as job (job.id)}<JobRow
          {job}
          label={t("enhance.jobLabel", { id: job.id })}
        />{/each}
    </div>
    <aside>
      {#if eligible.length === 0}
        <h4>{t("enhance.noWorkflow")}</h4>
        <p>{t("enhance.workflowHint")}</p>
        <a href="/settings?tab=workflows">{t("enhance.openWorkflows")}</a>
      {:else}
        <label
          >{t("enhance.workflow")}<select
            value={workflowId ?? ""}
            onchange={(e) => changeWorkflow(Number(e.currentTarget.value))}
          >
            {#each eligible as w (w.id)}<option value={w.id}>{w.name}</option
              >{/each}
          </select></label
        >
        <div class="source">
          <Icon name="video" size={18} />
          <p>{t("enhance.source", { label: entry?.label ?? "—" })}</p>
        </div>
        {#if videoOutputs.length > 1}
          <label
            >{t("enhance.output")}<select
              value={outputId}
              onchange={(e) => {
                outputId = e.currentTarget.value;
                saveSetup();
              }}
            >
              {#each videoOutputs as output (output.nodeId)}<option
                  value={output.nodeId}>{output.label}</option
                >{/each}
            </select></label
          >
        {/if}
        <ComfyDynamicForm
          inputs={editableInputs}
          bind:values
          {assetOptions}
          allowUpload
          quiet
          onChange={saveSetup}
        />
        <p class="hint">{t("enhance.promptHint")}</p>
        <div class="buttons">
          <Button
            variant="primary"
            loading={busy}
            disabled={!entry?.clip.clip_path ||
              Boolean(activeJob) ||
              generationBusy ||
              missingInput ||
              !validOutput}
            onclick={() => enhance(false)}
            ><Icon name="sparkle" size={14} />{t("enhance.one")}</Button
          >
          <Button
            disabled={busy ||
              renderedCount === 0 ||
              missingInput ||
              !validOutput}
            onclick={() => enhance(true)}>{t("enhance.all")}</Button
          >
        </div>
        <p class="hint">{t("enhance.batchHint")}</p>
        <p class="hint">{t("enhance.exportHint")}</p>
      {/if}
    </aside>
  </div>
</section>

<style>
  .enhancement {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 16px 20px;
  }
  .intro {
    display: flex;
    gap: 16px;
    justify-content: space-between;
    align-items: start;
    margin-bottom: 20px;
  }
  h3,
  h4,
  p {
    margin: 0;
  }
  h3 {
    font-size: 18px;
    margin-bottom: 6px;
  }
  .intro p,
  .hint,
  aside p {
    color: var(--text-secondary);
    font-size: 13px;
    line-height: 1.5;
  }
  .count {
    color: var(--text-muted);
    font-size: 12px;
    white-space: nowrap;
  }
  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 340px;
    gap: 20px;
  }
  .comparison {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .players {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
  }
  article {
    min-width: 0;
  }
  h4 {
    font-size: 13px;
    margin-bottom: 8px;
  }
  h4 span {
    color: var(--text-muted);
    margin-left: 8px;
  }
  .player {
    aspect-ratio: 16 / 9;
    background: #050508;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }
  .player :global(video) {
    width: 100%;
    height: 100%;
    object-fit: contain;
  }
  .player p {
    color: var(--text-muted);
    font-size: 13px;
    padding: 20px;
    text-align: center;
  }
  a {
    display: inline-block;
    color: var(--accent);
    font-size: 12px;
    margin-top: 8px;
  }
  aside {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    align-self: start;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 7px;
    font-size: 12px;
    color: var(--text-secondary);
  }
  select {
    width: 100%;
    min-height: 36px;
    color: var(--text-primary);
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 7px;
    font: inherit;
  }
  .source {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 12px;
    background: var(--bg-elevated);
    border-radius: var(--radius-sm);
  }
  .version {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    font-size: 13px;
  }
  .version span {
    margin-right: 8px;
    color: var(--text-secondary);
  }
  .buttons {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .notice {
    color: var(--warning);
    font-size: 13px;
  }
  @media (max-width: 1100px) {
    .workspace {
      grid-template-columns: minmax(0, 1fr);
    }
    aside {
      width: 100%;
      box-sizing: border-box;
    }
  }
  @media (max-width: 680px) {
    .players {
      grid-template-columns: 1fr;
    }
    .intro {
      flex-direction: column;
    }
  }
</style>
