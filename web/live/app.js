import { renderApp } from "/render.js";
import { createStore } from "/store.js";

const bootstrap = window.__PEER_FORGE_LIVE_BOOTSTRAP__ || {};
const store = createStore(bootstrap);

function wireCommands(root) {
  root.querySelector("[data-status-command]").addEventListener("click", async () => {
    await store.sendCommand("status");
  });

  root.querySelector("[data-continue-command]").addEventListener("click", async () => {
    if (!store.state.dashboard?.boundary?.active) {
      return;
    }
    await store.sendCommand("continue");
  });

  root.querySelector("[data-abort-command]").addEventListener("click", async () => {
    if (!window.confirm("Abort the current live run?")) {
      return;
    }
    await store.sendCommand("abort");
  });

  root.querySelector("[data-note-form]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = root.querySelector("[data-note-input]");
    const text = input.value.trim();
    if (!text) {
      return;
    }
    await store.sendCommand(`note both ${text}`);
    input.value = "";
    renderApp(root, store.state);
  });

  for (const tab of root.querySelectorAll("[data-artifact-tab]")) {
    tab.addEventListener("click", () => {
      store.setArtifact(tab.dataset.artifactTab);
    });
  }
}

function main() {
  const root = document.querySelector(".shell");
  if (!root) {
    return;
  }
  store.subscribe((state) => renderApp(root, state));
  wireCommands(root);
  void store.init();
  window.addEventListener("beforeunload", () => store.destroy(), { once: true });
}

main();
