import useAuthStore from "../stores/authStore";

const BASE = "/api/v1";

// C6: los únicos estados que significan «esta credencial ya no vale». Un 500,
// un 502 o un corte de red NO terminan la sesión: antes, cualquier error la
// cerraba y un fallo transitorio echaba al usuario.
const ESTADOS_QUE_CIERRAN_SESION = new Set([401, 403]);

export function endsSession(status) {
  return ESTADOS_QUE_CIERRAN_SESION.has(status);
}

function getAuthHeaders() {
  // El store es la única fuente: él lo persiste en localStorage y lo rehidrata
  // al arrancar. Leer aquí localStorage por separado era una segunda copia que
  // se quedaba atrás justo después de renovar el token.
  const token = useAuthStore.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Promesa compartida: N peticiones en vuelo que reciben 401 provocan UN refresh,
// no N. Se limpia al terminar para que el siguiente 401 pueda reintentarlo.
let refreshing = null;

function refreshSession() {
  if (!refreshing) {
    refreshing = (async () => {
      const rt = useAuthStore.getState().refreshToken;
      if (!rt) {
        const err = new Error("sin refresh token");
        err.sessionEnded = true;
        throw err;
      }
      try {
        const data = await authApi.refresh(rt);
        const store = useAuthStore.getState();
        store.setAuth(
          data.access_token,
          data.refresh_token,
          data.user ?? store.user,
        );
        return data;
      } catch (err) {
        // Sólo un 401/403 del propio refresh prueba que la credencial murió.
        // Si el refresh falla por red o por un 500, la sesión sigue viva.
        if (endsSession(err.status)) err.sessionEnded = true;
        throw err;
      }
    })().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

// Renueva y dice si se puede reintentar. Cierra sesión SOLO si el refresh
// demostró que la credencial ya no sirve.
async function renovarSesion() {
  try {
    await refreshSession();
    return true;
  } catch (err) {
    if (err.sessionEnded) useAuthStore.getState().logout();
    return false;
  }
}

// `fetch` autenticado en crudo, para las tres rutas que necesitan la Response
// entera (multipart, blob): sin esto, la sesión se caía a los 30 min justo en
// una subida de CV o una descarga de .ics.
async function authFetch(url, init = {}, _reintentado = false) {
  const res = await fetch(url, {
    ...init,
    headers: { ...getAuthHeaders(), ...init.headers },
  });
  if (res.status !== 401 || _reintentado) return res;
  if (!(await renovarSesion())) return res;
  return authFetch(url, init, true);
}

async function request(path, options = {}) {
  const { headers: optHeaders, raw, ...rest } = options;
  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: { "Content-Type": "application/json", ...optHeaders },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((e) => e.msg || JSON.stringify(e)).join("; ")
          : detail
            ? JSON.stringify(detail)
            : res.statusText;
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return undefined;
  return raw ? res.text() : res.json();
}

async function authRequest(path, options = {}) {
  const { _reintentado, ...rest } = options;
  try {
    return await request(path, {
      ...rest,
      headers: { ...getAuthHeaders(), ...rest.headers },
    });
  } catch (err) {
    if (err.status !== 401 || _reintentado) throw err;
    if (!(await renovarSesion())) throw err;
    // El reintento vuelve a leer la cabecera, que ya lleva el token nuevo.
    return authRequest(path, { ...rest, _reintentado: true });
  }
}

export const jobsApi = {
  search(params = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined && v !== "" && v !== false) {
        qs.set(k, v);
      }
    }
    const query = qs.toString();
    return request(`/jobs/search${query ? `?${query}` : ""}`);
  },

  getJob(hash) {
    return request(`/jobs/${hash}`);
  },

  getStats() {
    return request("/jobs/stats");
  },

  getSources() {
    return request("/jobs/sources");
  },
};

export const profileApi = {
  getProfile() {
    return authRequest("/profile");
  },

  updateProfile(data) {
    return authRequest("/profile", {
      method: "PUT",
      body: JSON.stringify(data),
    });
  },

  async uploadCV(file) {
    const formData = new FormData();
    formData.append("file", file);
    // authFetch y no authRequest: el boundary del multipart lo pone el
    // navegador, así que aquí NO se fija Content-Type.
    const res = await authFetch(`${BASE}/profile/cv`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const err = new Error(body.detail || res.statusText);
      err.status = res.status;
      throw err;
    }
    return res.json();
  },

  deleteCV() {
    return authRequest("/profile/cv", { method: "DELETE" });
  },
};

export const authApi = {
  register(email, password, gdpr_consent) {
    return request("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, gdpr_consent }),
    });
  },

  login(email, password) {
    return request("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },

  refresh(refresh_token) {
    return request("/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token }),
    });
  },

  getMe() {
    return authRequest("/auth/me");
  },
};

export const applicationsApi = {
  list(params = {}) {
    const qs = new URLSearchParams();
    if (params.status) qs.set("status", params.status);
    if (params.limit) qs.set("limit", params.limit);
    if (params.offset !== undefined) qs.set("offset", params.offset);
    const query = qs.toString();
    return authRequest(`/applications${query ? `?${query}` : ""}`);
  },

  create(jobHash, notes = null) {
    const body = { job_hash: jobHash };
    if (notes) body.notes = notes;
    return authRequest("/applications", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  update(id, data) {
    return authRequest(`/applications/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },

  remove(id) {
    return authRequest(`/applications/${id}`, { method: "DELETE" });
  },

  stats() {
    return authRequest("/applications/stats");
  },
};

export const searchesApi = {
  list(params = {}) {
    const qs = new URLSearchParams();
    if (params.limit) qs.set("limit", params.limit);
    if (params.offset !== undefined) qs.set("offset", params.offset);
    const query = qs.toString();
    return authRequest(`/searches${query ? `?${query}` : ""}`);
  },

  create(data) {
    return authRequest("/searches", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  update(id, data) {
    return authRequest(`/searches/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
  },

  remove(id) {
    return authRequest(`/searches/${id}`, { method: "DELETE" });
  },

  run(id) {
    return authRequest(`/searches/${id}/run`, { method: "POST" });
  },
};

export const notificationsApi = {
  list(params = {}) {
    const qs = new URLSearchParams();
    if (params.limit) qs.set("limit", params.limit);
    if (params.offset !== undefined) qs.set("offset", params.offset);
    const query = qs.toString();
    return authRequest(`/notifications${query ? `?${query}` : ""}`);
  },

  markRead(id) {
    return authRequest(`/notifications/${id}/read`, { method: "PUT" });
  },
};

export const documentsApi = {
  async generateAndFetch(jobHash, docType, language, operationId) {
    const result = await documentsApi.generate(jobHash, docType, language, operationId);
    if (result.status !== "delivered") return result;
    try {
      return await documentsApi.get(result.document_id);
    } catch (error) {
      // A receipt remains valid after deletion. Close this operation without
      // silently generating another document; other errors remain retryable.
      if (error.status === 404) return { ...result, status: "removed" };
      throw error;
    }
  },

  pending() {
    return authRequest("/documents/operations");
  },

  retry(operationId) {
    return authRequest(`/documents/operations/${operationId}/retry`, { method: "POST" });
  },
  get(documentId) {
    return authRequest(`/documents/item/${documentId}`);
  },

  page(cursor = null) {
    return authRequest(`/documents${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`);
  },
  generate(jobHash, docType, language = "en", operationId = null) {
    return authRequest("/documents/generate", {
      method: "POST",
      body: JSON.stringify({
        job_hash: jobHash,
        operation_id: operationId,
        doc_type: docType,
        language,
      }),
    });
  },

  listForJob(jobHash, docType = null) {
    const qs = new URLSearchParams();
    if (docType) qs.set("doc_type", docType);
    const query = qs.toString();
    return authRequest(`/documents/${jobHash}${query ? `?${query}` : ""}`);
  },

  remove(documentId) {
    return authRequest(`/documents/${documentId}`, { method: "DELETE" });
  },
};

export const analyticsApi = {
  analyze(params = {}) {
    return authRequest('/analytics/analyze', {
      method: 'POST',
      body: JSON.stringify({ min_rejected: params.min_rejected ?? 2 }),
    })
  },

  listSuggestions(statusFilter = 'pending') {
    return authRequest(`/analytics/suggestions?status_filter=${statusFilter}`)
  },

  reviewSuggestion(id, action) {
    return authRequest(`/analytics/suggestions/${id}/review`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
  },

  listFilters() {
    return authRequest('/analytics/filters')
  },

  createFilter(data) {
    return authRequest('/analytics/filters', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  },

  async deleteFilter(id) {
    const res = await authFetch(`${BASE}/analytics/filters/${id}`, {
      method: 'DELETE',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      const err = new Error(body.detail || res.statusText)
      err.status = res.status
      throw err
    }
    // 204 No Content — sin body
  },
}

export const matchApi = {
  analyze() {
    return authRequest("/match/analyze", {
      method: "POST",
      body: JSON.stringify({}),
    });
  },

  getResults(params = {}) {
    const qs = new URLSearchParams();
    if (params.limit) qs.set("limit", params.limit);
    if (params.offset !== undefined) qs.set("offset", params.offset);
    if (params.translate === false) qs.set("translate", "false");
    const query = qs.toString();
    return authRequest(`/match/results${query ? `?${query}` : ""}`);
  },

  getHistory(params = {}) {
    const qs = new URLSearchParams();
    if (params.limit) qs.set("limit", params.limit);
    if (params.offset !== undefined) qs.set("offset", params.offset);
    const query = qs.toString();
    return authRequest(`/match/history${query ? `?${query}` : ""}`);
  },

  submitFeedback(jobHash, feedback) {
    return authRequest(`/match/${jobHash}/feedback`, {
      method: "POST",
      body: JSON.stringify({ feedback }),
    });
  },

  submitImplicit(jobHash, action, durationMs = null) {
    const body = { action };
    if (durationMs !== null) body.duration_ms = durationMs;
    return authRequest(`/match/${jobHash}/implicit`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  clearFeedback(jobHash) {
    return authRequest(`/match/${jobHash}/feedback`, { method: "DELETE" });
  },

  getSaved(params = {}) {
    const qs = new URLSearchParams();
    if (params.limit) qs.set("limit", params.limit);
    if (params.offset !== undefined) qs.set("offset", params.offset);
    const query = qs.toString();
    return authRequest(`/match/saved${query ? `?${query}` : ""}`);
  },
};

// Watchlist API — colegios suizos vigilados
export const watchlistApi = {
  listSchools() {
    return authRequest("/watchlist/schools");
  },
  setStatus(jobHash, applicationStatus) {
    return authRequest(`/watchlist/match/${jobHash}/status`, {
      method: "POST",
      body: JSON.stringify({ application_status: applicationStatus }),
    });
  },
  generateDraft(jobHash, templateOverride = null) {
    return authRequest(`/watchlist/match/${jobHash}/draft`, {
      method: "POST",
      body: JSON.stringify(
        templateOverride ? { template_override: templateOverride } : {},
      ),
    });
  },
  // Devuelve el borrador guardado como texto plano (no JSON)
  getDraft(jobHash) {
    return authRequest(`/watchlist/match/${jobHash}/draft`, {
      raw: true, // text/plain, no JSON.parse
    });
  },
  calendarUrl(jobHash) {
    // El endpoint requiere Authorization, así que en lugar de exponerlo
    // como link directo el front lo descargará con fetch + blob.
    return `/watchlist/match/${jobHash}/calendar.ics`;
  },
  // Descarga el .ics como Blob respetando los headers de auth + manejo
  // de errores estándar. Reemplaza al fetch() directo que existía en
  // WatchlistPage y evitaba el wrapper de auth.
  async downloadIcs(jobHash) {
    const res = await authFetch(
      `${BASE}/watchlist/match/${jobHash}/calendar.ics`,
    );
    if (!res.ok) {
      const err = new Error(`HTTP ${res.status}: ${res.statusText}`);
      err.status = res.status;
      throw err;
    }
    return res.blob();
  },
};
