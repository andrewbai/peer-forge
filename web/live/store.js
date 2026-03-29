const MAX_EVENT_ITEMS = 180;
const REFRESH_DEBOUNCE_MS = 220;

function mergeEvents(state, items) {
  if (!Array.isArray(items) || items.length === 0) {
    return;
  }
  const bySeq = new Map(state.events.map((item) => [item.seq, item]));
  for (const item of items) {
    if (!item || typeof item.seq !== "number") {
      continue;
    }
    bySeq.set(item.seq, item);
    state.eventCursor = Math.max(state.eventCursor, item.seq);
  }
  state.events = Array.from(bySeq.values())
    .sort((left, right) => left.seq - right.seq)
    .slice(-MAX_EVENT_ITEMS);
}

export function createStore(bootstrap) {
  const state = {
    bootstrap,
    dashboard: null,
    schema: null,
    agents: {
      claude: { tail: null, inspect: null },
      codex: { tail: null, inspect: null },
    },
    artifacts: {
      finalPlan: null,
      currentPackage: null,
      currentDiff: null,
    },
    selectedArtifact: "finalPlan",
    events: [],
    eventCursor: 0,
    connection: "connecting",
    notice: null,
    pendingCommand: "",
  };

  const listeners = new Set();
  let eventSource = null;
  let reconnectTimer = 0;
  let refreshTimer = 0;
  let refreshInFlight = false;
  let refreshQueued = false;

  function emit() {
    for (const listener of listeners) {
      listener(state);
    }
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(state);
    return () => listeners.delete(listener);
  }

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.bootstrap.token) {
      headers.set("X-Peer-Forge-Token", state.bootstrap.token);
    }
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const response = await fetch(path, {
      method: options.method || "GET",
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (!response.ok) {
      let payload = null;
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }
      const detail = payload && payload.error ? payload.error : `${response.status} ${response.statusText}`;
      throw new Error(detail);
    }
    return response.json();
  }

  async function refreshDashboard() {
    state.dashboard = await request("/dashboard");
  }

  async function refreshSchema() {
    state.schema = await request("/commands/schema");
  }

  async function refreshAgent(agent) {
    const [tail, inspect] = await Promise.all([
      request(`/agents/${agent}/tail?lines=60`),
      request(`/agents/${agent}/inspect`),
    ]);
    state.agents[agent] = { tail, inspect };
  }

  async function refreshArtifacts() {
    const [finalPlan, currentPackage, currentDiff] = await Promise.all([
      request("/artifacts/final-plan?lines=220"),
      request("/artifacts/current-package"),
      request("/artifacts/current-diff?lines=340"),
    ]);
    state.artifacts = { finalPlan, currentPackage, currentDiff };
  }

  async function fetchEvents(after = 0, limit = 100) {
    const payload = await request(`/events?after=${after}&limit=${limit}`);
    mergeEvents(state, payload.items || []);
  }

  async function syncSnapshot() {
    if (refreshInFlight) {
      refreshQueued = true;
      return;
    }
    refreshInFlight = true;
    try {
      await Promise.all([
        refreshDashboard(),
        refreshArtifacts(),
        refreshAgent("claude"),
        refreshAgent("codex"),
      ]);
    } finally {
      refreshInFlight = false;
      emit();
      if (refreshQueued) {
        refreshQueued = false;
        await syncSnapshot();
      }
    }
  }

  function scheduleSync() {
    if (refreshTimer) {
      window.clearTimeout(refreshTimer);
    }
    refreshTimer = window.setTimeout(() => {
      refreshTimer = 0;
      void syncSnapshot();
    }, REFRESH_DEBOUNCE_MS);
  }

  function setNotice(tone, text) {
    state.notice = text ? { tone, text } : null;
    emit();
  }

  function setArtifact(tab) {
    state.selectedArtifact = tab;
    emit();
  }

  function buildEventSourceUrl() {
    const streamUrl = state.bootstrap.eventsStreamUrl || `${window.location.origin}/events/stream`;
    const url = new URL(streamUrl, window.location.origin);
    if (state.bootstrap.token) {
      url.searchParams.set("token", state.bootstrap.token);
    }
    if (state.eventCursor) {
      url.searchParams.set("after", String(state.eventCursor));
    }
    return url.toString();
  }

  async function reconnectStream() {
    state.connection = "reconnecting";
    emit();
    try {
      await fetchEvents(state.eventCursor, 120);
    } catch (error) {
      setNotice("error", `Failed to reload missed events: ${error.message}`);
    }
    scheduleSync();
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = 0;
      openStream();
    }, 1500);
  }

  function closeStream() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  function openStream() {
    closeStream();
    eventSource = new EventSource(buildEventSourceUrl());
    state.connection = "connecting";
    emit();
    eventSource.addEventListener("supervisor", (message) => {
      try {
        const item = JSON.parse(message.data);
        mergeEvents(state, [item]);
        state.connection = "live";
        emit();
        scheduleSync();
      } catch (error) {
        setNotice("error", `Invalid event payload: ${error.message}`);
      }
    });
    eventSource.onopen = () => {
      state.connection = "live";
      emit();
    };
    eventSource.onerror = () => {
      closeStream();
      if (!reconnectTimer) {
        void reconnectStream();
      }
    };
  }

  async function sendCommand(command) {
    state.pendingCommand = command;
    emit();
    try {
      const result = await request("/commands", {
        method: "POST",
        body: { command, source: "web" },
      });
      setNotice("success", `Queued command ${result.request_id}`);
      return result;
    } catch (error) {
      setNotice("error", `Command failed: ${error.message}`);
      throw error;
    } finally {
      state.pendingCommand = "";
      emit();
    }
  }

  async function init() {
    try {
      await Promise.all([
        refreshDashboard(),
        refreshSchema(),
        refreshArtifacts(),
        refreshAgent("claude"),
        refreshAgent("codex"),
        fetchEvents(0, 120),
      ]);
      state.connection = "live";
      emit();
      openStream();
    } catch (error) {
      state.connection = "error";
      setNotice("error", `Initial load failed: ${error.message}`);
    }
  }

  function destroy() {
    closeStream();
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
    }
    if (refreshTimer) {
      window.clearTimeout(refreshTimer);
    }
  }

  return {
    state,
    subscribe,
    init,
    destroy,
    setArtifact,
    sendCommand,
  };
}
