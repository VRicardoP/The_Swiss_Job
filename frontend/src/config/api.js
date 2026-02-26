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
