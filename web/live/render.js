function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatTimestamp(value) {
  if (!value) {
    return "n/a";
  }
  return String(value).replace("T", " ").replace("Z", "");
}

function summarizeEvent(item) {
  const event = item.event || {};
  const type = event.type || "event";
  if (type === "turn-dispatched") {
    return `${event.turn_id || ""} dispatched`;
  }
  if (type === "turn-completed") {
    return `${event.turn_id || ""} completed`;
  }
  if (type === "command-processed") {
    return `${event.raw_command || ""} (${event.action || "ok"})`;
  }
  if (type === "boundary-entered") {
    return `${event.label || ""} -> ${event.next_phase || "n/a"}`;
  }
  if (type === "boundary-resumed") {
    return `${event.resolution || ""} -> ${event.next_phase || "n/a"}`;
  }
  if (type === "run-status-changed") {
    return `${event.previous_status || "n/a"} -> ${event.status || "n/a"}`;
  }
  if (type === "note-added") {
    return event.note?.summary || "";
  }
  if (type === "control-server-started") {
    return event.base_url || "";
  }
  if (type === "read-only-violation") {
    return event.message || "";
  }
  return JSON.stringify(event, null, 2);
}

function renderArtifactContent(state) {
  const tab = state.selectedArtifact;
  if (tab === "finalPlan") {
    const payload = state.artifacts.finalPlan;
    if (!payload) {
      return "Loading final plan...";
    }
    if (!payload.available) {
      return "Final plan is not available yet.";
    }
    if (payload.data) {
      return JSON.stringify(payload.data, null, 2);
    }
    return payload.preview || "(empty)";
  }
  if (tab === "currentPackage") {
    const payload = state.artifacts.currentPackage;
    if (!payload) {
      return "Loading package...";
    }
    if (!payload.available) {
      return "No current execution package is available yet.";
    }
    return JSON.stringify(payload, null, 2);
  }
  const payload = state.artifacts.currentDiff;
  if (!payload) {
    return "Loading diff...";
  }
  if (!payload.available) {
    return "Current diff is not available yet.";
  }
  return payload.preview || "(empty)";
}

function renderAgentMeta(payload) {
  if (!payload) {
    return `<div class="empty-state">Loading agent state...</div>`;
  }
  const inspect = payload.inspect;
  if (!inspect) {
    return `<div class="empty-state">Agent details unavailable.</div>`;
  }
  const pairs = [
    ["Mode", inspect.active ? (inspect.status === "completed" ? "completed" : inspect.status) : "inactive"],
    ["Runtime", inspect.runtime || "n/a"],
    ["Workspace", inspect.workspace || "n/a"],
    ["Phase", inspect.phase || "n/a"],
    ["Turn", inspect.turn_id || "n/a"],
    ["Error", inspect.parse_error || "none"],
  ];
  return pairs
    .map(
      ([label, value]) =>
        `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`,
    )
    .join("");
}

function renderTurnList(dashboard) {
  if (!dashboard || !Array.isArray(dashboard.turns) || dashboard.turns.length === 0) {
    return `<li class="empty-state">No turns yet.</li>`;
  }
  const currentTurnId = dashboard.current_turn?.id || "";
  return dashboard.turns
    .map((turn) => {
      const isCurrent = turn.id === currentTurnId ? " is-current" : "";
      return `
        <li class="turn-item${isCurrent}">
          <div class="turn-head">
            <span class="turn-title">${escapeHtml(turn.id)}</span>
            <span class="pill status-pill" data-state="${escapeHtml(turn.status)}">${escapeHtml(turn.status)}</span>
          </div>
          <div class="turn-summary">${escapeHtml(turn.summary || turn.phase)}</div>
          <div class="turn-meta">
            ${escapeHtml(turn.phase)} | ${escapeHtml(turn.mode || "n/a")} | ${escapeHtml((turn.active_agents || []).join(", ") || "n/a")}
          </div>
        </li>
      `;
    })
    .join("");
}

