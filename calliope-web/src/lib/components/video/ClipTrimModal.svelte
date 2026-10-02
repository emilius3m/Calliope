<script lang="ts">
	/**
	 * ClipTrimModal — non-destructive in/out points for one rendered clip.
	 * The file stays untouched: the film export plays only the kept range,
	 * and a re-render of the clip retires the trim on the server.
	 */
	import { untrack } from 'svelte';
	import { useQueryClient } from '@tanstack/svelte-query';
	import { assetUrl, projects, type Clip } from '$lib/api';
	import Button from '$lib/components/ui/Button.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';

	interface Props {
		open?: boolean;
		projectId: number;
		clip: Clip | null;
		label?: string;
	}

	let { open = $bindable(false), projectId, clip, label }: Props = $props();

	const MIN_KEEP = 0.5;
	const client = useQueryClient();

	let video = $state<HTMLVideoElement | null>(null);
	let duration = $state(0);
	let start = $state(0);
	let end = $state(0);
	let now = $state(0);
	let previewing = $state(false);
	let busy = $state(false);

	const src = $derived(clip ? assetUrl(clip.film_path ?? clip.clip_path) : null);
	const kept = $derived(Math.max(0, end - start));
	const whole = $derived(duration > 0 && start <= 0.01 && end >= duration - 0.01);
	const valid = $derived(duration > 0 && kept >= MIN_KEEP);
	const pct = (sec: number) => (duration > 0 ? `${(sec / duration) * 100}%` : '0%');

	const clipId = $derived(clip?.id ?? null);

	// Reset to the saved trim when the dialog opens on a clip — not on every
	// scenes refetch, which hands us a fresh clip object and would wipe edits.
	$effect(() => {
		if (!open || clipId == null) return;
		untrack(() => {
			start = clip?.trim?.start ?? 0;
			end = clip?.trim?.end ?? duration;
			previewing = false;
		});
	});

	// A different video needs its own metadata before the range is usable.
	$effect(() => {
		void src;
		untrack(() => (duration = 0));
	});

	function onLoaded() {
		if (!video) return;
		duration = video.duration || 0;
		if (!end || end > duration) end = duration;
		if (start >= end - MIN_KEEP) start = 0;
		video.currentTime = start;
	}

	function onTime() {
		if (!video) return;
		now = video.currentTime;
		if (previewing && now >= end) {
			video.pause();
			previewing = false;
		}
	}

	function seek(sec: number) {
		if (video) video.currentTime = sec;
	}

	function setStart(sec: number) {
		start = Math.max(0, Math.min(sec, end - MIN_KEEP));
		seek(start);
	}

	function setEnd(sec: number) {
		end = Math.min(duration, Math.max(sec, start + MIN_KEEP));
		seek(end);
	}

	function previewRange() {
		if (!video) return;
		video.currentTime = start;
		previewing = true;
		void video.play();
	}

	const fmt = (sec: number) => `${sec.toFixed(2)} s`;

	async function save(trim: { start: number; end: number } | null) {
		if (!clip) return;
		busy = true;
		try {
			await projects.updateClip(projectId, clip.id, { trim });
			await client.invalidateQueries({ queryKey: ['scenes', projectId] });
			toast.success(trim ? t('trim.saved', { kept: fmt(trim.end - trim.start) }) : t('trim.cleared'));
			open = false;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = false;
		}
	}
</script>

<Modal bind:open title={t('trim.title', { label: label ?? '' })} size="lg">
	{#if !clip || !src}
		<p class="muted">{t('trim.noVideo')}</p>
	{:else}
		<p class="lead">{t('trim.lead')}</p>
		<!-- svelte-ignore a11y_media_has_caption -->
		<video
			bind:this={video}
			class="player"
			{src}
			controls
			preload="metadata"
			onloadedmetadata={onLoaded}
			ontimeupdate={onTime}
			onpause={() => (previewing = false)}
		></video>

		<div class="track" aria-hidden="true">
			<div class="kept" style:left={pct(start)} style:width={pct(kept)}></div>
			<div class="head" style:left={pct(now)}></div>
		</div>

		<div class="ranges">
			<label class="field">
				<span class="field-label">{t('trim.start')} · {fmt(start)}</span>
				<input
					type="range"
					min="0"
					max={duration}
					step="0.05"
					value={start}
					disabled={!duration}
					oninput={(e) => setStart(Number(e.currentTarget.value))}
				/>
			</label>
			<label class="field">
				<span class="field-label">{t('trim.end')} · {fmt(end)}</span>
				<input
					type="range"
					min="0"
					max={duration}
					step="0.05"
					value={end}
					disabled={!duration}
					oninput={(e) => setEnd(Number(e.currentTarget.value))}
				/>
			</label>
		</div>

		<div class="actions">
			<Button size="sm" disabled={!duration} onclick={() => setStart(now)}>
				{t('trim.setStart')}
			</Button>
			<Button size="sm" disabled={!duration} onclick={() => setEnd(now)}>
				{t('trim.setEnd')}
			</Button>
			<Button size="sm" variant="ghost" disabled={!valid} onclick={previewRange}>
				<Icon name="play" size={14} />
				{t('trim.preview')}
			</Button>
			<span class="summary" role="status">
				{t('trim.summary', { kept: fmt(kept), total: fmt(duration) })}
			</span>
		</div>
	{/if}

	{#snippet footer()}
		<Button variant="ghost" disabled={busy || !clip?.trim} onclick={() => save(null)}>
			{t('trim.reset')}
		</Button>
		<span class="spacer"></span>
		<Button variant="secondary" disabled={busy} onclick={() => (open = false)}>
			{t('common.cancel')}
		</Button>
		<Button
			variant="primary"
			loading={busy}
			disabled={!valid}
			onclick={() => save(whole ? null : { start: Number(start.toFixed(3)), end: Number(end.toFixed(3)) })}
		>
			{t('trim.save')}
		</Button>
	{/snippet}
</Modal>

<style>
	.lead,
	.muted {
		margin: 0 0 12px;
		color: var(--text-secondary);
		font-size: 13px;
		line-height: 1.5;
	}
	.player {
		display: block;
		width: 100%;
		max-height: 52vh;
		background: #000;
		border-radius: var(--radius-md);
	}
	.track {
		position: relative;
		height: 8px;
		margin: 14px 0 6px;
		border-radius: 4px;
		background: var(--bg-elevated, var(--border));
	}
	.kept {
		position: absolute;
		top: 0;
		bottom: 0;
		border-radius: 4px;
		background: var(--accent);
		opacity: 0.75;
	}
	.head {
		position: absolute;
		top: -3px;
		width: 2px;
		height: 14px;
		background: var(--text-primary);
	}
	.ranges {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 16px;
	}
	.ranges input {
		width: 100%;
		accent-color: var(--accent);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin-top: 8px;
	}
	.summary {
		margin-left: auto;
		color: var(--text-secondary);
		font-size: 13px;
		font-variant-numeric: tabular-nums;
	}
	.spacer {
		flex: 1;
	}
	@media (max-width: 640px) {
		.ranges {
			grid-template-columns: 1fr;
		}
	}
</style>
