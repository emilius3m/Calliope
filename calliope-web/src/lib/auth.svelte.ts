export interface AuthUser {
  username: string;
}
interface AuthStatus {
  enabled: boolean;
  setup_required: boolean;
  user: AuthUser | null;
}

export const auth = $state({
  ready: false,
  enabled: true,
  setupRequired: false,
  user: null as AuthUser | null,
  error: "",
});
let revision = 0;

export function expireAuth() {
  revision++;
  if (auth.enabled) auth.user = null;
}

export async function authenticatedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
) {
  const headers = new Headers(init?.headers);
  headers.set("X-Calliope-Request", "1");
  const response = await fetch(input, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (response.status === 401) expireAuth();
  return response;
}

export async function refreshAuth() {
  const startedAtRevision = revision;
  try {
    const res = await fetch("/api/auth/status", {
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!res.ok) throw new Error("Cannot reach the Calliope server.");
    const status: AuthStatus = await res.json();
    if (startedAtRevision !== revision) return;
    auth.enabled = status.enabled;
    auth.setupRequired = status.setup_required;
    auth.user = status.user;
    auth.error = "";
    auth.ready = true;
  } catch {
    if (startedAtRevision !== revision) return;
    auth.error = "Cannot reach the Calliope server.";
    // Fail closed: do not keep showing private content after a failed check.
    auth.ready = false;
  }
}

async function authRequest(path: string, body: Record<string, string> = {}) {
  revision++;
  try {
    const response = await fetch(`/api/auth/${path}`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-Calliope-Request": "1",
      },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(
        typeof error.detail === "string"
          ? error.detail
          : "Please check the supplied details.",
      );
    }
    return response.json();
  } finally {
    revision++;
  }
}

export const authentication = {
  async login(username: string, password: string, setupCode?: string) {
    await authRequest(setupCode === undefined ? "login" : "setup", {
      username,
      password,
      ...(setupCode === undefined ? {} : { setup_code: setupCode }),
    });
    await refreshAuth();
  },
  async logout() {
    await authRequest("logout");
    expireAuth();
  },
  async changePassword(currentPassword: string, newPassword: string) {
    await authRequest("password", {
      current_password: currentPassword,
      new_password: newPassword,
    });
    await refreshAuth();
  },
};