function renderEvents(events) {
  if (!events || events.length === 0) {
    return `<div class="empty-state">No events yet.</div>`;
  }
  return [...events]
    .reverse()
    .map(
      (item) => `
        <article class="event-item">
          <div class="event-head">
            <span class="event-title">${escapeHtml(item.event?.type || "event")}</span>
            <span class="event-meta">#${escapeHtml(item.seq)} | ${escapeHtml(formatTimestamp(item.event?.timestamp || ""))}</span>
          </div>
          <pre class="event-body">${escapeHtml(summarizeEvent(item))}</pre>
        </article>
      `,
    )
    .join("");
}

export function renderApp(root, state) {
  const dashboard = state.dashboard;
  const run = dashboard?.run || {};
  const summary = dashboard?.summary || {};
  const boundary = dashboard?.boundary || {};

  root.querySelector("[data-connection-pill]").textContent = state.connection;
  root.querySelector("[data-connection-pill]").dataset.state = state.connection;
  root.querySelector("[data-run-status-pill]").textContent = run.status || "unknown";
  root.querySelector("[data-run-status-pill]").dataset.state = run.status || "unknown";
  root.querySelector("[data-run-id]").textContent = run.run_id || "n/a";
  root.querySelector("[data-current-phase]").textContent = run.current_phase || "n/a";
  root.querySelector("[data-session-name]").textContent = run.session_name || "n/a";
  root.querySelector("[data-transport]").textContent = run.transport || "n/a";
  root.querySelector("[data-selected-executor]").textContent = summary.selected_executor || "n/a";
  root.querySelector("[data-selected-reviewer]").textContent = summary.selected_reviewer || "n/a";

  const flash = root.querySelector("[data-flash-banner]");
  if (state.notice?.text) {
    flash.classList.remove("is-hidden");
    flash.dataset.tone = state.notice.tone || "info";
    flash.textContent = state.notice.text;
  } else {
    flash.classList.add("is-hidden");
    flash.textContent = "";
    flash.dataset.tone = "";
  }

  const boundaryBanner = root.querySelector("[data-boundary-banner]");
  if (boundary.active) {
    boundaryBanner.classList.remove("is-hidden");
    root.querySelector("[data-boundary-label]").textContent = boundary.label || "Boundary";
    root.querySelector("[data-boundary-next-phase]").textContent = `Next phase: ${boundary.next_phase || "n/a"}`;
    root.querySelector("[data-boundary-commands]").innerHTML = (boundary.allowed_commands || [])
      .map((command) => `<span class="command-chip">${escapeHtml(command)}</span>`)
      .join("");
  } else {
    boundaryBanner.classList.add("is-hidden");
  }

  root.querySelector("[data-turn-list]").innerHTML = renderTurnList(dashboard);
  root.querySelector("[data-event-feed]").innerHTML = renderEvents(state.events);
  root.querySelector("[data-artifact-content]").textContent = renderArtifactContent(state);

  const continueButton = root.querySelector("[data-continue-command]");
  continueButton.disabled = !boundary.active || Boolean(state.pendingCommand);

  const noteSubmit = root.querySelector("[data-note-submit]");
  const noteInput = root.querySelector("[data-note-input]");
  const noteValue = noteInput.value.trim();
  noteSubmit.disabled = !boundary.next_phase || !noteValue || Boolean(state.pendingCommand);

  for (const tab of root.querySelectorAll("[data-artifact-tab]")) {
    tab.classList.toggle("is-active", tab.dataset.artifactTab === state.selectedArtifact);
  }

  for (const agent of ["claude", "codex"]) {
    const payload = state.agents[agent];
    const inspect = payload.inspect;
    root.querySelector(`[data-agent-status="${agent}"]`).textContent = inspect?.status || "idle";
    root.querySelector(`[data-agent-status="${agent}"]`).dataset.state = inspect?.status || "idle";
    root.querySelector(`[data-agent-meta="${agent}"]`).innerHTML = renderAgentMeta(payload);
    root.querySelector(`[data-agent-tail="${agent}"]`).textContent = payload.tail?.tail || "Loading tail...";
  }
}
