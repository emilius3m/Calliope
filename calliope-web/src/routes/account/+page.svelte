<script lang="ts">
  import AppHeader from "$lib/components/AppHeader.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import { auth, authentication } from "$lib/auth.svelte";
  import { t } from "$lib/i18n.svelte";
  let currentPassword = $state("");
  let newPassword = $state("");
  let confirmation = $state("");
  let busy = $state(false);
  let error = $state("");
  let saved = $state(false);
  async function submit(event: SubmitEvent) {
    event.preventDefault();
    error = "";
    saved = false;
    if (newPassword !== confirmation) {
      error = t("auth.passwordMismatch");
      return;
    }
    busy = true;
    try {
      await authentication.changePassword(currentPassword, newPassword);
      currentPassword = "";
      newPassword = "";
      confirmation = "";
      saved = true;
    } catch (e) {
      error = e instanceof Error ? e.message : t("auth.failed");
    } finally {
      busy = false;
    }
  }
</script>

<svelte:head><title>{t("auth.accountTitle")} — Calliope</title></svelte:head>
<AppHeader crumb={t("auth.accountTitle")} />
<main>
  <h1>{t("auth.accountTitle")}</h1>
  {#if auth.enabled && auth.user}
    <p class="username">{auth.user.username}</p>
    <section>
      <h2>{t("auth.changePassword")}</h2>
      <p>{t("auth.changeLead")}</p>
      <form onsubmit={submit}>
        <label class="field"
          ><span class="field-label">{t("auth.currentPassword")}</span><input
            class="field-input"
            bind:value={currentPassword}
            type="password"
            autocomplete="current-password"
            required
            maxlength="512"
          /></label
        >
        <label class="field"
          ><span class="field-label">{t("auth.newPassword")}</span><input
            class="field-input"
            bind:value={newPassword}
            type="password"
            autocomplete="new-password"
            required
            minlength="12"
            maxlength="512"
          /><span class="field-hint">{t("auth.passwordHint")}</span></label
        >
        <label class="field"
          ><span class="field-label">{t("auth.confirmPassword")}</span><input
            class="field-input"
            bind:value={confirmation}
            type="password"
            autocomplete="new-password"
            required
            minlength="12"
            maxlength="512"
          /></label
        >
        {#if error}<p class="error" role="alert">{error}</p>{/if}
        {#if saved}<p class="success" role="status">
            {t("auth.passwordSaved")}
          </p>{/if}
        <Button variant="primary" type="submit" loading={busy}
          >{t("auth.savePassword")}</Button
        >
      </form>
    </section>
  {:else}<p>{t("auth.disabled")}</p>{/if}
</main>

<style>
  main {
    max-width: 520px;
    padding: 32px 24px;
    margin: 0 auto;
  }
  h1 {
    font-size: 28px;
  }
  h2 {
    font-size: 20px;
  }
  section {
    margin-top: 28px;
    padding: 24px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: var(--bg-surface);
  }
  p {
    color: var(--text-secondary);
    line-height: 1.6;
  }
  .username {
    color: var(--accent);
  }
  form {
    margin-top: 24px;
  }
  .error {
    color: var(--error);
  }
  .success {
    color: var(--success);
  }
</style>
