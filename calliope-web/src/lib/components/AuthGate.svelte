<script lang="ts">
  import { onMount, type Snippet } from "svelte";
  import { auth, authentication, refreshAuth } from "$lib/auth.svelte";
  import { t } from "$lib/i18n.svelte";
  import Button from "./ui/Button.svelte";
  import LanguageSwitcher from "./LanguageSwitcher.svelte";
  let { children }: { children: Snippet } = $props();
  let username = $state("");
  let password = $state("");
  let confirmation = $state("");
  let setupCode = $state("");
  let busy = $state(false);
  let error = $state("");

  onMount(() => {
    void refreshAuth();
    const timer = setInterval(() => {
      void refreshAuth();
    }, 30000);
    const visible = () => {
      if (document.visibilityState === "visible") void refreshAuth();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  });

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    error = "";
    if (auth.setupRequired && password !== confirmation) {
      error = t("auth.passwordMismatch");
      return;
    }
    busy = true;
    try {
      await authentication.login(
        username,
        password,
        auth.setupRequired ? setupCode : undefined,
      );
      password = "";
      confirmation = "";
      setupCode = "";
    } catch (e) {
      error = e instanceof Error ? e.message : t("auth.failed");
    } finally {
      busy = false;
    }
  }
</script>

{#if auth.ready && (!auth.enabled || auth.user)}
  {@render children()}
{:else}
  <main class="auth-shell">
    <div class="language"><LanguageSwitcher /></div>
    <section class="auth-card" aria-labelledby="auth-title">
      <a class="brand" href="/">Calliope <span>Lab</span></a>
      {#if auth.error}
        <h1 id="auth-title">{t("auth.unavailable")}</h1>
        <p role="alert">{t("auth.serverHint")}</p>
        <Button
          variant="primary"
          onclick={() => {
            void refreshAuth();
          }}>{t("auth.retry")}</Button
        >
      {:else if !auth.ready}
        <h1 id="auth-title">{t("auth.checking")}</h1>
      {:else}
        <h1 id="auth-title">
          {t(auth.setupRequired ? "auth.setupTitle" : "auth.loginTitle")}
        </h1>
        <p>{t(auth.setupRequired ? "auth.setupLead" : "auth.loginLead")}</p>
        <form onsubmit={submit}>
          {#if auth.setupRequired}
            <label class="field"
              ><span class="field-label">{t("auth.setupCode")}</span>
              <input
                class="field-input"
                bind:value={setupCode}
                type="password"
                required
                maxlength="256"
                autocomplete="off"
              />
              <span class="field-hint">{t("auth.setupHint")}</span>
            </label>
          {/if}
          <label class="field"
            ><span class="field-label">{t("auth.username")}</span>
            <input
              class="field-input"
              bind:value={username}
              required
              maxlength="64"
              pattern="[A-Za-z0-9_.@-]+"
              autocomplete="username"
              autocapitalize="none"
              spellcheck="false"
            />
          </label>
          <label class="field"
            ><span class="field-label">{t("auth.password")}</span>
            <input
              class="field-input"
              bind:value={password}
              type="password"
              required
              minlength={auth.setupRequired ? 12 : 1}
              maxlength="512"
              autocomplete={auth.setupRequired
                ? "new-password"
                : "current-password"}
            />
            {#if auth.setupRequired}<span class="field-hint"
                >{t("auth.passwordHint")}</span
              >{/if}
          </label>
          {#if auth.setupRequired}
            <label class="field"
              ><span class="field-label">{t("auth.confirmPassword")}</span>
              <input
                class="field-input"
                bind:value={confirmation}
                type="password"
                required
                minlength="12"
                maxlength="512"
                autocomplete="new-password"
              />
            </label>
          {/if}
          {#if error}<p class="error" role="alert">{error}</p>{/if}
          <Button type="submit" variant="primary" loading={busy}
            >{t(
              auth.setupRequired ? "auth.createAccount" : "auth.signIn",
            )}</Button
          >
        </form>
      {/if}
    </section>
  </main>
{/if}

<style>
  .auth-shell {
    min-height: 100dvh;
    display: grid;
    place-items: center;
    padding: 32px 20px;
    box-sizing: border-box;
    background:
      radial-gradient(ellipse at top, var(--accent-glow), transparent 60%),
      var(--bg-primary);
  }
  .language {
    position: absolute;
    top: 20px;
    right: 24px;
  }
  .auth-card {
    width: 100%;
    max-width: 420px;
    padding: 32px;
    box-sizing: border-box;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
  }
  .brand {
    font-family: var(--font-display);
    font-size: 24px;
    font-weight: 700;
    color: var(--text-primary);
    text-decoration: none;
  }
  .brand span {
    color: var(--accent);
  }
  h1 {
    margin-top: 28px;
    font-size: 25px;
    letter-spacing: -0.02em;
  }
  p {
    color: var(--text-secondary);
    line-height: 1.6;
  }
  form {
    margin-top: 24px;
  }
  .field-hint {
    display: block;
    line-height: 1.5;
  }
  .error {
    color: var(--error);
  }
  @media (max-width: 480px) {
    .auth-card {
      padding: 24px;
    }
  }
</style>
