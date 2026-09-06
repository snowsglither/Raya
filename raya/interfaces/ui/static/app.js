// RAYA V2 Cockpit — client-side logic (Phase 6, layout revised Chantier 17).
//
// This file OWNS: visibility, layout, animation, local navigation, temporary
// presentation state (consigne UI STATE VS CORE STATE). It never decides task
// lifecycle, permissions, memory, world truth, execution, or model routing —
// it only renders what the backend reports and forwards user intent to it.
//
// Chantier 17 : the chat now occupies the Cockpit's main surface once a
// conversation is actually happening (sending a message), with the orb as a
// floating presence that shrinks to the bottom-left — replacing the earlier
// design where Conversation was a same-sized side panel opened only by the
// "C" key. Tasks/World/Browser/Computer/Attention/Spatial remain exactly
// that: contextual side panels opened via their own shortcut or a real
// `ui.view_requested` event from the model (ui.show_view,
// raya/tools/catalog/ui_views.py) — never local text pattern-matching on
// what the user typed. "Conversation mode" (`#app.conversation-mode`, driven
// by `enterConversationMode()`/`closePanel("conversation")` below) is a pure
// UI-layout concern, orthogonal to `#app[data-presence]` (glow/animation) —
// it never touches session, memory, task, or World State.

(() => {
  "use strict";

  const SESSION_ID = (() => {
    let id = null;
    try { id = localStorage.getItem("raya_session_id"); } catch (_) { /* private mode etc. */ }
    if (!id) {
      id = "cockpit-" + Math.random().toString(36).slice(2, 10);
      try { localStorage.setItem("raya_session_id", id); } catch (_) { /* ignore */ }
    }
    return id;
  })();

  const api = (path, options) => fetch(`/api/session/${SESSION_ID}${path}`, options).then((r) => {
    if (r.status === 204) return null;
    return r.json();
  });

  const el = {
    app: document.getElementById("app"),
    stage: document.getElementById("stage"),
    statusWord: document.getElementById("status-word"),
    statusSub: document.getElementById("status-sub"),
    input: document.getElementById("text-input"),
    sendBtn: document.getElementById("send-btn"),
    micBtn: document.getElementById("mic-btn"),
    stopBtn: document.getElementById("stop-btn"),
    connBanner: document.getElementById("conn-banner"),
    panels: {
      conversation: document.getElementById("panel-conversation"),
      tasks: document.getElementById("panel-tasks"),
      world: document.getElementById("panel-world"),
      browser: document.getElementById("panel-browser"),
      computer: document.getElementById("panel-computer"),
      attention: document.getElementById("panel-attention"),
      spatial: document.getElementById("panel-spatial"),
    },
    conversationBody: document.getElementById("conversation-body"),
    tasksBody: document.getElementById("tasks-body"),
    worldBody: document.getElementById("world-body"),
    browserBody: document.getElementById("browser-body"),
    computerBody: document.getElementById("computer-body"),
    attentionBody: document.getElementById("attention-body"),
    spatialBody: document.getElementById("spatial-body"),
    spatialCanvas: document.getElementById("spatial-canvas"),
    confirmOverlay: document.getElementById("confirm-overlay"),
    confirmTitle: document.getElementById("confirm-title"),
    confirmReason: document.getElementById("confirm-reason"),
    confirmYes: document.getElementById("confirm-yes"),
    confirmNo: document.getElementById("confirm-no"),
  };

  const PRESENCE_LABELS = {
    idle: "Online",
    listening: "Listening",
    processing: "Working on it",
    speaking: "Speaking",
    working: "Working",
    interrupted: "Interrupted",
    waiting: "Waiting",
    needs_attention: "Needs your input",
    unavailable: "Unavailable",
  };

  function setPresence(state) {
    el.app.dataset.presence = state;
    el.statusWord.textContent = PRESENCE_LABELS[state] || state;
  }

  function openPanel(name) {
    const panel = el.panels[name];
    if (!panel) return;
    // One contextual surface at a time — keeps the cockpit calm instead of
    // stacking overlapping panels (consigne "SHOW LESS").
    for (const [otherName, otherPanel] of Object.entries(el.panels)) {
      if (otherName !== name && otherPanel.classList.contains("open")) closePanel(otherName);
    }
    panel.hidden = false;
    requestAnimationFrame(() => panel.classList.add("open"));
    el.stage.classList.add("dimmed");
  }

  function closePanel(name) {
    const panel = el.panels[name];
    if (!panel) return;
    panel.classList.remove("open");
    setTimeout(() => { panel.hidden = true; }, 260);
    if (!anyPanelOpen()) el.stage.classList.remove("dimmed");
    // Renderer lifecycle (consigne §17) : jamais un contexte WebGL qui vit
    // au-delà de la vue qui l'a ouvert — libéré dès que le panneau se ferme,
    // par quelque chemin que ce soit (scene.close, Escape, un autre panneau).
    if (name === "spatial") destroySpatialScene();
    // Chantier 17 : SEUL point de sortie de "conversation mode" (Escape,
    // le bouton fermer du panneau, un autre panneau qui en prend la place,
    // ou l'inactivité via resetInactivityTimer ci-dessous) — jamais dupliqué
    // ailleurs. Purement visuel : l'historique/la session ne sont jamais
    // touchés (consigne A7 : "aucune donnée n'est supprimée").
    if (name === "conversation" && conversationActive) {
      conversationActive = false;
      el.app.classList.remove("conversation-mode");
      if (inactivityTimer) { clearTimeout(inactivityTimer); inactivityTimer = null; }
    }
  }

  function anyPanelOpen() {
    return Object.values(el.panels).some((p) => p.classList.contains("open"));
  }

  function closeAllPanels() {
    Object.keys(el.panels).forEach(closePanel);
  }

  // ---------------- Conversation mode (Chantier 17) ----------------
  //
  // A purely visual layout state, orthogonal to presence: the orb shrinks to
  // a small floating presence bottom-left and the conversation panel becomes
  // the Cockpit's main full-bleed surface instead of a side card. Entered by
  // real conversational activity (sendMessage) or the model explicitly
  // showing the conversation view; exited by closePanel("conversation")
  // (Escape, close button, another panel taking over) or ~60s of no further
  // user-initiated activity. Never affects session/memory/task/World State —
  // those keep existing entirely independently of whether this is showing.

  let conversationActive = false;
  let inactivityTimer = null;
  const CONVERSATION_IDLE_TIMEOUT_MS = 60000;

  function enterConversationMode() {
    conversationActive = true;
    el.app.classList.add("conversation-mode");
    openPanel("conversation");
    resetInactivityTimer();
  }

  function resetInactivityTimer() {
    if (!conversationActive) return;
    if (inactivityTimer) clearTimeout(inactivityTimer);
    inactivityTimer = setTimeout(() => closePanel("conversation"), CONVERSATION_IDLE_TIMEOUT_MS);
  }

  // ---------------- Conversation ----------------

  function renderConversation(view) {
    el.conversationBody.innerHTML = "";
    if (!view.messages.length) {
      el.conversationBody.innerHTML = '<div class="empty-note">No conversation yet — say something.</div>';
      return;
    }
    for (const m of view.messages) {
      const div = document.createElement("div");
      div.className = `msg ${m.role === "user" ? "user" : "raya"}`;
      div.textContent = m.text;
      el.conversationBody.appendChild(div);
    }
    el.conversationBody.scrollTop = el.conversationBody.scrollHeight;
  }

  async function refreshConversation() {
    renderConversation(await api("/conversation"));
  }

  async function sendMessage(text, { viaVoice = false } = {}) {
    if (!text.trim()) return;
    el.input.value = "";
    // Chantier 17 : un échange réel EST l'intention d'entrer en conversation
    // mode (le chat devient la surface principale, l'orbe se réduit) —
    // inversion délibérée de la règle Phase 6/11 précédente ("C" ouvrait
    // seul la conversation). "C" n'a plus cette fonction (voir le listener
    // clavier plus bas) : la seule façon d'entrer en conversation reste
    // l'usage réel (envoyer un message), ou le modèle lui-même
    // (ui.show_view -> openViewByName, routé vers enterConversationMode()).
    enterConversationMode();
    const view = await api("/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    renderConversation(view);
    refreshPresence();
    resetInactivityTimer(); // l'échange est terminé -- 60s pleines à partir de maintenant
    // Phase 11 (addendum "reconnecter la réponse vocale du Cockpit") : ne
    // parle QUE quand ce tour a été déclenché par le micro — un message
    // tapé au clavier reste silencieux (symétrique à l'entrée : le mic
    // n'est utilisé que sur demande explicite du bouton, jamais imposé).
    // Système séparé et sans rapport avec la voix serveur Phase 5
    // (VoiceChannel/Whisper/Kokoro, haut-parleurs physiques de la machine) :
    // ceci parle uniquement dans CET onglet navigateur, via l'API native du
    // navigateur — jamais un second moteur TTS applicatif.
    if (viaVoice) speakLastReply(view);
  }

  function speakLastReply(view) {
    if (!("speechSynthesis" in window)) return; // dégradation honnête, pas d'erreur visible
    const last = view.messages[view.messages.length - 1];
    if (!last || last.role !== "raya" || !last.text) return;
    window.speechSynthesis.cancel(); // jamais chevaucher une lecture précédente
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(last.text));
  }

  // ---------------- Tasks ----------------

  function taskBadgeClass(state) {
    if (state === "RUNNING") return "running";
    if (state === "FAILED") return "failed";
    if (state === "COMPLETED" || state === "CANCELLED") return "completed";
    return "";
  }

  async function refreshTasks() {
    const view = await api("/tasks");
    el.tasksBody.innerHTML = "";
    if (!view.tasks.length) {
      el.tasksBody.innerHTML = '<div class="empty-note">No active tasks.</div>';
      return;
    }
    for (const t of view.tasks) {
      const row = document.createElement("div");
      row.className = "row";
      const pct = t.progress_percent != null ? `${Math.round(t.progress_percent)}%` : "";
      row.innerHTML = `
        <div class="row-title">${escapeHtml(t.objective)}</div>
        <div class="row-meta">
          <span class="badge ${taskBadgeClass(t.state)}">${t.state}</span>
          <span>${t.priority_name}</span>
          ${pct ? `<span>${pct}</span>` : ""}
        </div>
        ${t.current_step ? `<div class="row-meta">${escapeHtml(t.current_step)}</div>` : ""}
        <div class="row-actions" data-task="${t.id}"></div>
      `;
      const actions = row.querySelector(".row-actions");
      addTaskActionButtons(actions, t.id, t.state);
      el.tasksBody.appendChild(row);
    }
  }

  function addTaskActionButtons(container, taskId, state) {
    const buttons = [];
    if (state === "RUNNING") buttons.push(["pause", "Pause"]);
    if (state === "PAUSED") buttons.push(["resume", "Resume"]);
    if (state === "RUNNING" || state === "PAUSED" || state === "PENDING") buttons.push(["cancel", "Cancel"]);
    for (const [action, label] of buttons) {
      const btn = document.createElement("button");
      btn.textContent = label;
      btn.addEventListener("click", async () => {
        await api(`/tasks/${taskId}/${action}`, { method: "POST" });
        refreshTasks();
      });
      container.appendChild(btn);
    }
  }

  // ---------------- World / Browser / Computer ----------------

  async function refreshWorld() {
    const view = await api("/world");
    el.worldBody.innerHTML = "";
    if (!view.facts.length) {
      el.worldBody.innerHTML = '<div class="empty-note">Nothing known yet.</div>';
      return;
    }
    for (const f of view.facts) {
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `
        <div class="row-title">${escapeHtml(f.domain)}.${escapeHtml(f.key)}</div>
        <div class="row-meta">
          <span>${escapeHtml(String(f.value))}</span>
          ${f.is_stale ? '<span class="badge">stale</span>' : ""}
        </div>
      `;
      el.worldBody.appendChild(row);
    }
  }

  function renderActivity(container, view, emptyText) {
    container.innerHTML = "";
    if (!view.has_activity) {
      container.innerHTML = `<div class="empty-note">${emptyText}</div>`;
      return;
    }
    for (const a of view.activity) {
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `
        <div class="row-title">${escapeHtml(a.tool_name)}</div>
        <div class="row-meta"><span class="badge ${a.outcome === "SUCCESS" ? "running" : "failed"}">${escapeHtml(a.outcome)}</span></div>
      `;
      container.appendChild(row);
    }
  }

  async function refreshBrowser() {
    renderActivity(el.browserBody, await api("/browser"), "No recent browser activity.");
  }

  async function refreshComputer() {
    renderActivity(el.computerBody, await api("/computer"), "No recent computer activity.");
  }

  async function refreshAttention() {
    const view = await api("/attention");
    el.attentionBody.innerHTML = "";
    if (!view.decisions.length) {
      el.attentionBody.innerHTML = '<div class="empty-note">No recent attention decisions.</div>';
      return;
    }
    for (const d of view.decisions) {
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `<div class="row-title">${escapeHtml(d.decision)}</div><div class="row-meta">${escapeHtml(d.reasoning || "")}</div>`;
      el.attentionBody.appendChild(row);
    }
  }

  // ---------------- Confirmation ----------------

  function showConfirmation(view) {
    if (!view) { el.confirmOverlay.hidden = true; return; }
    el.confirmTitle.textContent = view.tool_name;
    el.confirmReason.textContent = view.reason || "";
    el.confirmOverlay.hidden = false;
  }

  async function refreshConfirmation() {
    showConfirmation(await api("/confirmation"));
  }

  async function resolveConfirmation(approved) {
    el.confirmOverlay.hidden = true;
    const view = await api("/confirmation/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved }),
    });
    renderConversation(view);
    refreshPresence();
  }

  // ---------------- Presence ----------------

  async function refreshPresence() {
    const view = await api("/presence");
    setPresence(view.state);
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  // ---------------- WebSocket (event-driven, no polling loop) ----------------

  let ws = null;
  let reconnectDelay = 1000;

  function connectWs() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${scheme}://${location.host}/ws/${SESSION_ID}`);

    ws.onopen = () => {
      el.connBanner.classList.remove("visible");
      reconnectDelay = 1000;
    };

    ws.onclose = () => {
      el.connBanner.classList.add("visible");
      setTimeout(connectWs, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.6, 15000);
    };

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      handleNotification(msg);
    };
  }

  function handleNotification(msg) {
    switch (msg.type) {
      case "sync":
        // Resynchronisation (reconnect/refresh) : met à jour le contenu,
        // jamais l'ouverture du panneau — une reconnexion réseau n'est pas
        // une intention d'ouvrir Conversation (même règle que sendMessage).
        setPresence(msg.payload.presence.state);
        renderConversation(msg.payload.conversation);
        showConfirmation(msg.payload.confirmation);
        break;
      case "harness.confirmation_required":
        refreshConfirmation();
        refreshPresence();
        break;
      case "harness.confirmation_resolved":
        showConfirmation(null);
        refreshPresence();
        break;
      case "task.started":
      case "task.paused":
      case "task.resumed":
      case "task.completed":
      case "task.failed":
      case "task.cancelled":
        refreshPresence();
        if (!el.panels.tasks.hidden) refreshTasks();
        break;
      case "ui.view_requested":
        if (msg.payload.action === "hide") {
          if (msg.payload.view === "all") closeAllPanels();
          else closePanel(msg.payload.view);
        } else {
          openViewByName(msg.payload.view);
        }
        break;
      case "interface.stop_requested":
        refreshPresence();
        break;
      default:
        break;
    }
  }

  function openViewByName(view) {
    if (view === "conversation") { enterConversationMode(); refreshConversation(); }
    else if (view === "tasks") { openPanel("tasks"); refreshTasks(); }
    else if (view === "world") { openPanel("world"); refreshWorld(); }
    else if (view === "browser") { openPanel("browser"); refreshBrowser(); }
    else if (view === "computer") { openPanel("computer"); refreshComputer(); }
    else if (view === "attention") { openPanel("attention"); refreshAttention(); }
    else if (view === "spatial") { openPanel("spatial"); refreshSpatial(); }
  }

  // ---------------- Spatial (Phase 8 — Creative/Spatial Agent) ----------------
  //
  // This is ONE contextual view among many (consigne "pas le coeur de
  // l'identité de RAYA") — opened only via a real `ui.view_requested`
  // event triggered by the model calling scene.render, never on load and
  // never via a keyboard shortcut. The renderer library itself (Three.js)
  // is fetched from a CDN only the first time this view actually opens
  // (never unconditionally at page load, see test_ui_cockpit_static_files_
  // do_not_load_threejs_unconditionally) — this file is also the ONLY place
  // that knows the renderer is Three.js; the backend payload (from
  // raya.spatial.renderer.threejs_adapter.ThreeJSAdapter) is renderer-shaped
  // but renderer-agnostic on the wire (plain positions/rotations/scales).

  let threeLoadPromise = null;
  function loadThree() {
    if (window.THREE) return Promise.resolve();
    if (threeLoadPromise) return threeLoadPromise;
    threeLoadPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js";
      script.onload = () => resolve();
      script.onerror = () => { threeLoadPromise = null; reject(new Error("Renderer unavailable (CDN unreachable)")); };
      document.head.appendChild(script);
    });
    return threeLoadPromise;
  }

  let spatialState = null; // { renderer, scene, camera, animId } — null when nothing is mounted

  function buildGeometry(geometry) {
    const p = geometry.params || {};
    switch (geometry.kind) {
      case "sphere": return new THREE.SphereGeometry(p.radius || 1, 24, 16);
      case "cylinder": return new THREE.CylinderGeometry(p.radius_top ?? p.radius ?? 1, p.radius_bottom ?? p.radius ?? 1, p.height || 1, 24);
      case "cone": return new THREE.ConeGeometry(p.radius || 1, p.height || 1, 24);
      case "torus": return new THREE.TorusGeometry(p.radius || 1, p.tube || 0.3, 12, 24);
      case "plane": return new THREE.PlaneGeometry(p.width || 1, p.height || 1);
      case "group": return null; // pure hierarchy node — nothing to draw
      default: return new THREE.BoxGeometry(p.width || 1, p.height || 1, p.depth || 1);
    }
  }

  function mountSpatialScene(payload) {
    destroySpatialScene(); // never stack two live WebGL contexts on the same canvas
    const canvas = el.spatialCanvas;
    const width = canvas.clientWidth || 480;
    const height = canvas.clientHeight || 360;
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setSize(width, height, false);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 2000);
    camera.position.set(6, 5, 9);
    camera.lookAt(0, 0, 0);

    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.65);
    dirLight.position.set(6, 10, 6);
    scene.add(dirLight);

    for (const obj of payload.objects || []) {
      if (!obj.geometry) continue;
      const meshGeometry = buildGeometry(obj.geometry);
      if (!meshGeometry) continue;
      const color = obj.material ? obj.material.color : "#39a8ff";
      const opacity = obj.material ? obj.material.opacity : 1.0;
      const material = new THREE.MeshStandardMaterial({ color, opacity, transparent: opacity < 1 });
      const mesh = new THREE.Mesh(meshGeometry, material);
      mesh.position.set(obj.position[0], obj.position[1], obj.position[2]);
      mesh.rotation.set(
        (obj.rotation[0] * Math.PI) / 180,
        (obj.rotation[1] * Math.PI) / 180,
        (obj.rotation[2] * Math.PI) / 180,
      );
      mesh.scale.set(obj.scale[0], obj.scale[1], obj.scale[2]);
      scene.add(mesh);
    }

    let animId = null;
    function animate() {
      animId = requestAnimationFrame(animate);
      renderer.render(scene, camera);
    }
    animate();

    spatialState = { renderer, scene, camera, animId };
  }

  function destroySpatialScene() {
    if (!spatialState) return;
    cancelAnimationFrame(spatialState.animId);
    spatialState.scene.traverse((obj) => {
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) obj.material.dispose();
    });
    spatialState.renderer.dispose();
    spatialState = null;
  }

  async function refreshSpatial() {
    try {
      await loadThree();
    } catch (err) {
      el.spatialBody.innerHTML = `<div class="empty-note">${escapeHtml(err.message)}</div>`;
      return;
    }
    const view = await api("/spatial");
    if (!view.mounted || !view.payload) {
      destroySpatialScene();
      return;
    }
    mountSpatialScene(view.payload);
  }

  // ---------------- Wiring ----------------

  el.sendBtn.addEventListener("click", () => sendMessage(el.input.value));
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendMessage(el.input.value);
    if (e.key === "Escape") closeAllPanels();
  });
  el.stopBtn.addEventListener("click", () => api("/stop", { method: "POST" }));
  el.confirmYes.addEventListener("click", () => resolveConfirmation(true));
  el.confirmNo.addEventListener("click", () => resolveConfirmation(false));

  document.querySelectorAll("[data-close-panel]").forEach((btn) => {
    btn.addEventListener("click", () => closePanel(btn.dataset.closePanel));
  });

  // Keyboard shortcuts remain available (voice is primary, not mandatory).
  // Chantier 17 : "C" n'ouvre plus la conversation (elle s'ouvre désormais
  // d'elle-même dès qu'un échange réel a lieu, voir sendMessage) — inspecté
  // avant suppression (app.js Phase 6/11) : ouvrir/rafraîchir Conversation
  // était son SEUL comportement, rien d'autre n'en dépend.
  document.addEventListener("keydown", (e) => {
    if (e.target === el.input) return;
    if (e.key === "Escape") { closeAllPanels(); return; }
    if (e.key.toLowerCase() === "t") { openPanel("tasks"); refreshTasks(); }
    if (e.key.toLowerCase() === "w") { openPanel("world"); refreshWorld(); }
    if (e.key === "/") { el.input.focus(); e.preventDefault(); }
  });

  // Minimal Web Speech API mic affordance — voice-first, keyboard optional.
  // If unsupported, the mic button is disabled honestly rather than faking
  // a "listening" state (consigne NO FALSE UI CLAIMS).
  const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRecognitionImpl) {
    const recognizer = new SpeechRecognitionImpl();
    recognizer.continuous = false;
    recognizer.interimResults = false;
    let micActive = false;
    recognizer.onresult = (event) => {
      const text = event.results[0][0].transcript;
      sendMessage(text, { viaVoice: true });
    };
    recognizer.onend = () => { micActive = false; el.micBtn.classList.remove("active"); setPresence("idle"); refreshPresence(); };
    el.micBtn.addEventListener("click", () => {
      if (micActive) { recognizer.stop(); return; }
      micActive = true;
      el.micBtn.classList.add("active");
      setPresence("listening");
      recognizer.start();
    });
  } else {
    el.micBtn.disabled = true;
    el.micBtn.title = "Voice input not supported in this browser";
  }

  // Initial sync (in case the WS connect is briefly delayed).
  refreshPresence();
  connectWs();
})();
