const BASE = "/api/v1";

function getAuthHeaders() {
  const token =
    typeof localStorage !== "undefined"
      ? localStorage.getItem("swissjob_token")
      : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || res.statusText);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

async function authRequest(path, options = {}) {
  return request(path, {
    ...options,
    headers: { ...getAuthHeaders(), ...options.headers },
  });
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
    // Use raw fetch — multipart boundary must be set by the browser
    const res = await fetch(`${BASE}/profile/cv`, {
      method: "POST",
      headers: getAuthHeaders(),
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
  generate(jobHash, docType, language = "en") {
    return authRequest("/documents/generate", {
      method: "POST",
      body: JSON.stringify({
        job_hash: jobHash,
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

export const matchApi = {
  analyze(topK = 20) {
    return authRequest("/match/analyze", {
      method: "POST",
      body: JSON.stringify({ top_k: topK }),
    });
  },

  getResults(params = {}) {
    const qs = new URLSearchParams();
    if (params.limit) qs.set("limit", params.limit);
    if (params.offset !== undefined) qs.set("offset", params.offset);
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
};
