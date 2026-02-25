const BASE = "/api/v1";

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
