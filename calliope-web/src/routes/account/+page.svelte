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

  let tokenPassword = $state("");
  let tokenBusy = $state(false);
  let tokenError = $state("");
  let tokenNotice = $state("");
  let issued = $state<{ token: string; expires_at: number } | null>(null);
  let copied = $state("");
  const mcpUrl = $derived(
    typeof location === "undefined" ? "/mcp" : `${location.origin}/mcp`,
  );
  const command = $derived(
    issued
      ? `claude mcp add --transport http calliope ${mcpUrl} --header "Authorization: Bearer ${issued.token}"`
      : "",
  );
  async function tokenAction(revoke: boolean) {
    tokenError = "";
    tokenNotice = "";
    copied = "";
    issued = null;
    tokenBusy = true;
    try {
      if (revoke) {
        const { revoked } = await authentication.revokeTokens(tokenPassword);
        tokenNotice = t("auth.tokensRevoked", { count: revoked });
      } else {
        issued = await authentication.issueToken(tokenPassword);
      }
      tokenPassword = "";
    } catch (e) {
      tokenError = e instanceof Error ? e.message : t("auth.failed");
    } finally {
      tokenBusy = false;
    }
  }
  async function copy(which: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      copied = which;
    } catch {
      copied = "";
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
    <section>
      <h2>{t("auth.tokensTitle")}</h2>
      <p>{t("auth.tokensLead")}</p>
      <form
        onsubmit={(event) => {
          event.preventDefault();
          tokenAction(false);
        }}
      >
        <label class="field"
          ><span class="field-label">{t("auth.currentPassword")}</span><input
            class="field-input"
            bind:value={tokenPassword}
            type="password"
            autocomplete="current-password"
            required
            maxlength="512"
          /><span class="field-hint">{t("auth.tokenPasswordHint")}</span></label
        >
        {#if tokenError}<p class="error" role="alert">{tokenError}</p>{/if}
        {#if tokenNotice}<p class="success" role="status">{tokenNotice}</p>{/if}
        <div class="actions">
          <Button variant="primary" type="submit" loading={tokenBusy}
            >{t("auth.issueToken")}</Button
          >
          <Button
            variant="danger"
            type="button"
            disabled={tokenBusy || !tokenPassword}
            onclick={() => tokenAction(true)}>{t("auth.revokeTokens")}</Button
          >
        </div>
      </form>
      {#if issued}
        <div class="issued" role="status">
          <p class="warning">{t("auth.tokenOnce")}</p>
          <div class="copy-row">
            <input class="field-input mono" readonly value={issued.token} />
            <Button type="button" onclick={() => copy("token", issued!.token)}
              >{copied === "token" ? t("auth.copied") : t("auth.copy")}</Button
            >
          </div>
          <p class="hint">
            {t("auth.tokenExpires", {
              date: new Date(issued.expires_at * 1000).toLocaleString(),
            })}
          </p>
          <span class="field-label">{t("auth.tokenCommand")}</span>
          <div class="copy-row">
            <textarea class="field-input mono" readonly rows="3">{command}</textarea>
            <Button type="button" onclick={() => copy("command", command)}
              >{copied === "command" ? t("auth.copied") : t("auth.copy")}</Button
            >
          </div>
        </div>
      {/if}
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
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
  }
  .issued {
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
  }
  .warning {
    color: var(--warning, var(--accent));
  }
  .hint {
    font-size: 13px;
  }
  .copy-row {
    display: flex;
    gap: 8px;
    align-items: flex-start;
    margin: 8px 0 12px;
  }
  .mono {
    flex: 1;
    min-width: 0;
    font-family: var(--font-mono, monospace);
    font-size: 12px;
    resize: none;
  }
</style>
