"use strict";

const state = {
  ws: null,
  clientId: crypto.randomUUID(),
  voiceConnected: false,
  voiceConnecting: false,
  voiceWanted: false,
  voiceReconnectAttempts: 0,
  voiceReconnectTimer: null,
  maxVoiceReconnectAttempts: 1,
  peerConnection: null,
  playbackContext: null,
  micContext: null,
  micStream: null,
  micNode: null,
  micSink: null,
  // ---- Teams-only Voice Live session (independent from the page session) ----
  teamsWs: null,
  teamsClientId: null,
  teamsVoiceConnected: false,
  teamsVoiceConnecting: false,
  teamsPeerConnection: null,
  teamsLisaOutputStream: null,
  teamsLisaOutputAudioEl: null,
  teamsLisaOutputSource: null,
  teamsLisaAvatarVideoTrack: null,
  teamsBridgeDest: null,
  teamsSilentOsc: null,
  teamsSilentGain: null,
  teamsBridgeAudioReady: false,
  teamsPcmBridgeLogged: false,
  // ---- ACS Teams call ----
  teamsCallClient: null,
  teamsCallAgent: null,
  teamsCall: null,
  teamsLocalVideoStream: null,
  teamsCanvasInterval: null,
  teamsCanvasEls: null,
  teamsAvatarBackgroundImageUrl: "/background.png",
  teamsAvatarBackgroundImage: null,
  teamsVoiceLiveAvatarBackgroundImageUrl: "",
  teamsAvatarChromaKeyColor: "#00ff00",
  teamsAvatarChromaEnabled: true,
  teamsInboundAudioReady: false,
  teamsIncomingContext: null,
  teamsIncomingNode: null,
  teamsIncomingSink: null,
  remoteAudioHandler: null,
  remoteParticipantsHandler: null,
  teamsRemoteEmptyTimer: null,
  teamsRemoteEmptySince: 0,
  teamsAudioInputFirstFrameAt: 0,
  teamsAudioInputLastFrameAt: 0,
  teamsAudioInputSilenceTimer: null,
  teamsAudioInputSilenceLogged: false,
  teamsOnlyModeActive: false,
  backendReady: false,
  teamsLinkTouched: false,
  latestInviteUrl: "",
  candidateId: "",
  candidateName: "",
  autoJoinEnabled: false,
  autoJoinInFlight: false,
  autoJoinLastUrl: "",
  eventLog: [],
};

const TEAMS_REMOTE_EMPTY_GRACE_MS = 30000;

const els = {};

function $(id) { return document.getElementById(id); }

function log(message, metadata = {}) {
  const stamp = new Date().toLocaleTimeString();
  if (els.log) {
    els.log.textContent += `[${stamp}] ${message}\n`;
    els.log.scrollTop = els.log.scrollHeight;
  }
  const entry = {
    timestamp: nowIso(),
    message,
    event: metadata.event || "log",
    meetingUrl: els.teamsLink?.value?.trim() || state.latestInviteUrl || "",
    voiceConnected: state.voiceConnected,
    voiceConnecting: state.voiceConnecting,
    teamsState: state.teamsCall?.state || "",
    autoJoinEnabled: state.autoJoinEnabled,
    pageVisibility: document.visibilityState,
    metadata,
  };
  state.eventLog.push(entry);
  fetch("/api/client-log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entry),
    keepalive: true,
  }).catch(() => {});
}

function setPill(el, text, cls = "") {
  el.textContent = text;
  el.classList.remove("ok", "err");
  if (cls) el.classList.add(cls);
}

function setAutoState(text, cls = "off") {
  setPill(els.autoStatus, text, cls === "err" ? "err" : cls === "armed" || cls === "joined" || cls === "joining" ? "ok" : "");
  els.autoBanner.textContent = cls === "off" ? "Auto-join is off. Lisa joins only when you click Join Teams." : text;
  els.autoBanner.classList.remove("off", "armed", "err");
  els.autoBanner.classList.add(cls === "err" ? "err" : cls === "off" ? "off" : "armed");
}

function nowIso() {
  return new Date().toISOString();
}

function acsOriginProblem() {
  const host = location.hostname.toLowerCase();
  if (location.protocol === "https:" || location.protocol === "file:" || host === "localhost") return "";
  if (location.protocol === "http:" && (host === "127.0.0.1" || host === "::1" || host === "[::1]")) {
    return "Open the operator page as http://localhost:3000/ so the ACS Web Calling SDK can join Teams.";
  }
  return "ACS Web Calling SDK requires https, file:, or localhost.";
}

function redirectToAcsSafeLocalhost() {
  if (location.protocol !== "http:" || location.hostname !== "127.0.0.1") return false;
  const next = new URL(location.href);
  next.hostname = "localhost";
  location.replace(next.toString());
  return true;
}

function setPreflight(id, ok, detail) {
  const row = els[`preflight${id}`];
  if (!row) return;
  row.classList.remove("ok", "err", "pending");
  row.classList.add(ok ? "ok" : "err");
  row.querySelector("strong").textContent = ok ? `ready${detail ? ` - ${detail}` : ""}` : `blocked${detail ? ` - ${detail}` : ""}`;
}

function setPreflightPending(id) {
  const row = els[`preflight${id}`];
  if (!row) return;
  row.classList.remove("ok", "err");
  row.classList.add("pending");
  row.querySelector("strong").textContent = "checking";
}

async function checkVoiceLivePreflight() {
  const resp = await fetch("/api/preflight/voice-live", { cache: "no-store" });
  const data = await resp.json();
  setPreflight("Voice", Boolean(data.ok), data.ok ? `HTTP ${data.status || "reachable"}` : data.detail || "unreachable");
}

async function checkAcsPreflight() {
  const originProblem = acsOriginProblem();
  if (originProblem) {
    setPreflight("Acs", false, "use localhost");
    return;
  }
  const resp = await fetch("/api/acs-token", { cache: "no-store" });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try { detail = (await resp.json()).detail || detail; } catch (_) {}
    setPreflight("Acs", false, detail);
    return;
  }
  setPreflight("Acs", true, "token ok");
}

async function checkMicPreflight({ requestPermission = false } = {}) {
  if (!navigator.mediaDevices?.getUserMedia) {
    setPreflight("Mic", false, "unsupported");
    return;
  }
  try {
    if (navigator.permissions?.query) {
      const status = await navigator.permissions.query({ name: "microphone" });
      if (status.state === "granted") {
        setPreflight("Mic", true, "granted");
        return;
      }
      if (status.state === "denied") {
        setPreflight("Mic", false, "denied");
        return;
      }
    }
    if (!requestPermission) {
      setPreflight("Mic", false, "not granted");
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    stream.getTracks().forEach(track => track.stop());
    setPreflight("Mic", true, "granted");
  } catch (error) {
    setPreflight("Mic", false, error.name || error.message);
  }
}

function checkBrowserPreflight() {
  const missing = [];
  if (!window.RTCPeerConnection) missing.push("WebRTC");
  if (!window.AudioContext) missing.push("AudioContext");
  if (!window.AudioWorkletNode) missing.push("AudioWorklet");
  if (!window.WebSocket) missing.push("WebSocket");
  if (typeof AzureCommunicationCalling === "undefined") missing.push("ACS SDK");
  const originProblem = acsOriginProblem();
  if (originProblem) missing.push("origin");
  setPreflight("Browser", missing.length === 0, missing.join(", ") || "supported");
}

async function checkInvitePreflight() {
  const resp = await fetch("/api/latest-invite", { cache: "no-store" });
  if (!resp.ok) {
    setPreflight("Invite", false, `HTTP ${resp.status}`);
    return;
  }
  const invite = await resp.json();
  setPreflight("Invite", Boolean(invite.meeting_url), invite.meeting_url ? invite.candidate_name || "present" : "none yet");
}

async function runPreflight({ requestMic = false } = {}) {
  ["Voice", "Acs", "Mic", "Browser", "Invite"].forEach(setPreflightPending);
  log("Pre-flight checks started", { event: "preflight_started", requestMic });
  await Promise.all([
    checkVoiceLivePreflight().catch(error => setPreflight("Voice", false, error.message)),
    checkAcsPreflight().catch(error => setPreflight("Acs", false, error.message)),
    checkMicPreflight({ requestPermission: requestMic }).catch(error => setPreflight("Mic", false, error.message)),
    Promise.resolve().then(checkBrowserPreflight).catch(error => setPreflight("Browser", false, error.message)),
    checkInvitePreflight().catch(error => setPreflight("Invite", false, error.message)),
  ]);
  log("Pre-flight checks completed", { event: "preflight_completed", requestMic });
}

function logAutoJoinDecision({ meetingUrl, decision, outcome, reason = "", candidateName = "" }) {
  const safeUrl = meetingUrl || "";
  log(`AUTO ${decision} outcome=${outcome} url=${safeUrl || "none"}${reason ? ` reason=${reason}` : ""}`, {
    event: "auto_join_decision",
    decision,
    outcome,
    reason,
    candidateName,
  });
  fetch("/api/auto-join-log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      timestamp: nowIso(),
      meetingUrl: safeUrl,
      decision,
      outcome,
      reason,
      candidateName,
      autoJoinEnabled: state.autoJoinEnabled,
    }),
  }).catch(error => log(`Auto-join audit log failed: ${error.message}`));
}

function addMessage(role, text) {
  const item = document.createElement("div");
  item.className = `message ${role}`;
  const roleEl = document.createElement("span");
  roleEl.className = "role";
  roleEl.textContent = role === "assistant" ? "Lisa" : role;
  const textEl = document.createElement("span");
  textEl.textContent = `: ${text}`;
  item.append(roleEl, textEl);
  els.transcript.appendChild(item);
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function base64ToArrayBuffer(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

async function ensurePlaybackContext() {
  if (!state.playbackContext) {
    state.playbackContext = new AudioContext({ sampleRate: 24000 });
    log("Playback audio context created", { event: "audio_context_created" });
  }
  if (state.playbackContext.state === "suspended") {
    await state.playbackContext.resume();
    log(`Playback audio context resumed (${state.playbackContext.state})`, { event: "audio_context_resumed" });
  }
  return state.playbackContext;
}

async function checkBackendHealth({ throwOnError = false } = {}) {
  try {
    const resp = await fetch("/health", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    state.backendReady = true;
    return true;
  } catch (error) {
    state.backendReady = false;
    const message = `Local backend is not responding on ${location.origin}; restart the operator service and reload this page.`;
    log(`${message} (${error.message})`, { event: "backend_health_failed" });
    if (throwOnError) throw new Error(message);
    return false;
  }
}

function waitForCondition(predicate, timeoutMs, label) {
  const started = performance.now();
  return new Promise((resolve, reject) => {
    const timer = setInterval(() => {
      if (predicate()) {
        clearInterval(timer);
        resolve(true);
      } else if (performance.now() - started >= timeoutMs) {
        clearInterval(timer);
        reject(new Error(`${label} timed out after ${Math.round(timeoutMs / 1000)}s`));
      }
    }, 100);
  });
}

async function ensureVoiceConnectedForTeams() {
  // The page Lisa session is independent of Teams; we only need backend reachable
  // and an unlocked AudioContext (user gesture).
  await checkBackendHealth({ throwOnError: true });
  await ensurePlaybackContext();
}

async function ensureTeamsBridgeDestination() {
  if (state.teamsBridgeDest) return state.teamsBridgeDest;
  const ctx = await ensurePlaybackContext();
  state.teamsBridgeDest = ctx.createMediaStreamDestination();
  // A muted oscillator keeps the MediaStreamTrack "live" before Lisa speaks,
  // otherwise some browsers mark the track as muted and ACS may silence it.
  state.teamsSilentOsc = ctx.createOscillator();
  state.teamsSilentGain = ctx.createGain();
  state.teamsSilentGain.gain.value = 0;
  state.teamsSilentOsc.connect(state.teamsSilentGain);
  state.teamsSilentGain.connect(state.teamsBridgeDest);
  state.teamsSilentOsc.start();
  state.teamsPcmBridgeLogged = false;
  return state.teamsBridgeDest;
}

function cleanupTeamsIncomingAudio() {
  if (state.remoteAudioHandler && state.teamsCall) {
    try { state.teamsCall.off("remoteAudioStreamsUpdated", state.remoteAudioHandler); } catch (_) {}
  }
  state.remoteAudioHandler = null;
  if (state.teamsIncomingNode) {
    try { state.teamsIncomingNode.disconnect(); } catch (_) {}
    state.teamsIncomingNode = null;
  }
  if (state.teamsIncomingSink) {
    try { state.teamsIncomingSink.disconnect(); } catch (_) {}
    state.teamsIncomingSink = null;
  }
  if (state.teamsIncomingContext) {
    state.teamsIncomingContext.close().catch(() => {});
    state.teamsIncomingContext = null;
  }
  state.teamsInboundAudioReady = false;
}

function cleanupTeamsBridge() {
  if (state.teamsLisaOutputSource) {
    try { state.teamsLisaOutputSource.disconnect(); } catch (_) {}
    state.teamsLisaOutputSource = null;
  }
  if (state.teamsSilentOsc) {
    try { state.teamsSilentOsc.stop(); } catch (_) {}
    state.teamsSilentOsc = null;
  }
  if (state.teamsSilentGain) {
    try { state.teamsSilentGain.disconnect(); } catch (_) {}
    state.teamsSilentGain = null;
  }
  state.teamsBridgeDest = null;
  state.teamsBridgeAudioReady = false;
  state.teamsPcmBridgeLogged = false;
}

async function attachTeamsLisaAudioToTeams(reason = "ready") {
  if (!state.teamsLisaOutputStream || !state.teamsBridgeDest) return false;
  const ctx = await ensurePlaybackContext();
  if (state.teamsLisaOutputSource) {
    try { state.teamsLisaOutputSource.disconnect(); } catch (_) {}
  }
  state.teamsLisaOutputSource = ctx.createMediaStreamSource(state.teamsLisaOutputStream);
  if (state.teamsSilentGain) {
    try { state.teamsSilentGain.disconnect(state.teamsBridgeDest); } catch (_) {}
    state.teamsSilentGain = null;
  }
  state.teamsLisaOutputSource.connect(state.teamsBridgeDest);
  state.teamsBridgeAudioReady = true;
  log("Teams Lisa audio bridged into Teams call", { event: "teams_outbound_webrtc_audio_ready", reason });
  return true;
}

function rememberInviteMetadata(invite = {}) {
  if (invite.candidate_id) state.candidateId = String(invite.candidate_id);
  if (invite.candidate_name) state.candidateName = String(invite.candidate_name);
}

function getRemoteParticipantCount(call) {
  const participants = call?.remoteParticipants;
  return Array.isArray(participants) ? participants.length : 0;
}

function clearTeamsRemoteEmptyTimer(reason = "") {
  if (state.teamsRemoteEmptyTimer) clearTimeout(state.teamsRemoteEmptyTimer);
  if (state.teamsRemoteEmptySince && reason) {
    log(`Teams remote participant auto-leave timer cancelled (${reason})`, { event: "teams_remote_empty_timer_cancelled", reason });
  }
  state.teamsRemoteEmptyTimer = null;
  state.teamsRemoteEmptySince = 0;
}

function checkTeamsRemoteParticipants(call, reason = "check") {
  if (!call || call.state !== "Connected") {
    clearTeamsRemoteEmptyTimer("call_not_connected");
    return;
  }
  const remoteCount = getRemoteParticipantCount(call);
  if (remoteCount > 0) {
    clearTeamsRemoteEmptyTimer("remote_present");
    return;
  }
  if (state.teamsRemoteEmptyTimer) return;
  state.teamsRemoteEmptySince = performance.now();
  log("No remote Teams participant detected; Lisa will leave in 30s if nobody rejoins", {
    event: "teams_remote_empty_timer_started",
    reason,
    graceMs: TEAMS_REMOTE_EMPTY_GRACE_MS,
  });
  state.teamsRemoteEmptyTimer = setTimeout(() => {
    const activeCall = state.teamsCall;
    const elapsedMs = Math.round(performance.now() - state.teamsRemoteEmptySince);
    if (activeCall === call && activeCall.state === "Connected" && getRemoteParticipantCount(activeCall) === 0) {
      log("No remote Teams participant for 30s; Lisa is leaving the meeting", {
        event: "teams_remote_left_auto_hangup",
        elapsedMs,
      });
      leaveTeams().catch(error => log(`Auto-leave after remote left failed: ${error.message}`, { event: "teams_remote_left_auto_hangup_failed" }));
    }
  }, TEAMS_REMOTE_EMPTY_GRACE_MS);
}

function attachTeamsRemoteParticipantMonitor(call) {
  detachTeamsRemoteParticipantMonitor(call);
  state.remoteParticipantsHandler = () => checkTeamsRemoteParticipants(call, "remote_participants_updated");
  try {
    call.on("remoteParticipantsUpdated", state.remoteParticipantsHandler);
  } catch (error) {
    log(`Could not attach Teams remote participant monitor: ${error.message}`, { event: "teams_remote_monitor_error" });
    return;
  }
  checkTeamsRemoteParticipants(call, "connected");
}

function detachTeamsRemoteParticipantMonitor(call = state.teamsCall) {
  if (state.remoteParticipantsHandler && call) {
    try { call.off("remoteParticipantsUpdated", state.remoteParticipantsHandler); } catch (_) {}
  }
  state.remoteParticipantsHandler = null;
  clearTeamsRemoteEmptyTimer("monitor_detached");
}

// ---- Teams-only mode -------------------------------------------------------
// In Teams-only mode the page Lisa session is forcibly disconnected and the
// page-mic / page-text controls are disabled. Only the Teams Lisa session is
// active. This is the default whenever a Teams call is joined and is the
// recommended way to demo on a single laptop.

function enterTeamsOnlyMode(reason = "teams_join") {
  if (state.teamsOnlyModeActive) return;
  state.teamsOnlyModeActive = true;
  // Tear down the page Lisa session if any.
  const hadPageLisa = state.voiceConnected || state.voiceConnecting || Boolean(state.ws);
  if (hadPageLisa) disconnectVoiceLive();
  stopMic();
  // Disable page-only controls.
  if (els.connectBtn) { els.connectBtn.disabled = true; els.connectBtn.textContent = "Page Lisa disabled (Teams-only mode)"; }
  if (els.micBtn) { els.micBtn.disabled = true; els.micBtn.textContent = "Mic disabled"; }
  if (els.sendTextBtn) els.sendTextBtn.disabled = true;
  if (els.textInput) els.textInput.disabled = true;
  setPill(els.voiceStatus, "Voice: page Lisa disabled (Teams-only)");
  log("Page Lisa disabled in Teams-only mode", { event: "teams_only_mode_entered", reason, hadPageLisa });
}

function exitTeamsOnlyMode(reason = "teams_left") {
  if (!state.teamsOnlyModeActive) return;
  state.teamsOnlyModeActive = false;
  if (els.connectBtn) { els.connectBtn.disabled = false; els.connectBtn.textContent = "Connect Voice Live"; }
  if (els.micBtn) { els.micBtn.disabled = !state.voiceConnected; els.micBtn.textContent = "Mic"; }
  if (els.sendTextBtn) els.sendTextBtn.disabled = !state.voiceConnected;
  if (els.textInput) els.textInput.disabled = false;
  setPill(els.voiceStatus, "Voice: idle");
  log("Teams-only mode exited; page Lisa controls re-enabled", { event: "teams_only_mode_exited", reason });
}

function enableTeamsTestControl(enabled) {
  if (els.teamsTestBtn) els.teamsTestBtn.disabled = !enabled;
  if (els.teamsTestInput) els.teamsTestInput.disabled = !enabled;
}

function sendTeamsTestText() {
  if (!els.teamsTestInput) return;
  const text = els.teamsTestInput.value.trim();
  if (!text) return;
  if (!state.teamsWs || state.teamsWs.readyState !== WebSocket.OPEN) {
    log("Cannot send test text: Teams Lisa session is not connected", { event: "teams_test_text_blocked" });
    return;
  }
  addMessage("user", `[Teams test] ${text}`);
  state.teamsWs.send(JSON.stringify({ type: "send_text", text }));
  log(`Test text injected into Teams Lisa session: ${JSON.stringify(text)}`, { event: "teams_test_text_sent" });
  els.teamsTestInput.value = "";
}

// ---- Teams audio input activity monitor -----------------------------------
// First-frame and "silent" detection so the operator can tell whether Teams
// remote audio is actually arriving at the Teams Lisa session.

function noteTeamsAudioInputFrame() {
  const now = performance.now();
  state.teamsAudioInputLastFrameAt = now;
  if (!state.teamsAudioInputFirstFrameAt) {
    state.teamsAudioInputFirstFrameAt = now;
    log("Teams audio input active (first remote audio frame received)", { event: "teams_audio_input_active" });
  }
  if (state.teamsAudioInputSilenceLogged) {
    state.teamsAudioInputSilenceLogged = false;
    log("Teams audio input resumed", { event: "teams_audio_input_resumed" });
  }
}

function startTeamsAudioInputSilenceMonitor() {
  stopTeamsAudioInputSilenceMonitor();
  state.teamsAudioInputFirstFrameAt = 0;
  state.teamsAudioInputLastFrameAt = 0;
  state.teamsAudioInputSilenceLogged = false;
  state.teamsAudioInputSilenceTimer = setInterval(() => {
    const now = performance.now();
    if (!state.teamsAudioInputFirstFrameAt) {
      // No frames yet; only warn once after 5s.
      if (!state.teamsAudioInputSilenceLogged && now > 0) {
        // We rely on the call-state ms ticking; emit single "silent" warning when
        // the monitor has been running for >5s and no frame has ever arrived.
        if ((state.teamsAudioInputSilenceMonitorStartedAt || 0) && now - state.teamsAudioInputSilenceMonitorStartedAt > 5000) {
          state.teamsAudioInputSilenceLogged = true;
          log("Teams audio input silent (no remote audio frames after 5s)", { event: "teams_audio_input_silent" });
        }
      }
      return;
    }
    if (!state.teamsAudioInputSilenceLogged && now - state.teamsAudioInputLastFrameAt > 3000) {
      state.teamsAudioInputSilenceLogged = true;
      log("Teams audio input silent (no remote audio frames in last 3s)", { event: "teams_audio_input_silent" });
    }
  }, 1000);
  state.teamsAudioInputSilenceMonitorStartedAt = performance.now();
}

function stopTeamsAudioInputSilenceMonitor() {
  if (state.teamsAudioInputSilenceTimer) clearInterval(state.teamsAudioInputSilenceTimer);
  state.teamsAudioInputSilenceTimer = null;
  state.teamsAudioInputSilenceMonitorStartedAt = 0;
}

async function loadConfig() {
  const resp = await fetch("/api/config");
  const config = await resp.json();
  if (config.teamsMeetingLink && !state.teamsLinkTouched) {
    els.teamsLink.value = config.teamsMeetingLink;
    state.latestInviteUrl = config.teamsMeetingLink;
  }
  rememberInviteMetadata(config.latestInvite || {});
  els.displayName.value = config.teamsDisplayName || "Lisa HR";
  els.voiceName.value = config.voice || "en-US-AvaMultilingualNeural";
  els.avatarCharacter.value = config.avatarCharacter || "meg";
  els.avatarStyle.value = config.avatarStyle || "business";
  state.teamsAvatarBackgroundImageUrl = config.teamsAvatarBackgroundImage || "/background.png";
  state.teamsVoiceLiveAvatarBackgroundImageUrl = config.voiceLiveAvatarBackgroundImageUrl || "";
  state.teamsAvatarChromaKeyColor = config.teamsAvatarChromaKeyColor || "#00ff00";
  state.teamsAvatarChromaEnabled = config.teamsAvatarChromaEnabled !== false;
  els.instructions.value = "You are a friendly assistant. Keep answers short, natural, and spoken. Ask one question at a time.";
  log(`Config loaded. ACS=${config.acsConfigured ? "yes" : "no"}, agent=${config.agentConfigured ? "yes" : "no"}`);
  if (config.latestInvite?.meeting_url) {
    log(`Latest HR invite loaded for ${config.latestInvite.candidate_name || "candidate"}`);
  }
}

async function refreshLatestInvite() {
  if (state.teamsLinkTouched && els.teamsLink.value.trim()) return;
  const resp = await fetch("/api/latest-invite", { cache: "no-store" });
  if (!resp.ok) return;
  const invite = await resp.json();
  rememberInviteMetadata(invite);
  const url = invite.meeting_url || "";
  if (!url || url === state.latestInviteUrl) return;
  state.latestInviteUrl = url;
  els.teamsLink.value = url;
  log(`Picked up latest HR Teams invite for ${invite.candidate_name || "candidate"}`);
  await maybeAutoJoinInvite(url, invite.candidate_name || "candidate");
}

function teamsCallActive() {
  return Boolean(state.teamsCall && state.teamsCall.state !== "Disconnected");
}

function clearVoiceReconnectTimer() {
  if (state.voiceReconnectTimer) {
    clearTimeout(state.voiceReconnectTimer);
    state.voiceReconnectTimer = null;
  }
}

function scheduleVoiceReconnect(reason) {
  if (!state.voiceWanted || !teamsCallActive() || state.voiceConnected || state.voiceConnecting) return;
  if (state.voiceReconnectTimer) return;
  if (state.voiceReconnectAttempts >= state.maxVoiceReconnectAttempts) {
    setPill(els.voiceStatus, "Voice: reconnect failed", "err");
    log("Voice Live reconnect gave up after one automatic retry", { event: "voice_reconnect_exhausted", reason });
    return;
  }
  state.voiceReconnectAttempts += 1;
  const delayMs = Math.min(1000 * (2 ** (state.voiceReconnectAttempts - 1)), 8000);
  setPill(els.voiceStatus, `Voice: reconnect ${state.voiceReconnectAttempts}`, "err");
  log(`Voice Live reconnect scheduled in ${delayMs} ms (${reason})`, {
    event: "voice_reconnect_scheduled",
    reason,
    attempt: state.voiceReconnectAttempts,
    delayMs,
  });
  state.voiceReconnectTimer = setTimeout(() => {
    state.voiceReconnectTimer = null;
    if (state.voiceWanted && teamsCallActive() && !state.voiceConnected && !state.voiceConnecting) {
      connectVoiceLive({ reconnect: true }).catch(error => {
        log(`Voice Live reconnect failed: ${error.message}`, { event: "voice_reconnect_failed", reason });
        scheduleVoiceReconnect("connect_failed");
      });
    }
  }, delayMs);
}

function connectWebSocket() {
  return new Promise((resolve, reject) => {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${location.host}/ws/${state.clientId}`);
    ws.onopen = () => { state.ws = ws; resolve(ws); };
    ws.onerror = () => reject(new Error("WebSocket failed"));
    ws.onclose = () => {
      state.voiceConnected = false;
      state.voiceConnecting = false;
      cleanupPagePeerConnection();
      setPill(els.voiceStatus, "Voice: disconnected", "err");
      els.micBtn.disabled = true;
      els.sendTextBtn.disabled = true;
      els.connectBtn.disabled = false;
      els.connectBtn.textContent = "Connect Voice Live";
      scheduleVoiceReconnect("websocket_closed");
    };
    ws.onmessage = (event) => handleServerMessage(JSON.parse(event.data));
  });
}

async function connectVoiceLive(options = {}) {
  if (state.voiceConnected || state.voiceConnecting) return;
  if (state.teamsOnlyModeActive) {
    log("Page Lisa is disabled while Teams-only mode is active", { event: "page_lisa_blocked_teams_only" });
    return;
  }
  try {
    state.voiceWanted = true;
    if (!options.reconnect) {
      state.voiceReconnectAttempts = 0;
      clearVoiceReconnectTimer();
    }
    state.voiceConnecting = true;
    setPill(els.voiceStatus, "Voice: connecting");
    els.connectBtn.disabled = true;
    const ws = state.ws || await connectWebSocket();
    ws.send(JSON.stringify({
      type: "start_session",
      config: {
        voice: els.voiceName.value.trim(),
        avatarCharacter: els.avatarCharacter.value.trim(),
        avatarStyle: els.avatarStyle.value.trim(),
        avatarOutputMode: "webrtc",
        instructions: els.instructions.value.trim(),
        enableProactive: true,
        language: "en",
      },
    }));
  } catch (error) {
    state.voiceConnecting = false;
    els.connectBtn.disabled = false;
    setPill(els.voiceStatus, "Voice: error", "err");
    log(`Voice connect error: ${error.message}`);
    scheduleVoiceReconnect("connect_error");
  }
}

function handleServerMessage(message) {
  switch (message.type) {
    case "session_started":
      state.voiceConnected = true;
      state.voiceConnecting = false;
      state.voiceReconnectAttempts = 0;
      clearVoiceReconnectTimer();
      setPill(els.voiceStatus, "Voice: connected", "ok");
      els.micBtn.disabled = false;
      els.sendTextBtn.disabled = false;
      els.connectBtn.disabled = false;
      els.connectBtn.textContent = "Disconnect Voice Live";
      log(`Voice session ready (${message.config?.agentMode ? "agent" : "model"})`);
      break;
    case "ice_servers":
      setupWebRtc(message.iceServers || []);
      break;
    case "avatar_sdp_answer":
      handleAvatarSdpAnswer(message.serverSdp);
      break;
    case "audio_data":
      playPcmAudio(message.data);
      break;
    case "transcript_done":
      if (message.transcript) addMessage(message.role || "system", message.transcript);
      break;
    case "transcript_delta":
      break;
    case "response_created":
      log("Lisa response started");
      break;
    case "response_done":
      log("Lisa response done");
      break;
    case "speech_started":
      log("Speech detected");
      break;
    case "session_error":
    case "error":
      state.voiceConnecting = false;
      setPill(els.voiceStatus, "Voice: error", "err");
      log(`Voice error: ${message.error || message.message || "unknown"}`);
      els.connectBtn.disabled = false;
      els.connectBtn.textContent = "Connect Voice Live";
      scheduleVoiceReconnect("voice_error_event");
      break;
    case "session_closed":
      state.voiceConnected = false;
      state.voiceConnecting = false;
      els.micBtn.disabled = true;
      els.sendTextBtn.disabled = true;
      els.connectBtn.disabled = false;
      els.connectBtn.textContent = "Connect Voice Live";
      log("Voice session closed");
      scheduleVoiceReconnect("session_closed");
      break;
    default:
      log(`Event: ${message.type}`);
  }
}

async function setupWebRtc(iceServers) {
  await ensurePlaybackContext();
  cleanupPagePeerConnection();
  const pc = new RTCPeerConnection({ iceServers });
  state.peerConnection = pc;
  pc.addTransceiver("video", { direction: "sendrecv" });
  pc.addTransceiver("audio", { direction: "sendrecv" });
  pc.ontrack = (event) => {
    const stream = event.streams[0] || new MediaStream([event.track]);
    if (event.track.kind === "video") {
      // Page-side avatar only. Teams gets its own avatar from the Teams Lisa session.
      els.avatarVideo.srcObject = stream;
      els.avatarPane.classList.add("live");
    }
    // Audio from the page Lisa is handled via the PCM stream into playbackContext
    // (see playPcmAudio). We deliberately do NOT bridge WebRTC audio here so the
    // page session is fully isolated from Teams.
  };
  pc.onicecandidate = (event) => {
    if (!event.candidate) sendLocalSdp(pc);
  };
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  log("WebRTC offer created; waiting for ICE gathering");
}

function sendLocalSdp(pc) {
  if (!state.ws || !pc.localDescription) return;
  const encoded = btoa(JSON.stringify(pc.localDescription));
  state.ws.send(JSON.stringify({ type: "avatar_sdp_offer", clientSdp: encoded }));
  log("Avatar SDP offer sent");
}

async function handleAvatarSdpAnswer(serverSdp) {
  if (!state.peerConnection || !serverSdp) return;
  const answer = JSON.parse(atob(serverSdp));
  await state.peerConnection.setRemoteDescription(answer);
  log("Avatar WebRTC answer applied");
}

function cleanupPagePeerConnection() {
  if (state.peerConnection) {
    try { state.peerConnection.close(); } catch (_) {}
    state.peerConnection = null;
  }
  if (els.avatarPane) els.avatarPane.classList.remove("live");
  if (els.avatarVideo) try { els.avatarVideo.srcObject = null; } catch (_) {}
}

function disconnectVoiceLive(options = {}) {
  if (options.intentional !== false) {
    state.voiceWanted = false;
    state.voiceReconnectAttempts = 0;
    clearVoiceReconnectTimer();
  }
  stopMic();
  cleanupPagePeerConnection();
  if (state.ws?.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: "stop_session" }));
    state.ws.close();
  }
  state.ws = null;
  state.voiceConnected = false;
  state.voiceConnecting = false;
  els.micBtn.disabled = true;
  els.sendTextBtn.disabled = true;
  els.connectBtn.disabled = false;
  els.connectBtn.textContent = "Connect Voice Live";
  setPill(els.voiceStatus, "Voice: disconnected");
  log("Voice session stopped");
}

async function playPcmAudio(base64) {
  // Page Lisa audio: play to local speakers only. Teams Lisa is a separate
  // session with its own playTeamsPcmAudio() that routes into the Teams bridge.
  const ctx = await ensurePlaybackContext();
  const int16 = new Int16Array(base64ToArrayBuffer(base64));
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i += 1) float32[i] = int16[i] / 32768;
  const buffer = ctx.createBuffer(1, float32.length, 24000);
  buffer.getChannelData(0).set(float32);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  source.start();
}

async function toggleMic() {
  // Page Lisa mic. Independent of Teams; talking to the page Lisa never reaches
  // the Teams meeting, and Teams audio never reaches the page Lisa.
  if (state.micNode) {
    stopMic();
    return;
  }
  if (!state.voiceConnected || !state.ws) return;
  try {
    state.micContext = new AudioContext();
    await state.micContext.audioWorklet.addModule("/voice-capture-worklet.js");
    state.micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
    const source = state.micContext.createMediaStreamSource(state.micStream);
    state.micNode = new AudioWorkletNode(state.micContext, "voice-capture");
    state.micNode.port.onmessage = (event) => {
      if (state.ws?.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: "audio_chunk", data: arrayBufferToBase64(event.data) }));
      }
    };
    state.micSink = state.micContext.createGain();
    state.micSink.gain.value = 0;
    source.connect(state.micNode);
    state.micNode.connect(state.micSink);
    state.micSink.connect(state.micContext.destination);
    els.micBtn.textContent = "Stop mic";
    log("Local mic streaming to page Lisa (independent of Teams)");
  } catch (error) {
    stopMic();
    log(`Mic failed: ${error.message}`, { event: "mic_error" });
  }
}

function stopMic() {
  if (state.micNode) {
    try { state.micNode.disconnect(); } catch (_) {}
    state.micNode = null;
  }
  if (state.micSink) {
    try { state.micSink.disconnect(); } catch (_) {}
    state.micSink = null;
  }
  if (state.micStream) {
    state.micStream.getTracks().forEach(track => track.stop());
    state.micStream = null;
  }
  if (state.micContext) {
    state.micContext.close().catch(() => {});
    state.micContext = null;
  }
  els.micBtn.textContent = "Mic";
}

async function sendTextTurn() {
  const text = els.textInput.value.trim();
  if (!text || !state.ws) return;
  addMessage("user", text);
  state.ws.send(JSON.stringify({ type: "send_text", text }));
  els.textInput.value = "";
}

async function joinTeams() {
  if (state.teamsCall) {
    log("Teams call is already active");
    return;
  }
  const originProblem = acsOriginProblem();
  if (originProblem) {
    setPill(els.teamsStatus, "Teams: error", "err");
    log(originProblem, { event: "acs_origin_error" });
    throw new Error(originProblem);
  }
  if (typeof AzureCommunicationCalling === "undefined") {
    setPill(els.teamsStatus, "Teams: ACS bundle missing", "err");
    log("Build static/acs-calling.js first: cd rebuild-acs; python build_acs.py --version 1.42.1");
    throw new Error("ACS bundle missing");
  }
  const meetingLink = els.teamsLink.value.trim();
  if (!meetingLink) {
    log("Paste a Teams meeting link first");
    throw new Error("Teams meeting link is empty");
  }
  await ensureVoiceConnectedForTeams();
  setPill(els.teamsStatus, "Teams: starting Lisa");
  await connectTeamsLisa();
  setPill(els.teamsStatus, "Teams: token");
  const tokenResp = await fetch("/api/acs-token");
  if (!tokenResp.ok) throw new Error((await tokenResp.json()).detail || "ACS token failed");
  const tokenData = await tokenResp.json();
  const { CallClient, AzureCommunicationTokenCredential, LocalAudioStream } = AzureCommunicationCalling;
  state.teamsCallClient = new CallClient();
  const credential = new AzureCommunicationTokenCredential(tokenData.token);
  state.teamsCallAgent = await state.teamsCallClient.createCallAgent(credential, { displayName: els.displayName.value.trim() || "Lisa HR" });
  await ensureTeamsBridgeDestination();
  await attachTeamsLisaAudioToTeams("before_join").catch(error => log(`Teams audio bridge warning: ${error.message}`));
  const lisaAudioForTeams = new LocalAudioStream(state.teamsBridgeDest.stream);
  state.teamsCall = state.teamsCallAgent.join({ meetingLink }, { audioOptions: { localAudioStreams: [lisaAudioForTeams] } });
  state.teamsCall.on("stateChanged", async () => {
    const call = state.teamsCall;
    if (!call) return;
    setPill(els.teamsStatus, `Teams: ${call.state}`, call.state === "Connected" ? "ok" : "");
    if (call.state === "Connected") {
      els.joinBtn.disabled = true;
      // Teams-only demo mode: kill the page Lisa session entirely so a single
      // laptop only has ONE active Lisa.
      enterTeamsOnlyMode("teams_connected");
      log("Teams call CONNECTED", { event: "teams_state_connected" });
      attachTeamsRemoteParticipantMonitor(call);
      if (state.autoJoinEnabled) setAutoState("Auto: joined latest HR invite", "joined");
      if (state.autoJoinEnabled) logAutoJoinDecision({ meetingUrl: els.teamsLink.value.trim(), decision: "join", outcome: "connected" });
      const audioOk = await attachTeamsLisaAudioToTeams("teams_connected").catch(error => {
        log(`Lisa audio output to Teams FAILED: ${error.message}`, { event: "teams_outbound_audio_failed" });
        return false;
      });
      if (audioOk) log("Lisa audio output to Teams ACTIVE", { event: "teams_outbound_audio_active" });
      // Mute the meeting's incoming audio on the operator's speakers so a
      // single-laptop test does not loop the remote audio back through Teams.
      try {
        if (typeof call.muteIncomingAudio === "function") {
          await call.muteIncomingAudio();
          log("ACS incoming audio muted on operator speakers (echo prevention)", { event: "acs_incoming_audio_muted" });
        }
      } catch (err) {
        log(`muteIncomingAudio failed: ${err.message}`, { event: "acs_mute_incoming_audio_error" });
      }
      // Push avatar video into Teams (best-effort).
      startTeamsAvatarVideo()
        .then(() => log("Lisa avatar video output to Teams ACTIVE", { event: "teams_avatar_video_active" }))
        .catch(err => log(`Lisa avatar video output to Teams FAILED: ${err.message}`, { event: "teams_avatar_video_failed" }));
      startTeamsAudioInputSilenceMonitor();
      await startTeamsAudioCapture();
      enableTeamsTestControl(true);
    } else if (call.state === "InLobby") {
      els.joinBtn.disabled = true;
      setPill(els.teamsStatus, "Teams: InLobby - admit Lisa HR", "err");
      log("Teams LOBBY: Lisa HR is waiting. Admit Lisa HR from the Teams meeting.", { event: "teams_state_in_lobby" });
    } else if (call.state === "Disconnected") {
      log("Teams call DISCONNECTED", { event: "teams_state_disconnected" });
      detachTeamsRemoteParticipantMonitor(call);
      state.teamsCall = null;
      stopTeamsAudioInputSilenceMonitor();
      cleanupTeamsIncomingAudio();
      cleanupTeamsBridge();
      cleanupTeamsCanvas();
      disconnectTeamsLisa();
      enableTeamsTestControl(false);
      exitTeamsOnlyMode("teams_disconnected");
      els.joinBtn.disabled = false;
      if (state.autoJoinEnabled) setAutoState("Auto: armed for latest HR invite", "armed");
      if (state.autoJoinEnabled) logAutoJoinDecision({ meetingUrl: els.teamsLink.value.trim(), decision: "join", outcome: "disconnected" });
    }
  });
  setPill(els.teamsStatus, "Teams: joining");
}

async function maybeAutoJoinInvite(url, candidateName) {
  if (!url) {
    logAutoJoinDecision({ meetingUrl: "", decision: "skip", outcome: "skipped", reason: "missing_meeting_url", candidateName });
    return;
  }
  const originProblem = acsOriginProblem();
  if (originProblem) {
    setAutoState("Auto: error", "err");
    setPill(els.teamsStatus, "Teams: error", "err");
    logAutoJoinDecision({ meetingUrl: url, decision: "skip", outcome: "failed", reason: originProblem, candidateName });
    log(originProblem, { event: "acs_origin_error" });
    return;
  }
  if (!state.autoJoinEnabled) {
    logAutoJoinDecision({ meetingUrl: url, decision: "skip", outcome: "skipped", reason: "auto_join_off", candidateName });
    return;
  }
  if (state.autoJoinInFlight) {
    logAutoJoinDecision({ meetingUrl: url, decision: "skip", outcome: "skipped", reason: "join_in_flight", candidateName });
    return;
  }
  if (state.teamsCall) {
    logAutoJoinDecision({ meetingUrl: url, decision: "skip", outcome: "skipped", reason: "teams_call_active", candidateName });
    return;
  }
  if (url === state.autoJoinLastUrl) {
    logAutoJoinDecision({ meetingUrl: url, decision: "skip", outcome: "skipped", reason: "already_seen_when_armed", candidateName });
    return;
  }
  state.autoJoinLastUrl = url;
  state.autoJoinInFlight = true;
  setAutoState("Auto: joining latest HR invite", "joining");
  logAutoJoinDecision({ meetingUrl: url, decision: "join", outcome: "started", candidateName });
  log(`Auto-joining latest HR invite for ${candidateName}`);
  try {
    await joinTeams();
    logAutoJoinDecision({ meetingUrl: url, decision: "join", outcome: "requested", candidateName });
  } catch (error) {
    setAutoState("Auto: error", "err");
    setPill(els.teamsStatus, "Teams: error", "err");
    logAutoJoinDecision({ meetingUrl: url, decision: "join", outcome: "failed", reason: error.message, candidateName });
    log(`Auto-join failed: ${error.message}`);
  } finally {
    state.autoJoinInFlight = false;
  }
}

// ===== Teams-only Voice Live session ============================================
// Spawned exclusively when joining a Teams call. Uses its own WebSocket and its
// own RTCPeerConnection so audio/video for Teams is fully independent from the
// page Lisa session.

function connectTeamsLisaWebSocket() {
  return new Promise((resolve, reject) => {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const teamsClientId = `${state.clientId}--teams`;
    state.teamsClientId = teamsClientId;
    const ws = new WebSocket(`${protocol}//${location.host}/ws/${teamsClientId}`);
    ws.onopen = () => { state.teamsWs = ws; resolve(ws); };
    ws.onerror = () => reject(new Error("Teams Lisa WebSocket failed"));
    ws.onclose = () => {
      state.teamsVoiceConnected = false;
      state.teamsVoiceConnecting = false;
      cleanupTeamsPeerConnection();
      log("Teams Lisa session closed", { event: "teams_lisa_session_closed" });
    };
    ws.onmessage = (event) => handleTeamsLisaServerMessage(JSON.parse(event.data));
  });
}

async function connectTeamsLisa() {
  if (state.teamsVoiceConnected || state.teamsVoiceConnecting) return;
  state.teamsVoiceConnecting = true;
  await ensurePlaybackContext();
  const ws = state.teamsWs || await connectTeamsLisaWebSocket();
  ws.send(JSON.stringify({
    type: "start_session",
    config: {
      voice: els.voiceName.value.trim(),
      avatarCharacter: els.avatarCharacter.value.trim(),
      avatarStyle: els.avatarStyle.value.trim(),
      avatarBackgroundImageUrl: state.teamsVoiceLiveAvatarBackgroundImageUrl,
      avatarBackgroundColor: state.teamsVoiceLiveAvatarBackgroundImageUrl ? "" : state.teamsAvatarChromaEnabled ? state.teamsAvatarChromaKeyColor : "",
      avatarOutputMode: "webrtc",
      instructions: els.instructions.value.trim(),
      enableProactive: true,
      language: "en",
      candidateId: state.candidateId,
      candidateName: state.candidateName,
    },
  }));
  await waitForCondition(() => state.teamsVoiceConnected, 20000, "Teams Lisa Voice Live connection");
  await waitForCondition(() => Boolean(state.teamsLisaOutputStream), 8000, "Teams Lisa WebRTC audio stream")
    .catch(error => log(`${error.message}; Teams may have a brief silent gap until first response`, { event: "teams_lisa_audio_wait_timeout" }));
}

function handleTeamsLisaServerMessage(message) {
  switch (message.type) {
    case "session_started":
      state.teamsVoiceConnected = true;
      state.teamsVoiceConnecting = false;
      log("Teams Lisa session READY", { event: "teams_lisa_session_ready" });
      break;
    case "ice_servers":
      setupTeamsLisaWebRtc(message.iceServers || []).catch(err => log(`Teams Lisa WebRTC error: ${err.message}`));
      break;
    case "avatar_sdp_answer":
      handleTeamsLisaAvatarSdpAnswer(message.serverSdp);
      break;
    case "audio_data":
      playTeamsLisaPcmAudio(message.data);
      break;
    case "transcript_done":
      // Show Teams Lisa's words in transcript prefixed so it's distinguishable.
      if (message.transcript) addMessage(message.role === "assistant" ? "assistant" : message.role || "system", `[Teams] ${message.transcript}`);
      break;
    case "response_created":
      log("Teams Lisa response started");
      break;
    case "response_done":
      log("Teams Lisa response done");
      break;
    case "speech_started":
      log("Teams remote speech detected");
      break;
    case "session_error":
    case "error":
      log(`Teams Lisa error: ${message.error || message.message || "unknown"}`);
      break;
    case "session_closed":
      state.teamsVoiceConnected = false;
      state.teamsVoiceConnecting = false;
      log("Teams Lisa session closed (server)");
      break;
    default:
      // Ignore other event types for the Teams session.
      break;
  }
}

async function setupTeamsLisaWebRtc(iceServers) {
  await ensurePlaybackContext();
  cleanupTeamsPeerConnection();
  const pc = new RTCPeerConnection({ iceServers });
  state.teamsPeerConnection = pc;
  pc.addTransceiver("video", { direction: "sendrecv" });
  pc.addTransceiver("audio", { direction: "sendrecv" });
  pc.ontrack = (event) => {
    const stream = event.streams[0] || new MediaStream([event.track]);
    if (event.track.kind === "video") {
      state.teamsLisaAvatarVideoTrack = event.track;
      // If the ACS call is already connected, push the avatar video into Teams now.
      if (teamsCallActive()) {
        startTeamsAvatarVideo().catch(err => log(`Teams avatar video failed: ${err.message}`, { event: "teams_avatar_video_error" }));
      }
    } else if (event.track.kind === "audio") {
      state.teamsLisaOutputStream = stream;
      // Force the WebRTC audio track to keep flowing in this tab without playing
      // it through speakers (muted + offscreen <audio>).
      if (!state.teamsLisaOutputAudioEl) {
        const audioEl = document.createElement("audio");
        audioEl.autoplay = true;
        audioEl.playsInline = true;
        audioEl.muted = true;
        audioEl.srcObject = stream;
        document.body.appendChild(audioEl);
        state.teamsLisaOutputAudioEl = audioEl;
      }
      if (state.teamsBridgeDest) {
        attachTeamsLisaAudioToTeams("webrtc_track").catch(err => log(`Teams audio bridge error: ${err.message}`));
      }
    }
  };
  pc.onicecandidate = (event) => {
    if (!event.candidate) sendTeamsLisaLocalSdp(pc);
  };
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  log("Teams Lisa WebRTC offer created");
}

function sendTeamsLisaLocalSdp(pc) {
  if (!state.teamsWs || !pc.localDescription) return;
  const encoded = btoa(JSON.stringify(pc.localDescription));
  state.teamsWs.send(JSON.stringify({ type: "avatar_sdp_offer", clientSdp: encoded }));
}

async function handleTeamsLisaAvatarSdpAnswer(serverSdp) {
  if (!state.teamsPeerConnection || !serverSdp) return;
  const answer = JSON.parse(atob(serverSdp));
  await state.teamsPeerConnection.setRemoteDescription(answer);
  log("Teams Lisa WebRTC answer applied");
}

async function playTeamsLisaPcmAudio(base64) {
  // Fallback path used until the Teams Lisa WebRTC audio track exists.
  if (state.teamsBridgeAudioReady || !state.teamsBridgeDest) return;
  const ctx = await ensurePlaybackContext();
  const int16 = new Int16Array(base64ToArrayBuffer(base64));
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i += 1) float32[i] = int16[i] / 32768;
  const buffer = ctx.createBuffer(1, float32.length, 24000);
  buffer.getChannelData(0).set(float32);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(state.teamsBridgeDest);
  source.start();
  if (!state.teamsPcmBridgeLogged) {
    state.teamsPcmBridgeLogged = true;
    log("Teams Lisa PCM audio streaming to Teams (pre-WebRTC)", { event: "teams_outbound_pcm_audio_ready" });
  }
}

function cleanupTeamsPeerConnection() {
  if (state.teamsPeerConnection) {
    try { state.teamsPeerConnection.close(); } catch (_) {}
    state.teamsPeerConnection = null;
  }
  if (state.teamsLisaOutputAudioEl) {
    try { state.teamsLisaOutputAudioEl.srcObject = null; state.teamsLisaOutputAudioEl.remove(); } catch (_) {}
    state.teamsLisaOutputAudioEl = null;
  }
  state.teamsLisaOutputStream = null;
  state.teamsLisaAvatarVideoTrack = null;
}

function disconnectTeamsLisa() {
  cleanupTeamsPeerConnection();
  if (state.teamsWs?.readyState === WebSocket.OPEN) {
    try { state.teamsWs.send(JSON.stringify({ type: "stop_session" })); } catch (_) {}
    try { state.teamsWs.close(); } catch (_) {}
  }
  state.teamsWs = null;
  state.teamsClientId = null;
  state.teamsVoiceConnected = false;
  state.teamsVoiceConnecting = false;
}

// ================================================================================


function parseHexColor(color) {
  const match = /^#?([0-9a-f]{6})$/i.exec(String(color || "").trim());
  const value = match ? parseInt(match[1], 16) : 0x00ff00;
  return {
    red: (value >> 16) & 255,
    green: (value >> 8) & 255,
    blue: value & 255,
  };
}

function drawImageCover(context, image, width, height) {
  const sourceWidth = image.naturalWidth || image.videoWidth || width;
  const sourceHeight = image.naturalHeight || image.videoHeight || height;
  const scale = Math.max(width / sourceWidth, height / sourceHeight);
  const cropWidth = width / scale;
  const cropHeight = height / scale;
  const cropX = (sourceWidth - cropWidth) / 2;
  const cropY = (sourceHeight - cropHeight) / 2;
  context.drawImage(image, cropX, cropY, cropWidth, cropHeight, 0, 0, width, height);
}

function containRect(sourceWidth, sourceHeight, targetWidth, targetHeight) {
  const scale = Math.min(targetWidth / sourceWidth, targetHeight / sourceHeight);
  const width = sourceWidth * scale;
  const height = sourceHeight * scale;
  return {
    x: (targetWidth - width) / 2,
    y: (targetHeight - height) / 2,
    width,
    height,
  };
}

function sampleCornerMatteColors(imageData) {
  const { width, height, data } = imageData;
  const points = [
    [0, 0],
    [Math.max(0, width - 1), 0],
    [0, Math.max(0, height - 1)],
    [Math.max(0, width - 1), Math.max(0, height - 1)],
    [Math.floor(width / 2), 0],
  ];
  return points.map(([x, y]) => {
    const offset = ((y * width) + x) * 4;
    return { red: data[offset], green: data[offset + 1], blue: data[offset + 2], adaptive: true };
  });
}

function applyBackgroundMatte(imageData, matteColors) {
  const pixels = imageData.data;
  for (let index = 0; index < pixels.length; index += 4) {
    const red = pixels[index];
    const green = pixels[index + 1];
    const blue = pixels[index + 2];
    let alpha = pixels[index + 3];
    for (const matteColor of matteColors) {
      const redDelta = red - matteColor.red;
      const greenDelta = green - matteColor.green;
      const blueDelta = blue - matteColor.blue;
      const distance = Math.sqrt((redDelta * redDelta) + (greenDelta * greenDelta) + (blueDelta * blueDelta));
      const dominantGreen = !matteColor.adaptive && green > 95 && green - Math.max(red, blue) > 45;
      const hardThreshold = matteColor.adaptive ? 42 : 65;
      const softThreshold = matteColor.adaptive ? 92 : 130;
      if (distance < softThreshold || dominantGreen) {
        const candidateAlpha = distance < hardThreshold || dominantGreen ? 0 : Math.min(255, (distance - hardThreshold) * (255 / (softThreshold - hardThreshold)));
        alpha = Math.min(alpha, candidateAlpha);
      }
    }
    pixels[index + 3] = alpha;
  }
}

function loadTeamsAvatarBackgroundImage() {
  if (state.teamsAvatarBackgroundImage) return Promise.resolve(state.teamsAvatarBackgroundImage);
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      state.teamsAvatarBackgroundImage = image;
      resolve(image);
    };
    image.onerror = () => reject(new Error(`could not load ${state.teamsAvatarBackgroundImageUrl}`));
    image.src = state.teamsAvatarBackgroundImageUrl;
  });
}


async function startTeamsAvatarVideo() {
  if (!state.teamsCall || !state.teamsLisaAvatarVideoTrack || state.teamsLocalVideoStream) return;
  const canvas = document.createElement("canvas");
  canvas.width = 960;
  canvas.height = 540;
  const context = canvas.getContext("2d");
  const avatarCanvas = document.createElement("canvas");
  avatarCanvas.width = canvas.width;
  avatarCanvas.height = canvas.height;
  const avatarContext = avatarCanvas.getContext("2d", { willReadFrequently: true });
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.srcObject = new MediaStream([state.teamsLisaAvatarVideoTrack]);
  await video.play().catch(() => {});
  let backgroundImage = null;
  try {
    backgroundImage = await loadTeamsAvatarBackgroundImage();
  } catch (error) {
    log(`Avatar background image unavailable: ${error.message}; using plain canvas fallback`, { event: "teams_avatar_background_unavailable" });
  }
  const useLocalMatte = state.teamsAvatarChromaEnabled && !state.teamsVoiceLiveAvatarBackgroundImageUrl;
  const chromaKey = useLocalMatte ? parseHexColor(state.teamsAvatarChromaKeyColor) : null;
  if (useLocalMatte) {
    const backgroundMode = state.teamsVoiceLiveAvatarBackgroundImageUrl ? `image URL ${state.teamsVoiceLiveAvatarBackgroundImageUrl}` : `chroma ${state.teamsAvatarChromaKeyColor}`;
    log(`Voice Live avatar background requested (${backgroundMode}); compositing background image in Teams canvas`, { event: "teams_avatar_chroma_enabled" });
  } else {
    log("Avatar background removal is unavailable; compositing background image behind the unkeyed avatar video", { event: "teams_avatar_chroma_unavailable" });
  }
  state.teamsCanvasEls = { canvas, avatarCanvas, video };
  state.teamsCanvasInterval = setInterval(() => {
    if (backgroundImage) {
      drawImageCover(context, backgroundImage, canvas.width, canvas.height);
    } else {
      context.fillStyle = "white";
      context.fillRect(0, 0, canvas.width, canvas.height);
    }
    if (!video.videoWidth || !video.videoHeight || video.readyState < 2) return;
    const vw = video.videoWidth || canvas.width;
    const vh = video.videoHeight || canvas.height;
    const rect = containRect(vw, vh, canvas.width, canvas.height);
    if (useLocalMatte) {
      avatarContext.clearRect(0, 0, avatarCanvas.width, avatarCanvas.height);
      avatarContext.drawImage(video, rect.x, rect.y, rect.width, rect.height);
      const frame = avatarContext.getImageData(0, 0, avatarCanvas.width, avatarCanvas.height);
      applyBackgroundMatte(frame, [chromaKey, ...sampleCornerMatteColors(frame)]);
      avatarContext.putImageData(frame, 0, 0);
      context.drawImage(avatarCanvas, 0, 0);
    } else {
      context.drawImage(video, rect.x, rect.y, rect.width, rect.height);
    }
  }, 40);
  const { LocalVideoStream } = AzureCommunicationCalling;
  state.teamsLocalVideoStream = new LocalVideoStream(canvas.captureStream(25));
  await state.teamsCall.startVideo(state.teamsLocalVideoStream);
  log("Teams Lisa avatar video pushed into the Teams call", { event: "teams_avatar_video_started" });
}

function cleanupTeamsCanvas() {
  if (state.teamsCanvasInterval) clearInterval(state.teamsCanvasInterval);
  state.teamsCanvasInterval = null;
  if (state.teamsCanvasEls?.video) {
    try { state.teamsCanvasEls.video.srcObject = null; } catch (_) {}
    try { state.teamsCanvasEls.video.remove(); } catch (_) {}
  }
  state.teamsCanvasEls = null;
  state.teamsLocalVideoStream = null;
}

async function startTeamsAudioCapture() {
  if (!state.teamsCall || !state.teamsWs) return;
  const remoteStreams = state.teamsCall.remoteAudioStreams || [];
  if (!remoteStreams.length) {
    if (state.remoteAudioHandler) return;
    state.remoteAudioHandler = async ({ added }) => {
      if (added && added[0]) await attachIncomingTeamsAudio(added[0]).catch(error => log(`Teams inbound audio error: ${error.message}`, { event: "teams_inbound_audio_error" }));
    };
    state.teamsCall.on("remoteAudioStreamsUpdated", state.remoteAudioHandler);
    log("Waiting for Teams remote audio stream");
    return;
  }
  await attachIncomingTeamsAudio(remoteStreams[0]);
}

async function attachIncomingTeamsAudio(remoteAudioStream) {
  if (typeof remoteAudioStream.getMediaStream !== "function") {
    const message = "ACS bundle does not expose getMediaStream(); rebuild static/acs-calling.js before bidirectional Teams audio.";
    setPill(els.teamsStatus, "Teams: audio blocked", "err");
    log(message, { event: "teams_inbound_audio_blocked" });
    throw new Error(message);
  }
  cleanupTeamsIncomingAudio();
  const stream = await remoteAudioStream.getMediaStream();
  state.teamsIncomingContext = new AudioContext();
  await state.teamsIncomingContext.audioWorklet.addModule("/voice-capture-worklet.js");
  const source = state.teamsIncomingContext.createMediaStreamSource(stream);
  state.teamsIncomingNode = new AudioWorkletNode(state.teamsIncomingContext, "voice-capture");
  state.teamsIncomingNode.port.onmessage = (event) => {
    noteTeamsAudioInputFrame();
    // Forward Teams remote audio to the *Teams* Lisa session, never to the page Lisa.
    if (state.teamsWs?.readyState === WebSocket.OPEN) {
      state.teamsWs.send(JSON.stringify({ type: "audio_chunk", data: arrayBufferToBase64(event.data) }));
    }
  };
  state.teamsIncomingSink = state.teamsIncomingContext.createGain();
  state.teamsIncomingSink.gain.value = 0;
  source.connect(state.teamsIncomingNode);
  state.teamsIncomingNode.connect(state.teamsIncomingSink);
  state.teamsIncomingSink.connect(state.teamsIncomingContext.destination);
  state.teamsInboundAudioReady = true;
  log("Teams remote audio streaming to Teams Lisa");
}

async function leaveTeams() {
  detachTeamsRemoteParticipantMonitor();
  stopTeamsAudioInputSilenceMonitor();
  cleanupTeamsCanvas();
  cleanupTeamsIncomingAudio();
  if (state.teamsCall) await state.teamsCall.hangUp({ forEveryone: false }).catch(() => {});
  state.teamsCall = null;
  cleanupTeamsBridge();
  disconnectTeamsLisa();
  enableTeamsTestControl(false);
  exitTeamsOnlyMode("leave_teams");
  els.joinBtn.disabled = false;
  setPill(els.teamsStatus, "Teams: left");
  if (state.autoJoinEnabled) setAutoState("Auto: armed for latest HR invite", "armed");
}

async function killLisaMeeting() {
  // Single cleanup button: hang up Teams, stop both Lisa sessions, stop all
  // local tracks, close peer connections, reset UI state.
  log("Leave Meeting pressed (full cleanup)", { event: "kill_switch" });
  // Always stop page mic + tear down page Lisa even if Teams-only mode left it dormant.
  stopMic();
  disconnectVoiceLive();
  const hangup = leaveTeams().then(() => "done");
  const result = await Promise.race([hangup, new Promise(resolve => setTimeout(() => resolve("timeout"), 1500))]);
  if (result === "timeout") setPill(els.teamsStatus, "Teams: leaving");
  // Final UI reset.
  if (els.connectBtn) { els.connectBtn.disabled = false; els.connectBtn.textContent = "Connect Voice Live"; }
  if (els.micBtn) { els.micBtn.disabled = true; els.micBtn.textContent = "Mic"; }
  if (els.sendTextBtn) els.sendTextBtn.disabled = true;
  if (els.textInput) els.textInput.disabled = false;
  enableTeamsTestControl(false);
  setPill(els.voiceStatus, "Voice: idle");
}

function downloadOperatorLog() {
  const lines = [
    "Lisa operator session log",
    `Downloaded: ${new Date().toISOString()}`,
    `Page: ${location.href}`,
    "",
  ];
  for (const entry of state.eventLog) {
    lines.push(`[${entry.timestamp}] ${entry.event} ${entry.message}`);
    lines.push(`  meetingUrl: ${entry.meetingUrl || ""}`);
    lines.push(`  voiceConnected: ${entry.voiceConnected} voiceConnecting: ${entry.voiceConnecting} teamsState: ${entry.teamsState || ""} autoJoin: ${entry.autoJoinEnabled} visibility: ${entry.pageVisibility}`);
    if (entry.metadata && Object.keys(entry.metadata).length) lines.push(`  metadata: ${JSON.stringify(entry.metadata)}`);
    lines.push("");
  }
  const blob = new Blob([lines.join("\n")], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `lisa-operator-log-${new Date().toISOString().replace(/[:.]/g, "-")}.txt`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  log("Operator log downloaded", { event: "operator_log_downloaded" });
}

function bindUi() {
  Object.assign(els, {
    voiceStatus: $("voice-status"),
    teamsStatus: $("teams-status"),
    autoStatus: $("auto-status"),
    autoBanner: $("auto-banner"),
    preflightVoice: $("pf-voice"),
    preflightAcs: $("pf-acs"),
    preflightMic: $("pf-mic"),
    preflightBrowser: $("pf-browser"),
    preflightInvite: $("pf-invite"),
    preflightRefreshBtn: $("preflight-refresh-btn"),
    teamsLink: $("teams-link"),
    autoJoin: $("auto-join"),
    displayName: $("display-name"),
    voiceName: $("voice-name"),
    avatarCharacter: $("avatar-character"),
    avatarStyle: $("avatar-style"),
    instructions: $("instructions"),
    connectBtn: $("connect-btn"),
    micBtn: $("mic-btn"),
    joinBtn: $("join-btn"),
    leaveBtn: $("leave-btn"),
    downloadLogBtn: $("download-log-btn"),
    textInput: $("text-input"),
    sendTextBtn: $("send-text-btn"),
    teamsTestInput: $("teams-test-input"),
    teamsTestBtn: $("teams-test-btn"),
    log: $("log"),
    transcript: $("transcript"),
    avatarVideo: $("avatar-video"),
    avatarPane: document.querySelector(".avatar-pane"),
  });
  els.teamsLink.addEventListener("input", () => { state.teamsLinkTouched = true; });
  els.autoJoin.addEventListener("change", async () => {
    state.autoJoinEnabled = els.autoJoin.checked;
    if (state.autoJoinEnabled) {
      state.autoJoinLastUrl = state.latestInviteUrl || els.teamsLink.value.trim();
      setAutoState("Auto: armed for latest HR invite", "armed");
      logAutoJoinDecision({ meetingUrl: state.autoJoinLastUrl, decision: "arm", outcome: "armed", reason: "operator_enabled" });
      await ensurePlaybackContext().catch(error => log(`Audio unlock warning: ${error.message}`));
      log("Auto-join armed for the next HR invite");
    } else {
      setAutoState("Auto: off", "off");
      logAutoJoinDecision({ meetingUrl: state.latestInviteUrl || els.teamsLink.value.trim(), decision: "disarm", outcome: "disabled", reason: "operator_disabled" });
      log("Auto-join disabled");
    }
  });
  els.connectBtn.addEventListener("click", () => {
    if (state.voiceConnected) disconnectVoiceLive();
    else connectVoiceLive();
  });
  els.micBtn.addEventListener("click", toggleMic);
  els.joinBtn.addEventListener("click", () => joinTeams().catch(err => { setPill(els.teamsStatus, "Teams: error", "err"); log(err.message); }));
  els.leaveBtn.addEventListener("click", () => killLisaMeeting().catch(err => log(`Leave Meeting failed: ${err.message}`)));
  els.downloadLogBtn.addEventListener("click", downloadOperatorLog);
  els.preflightRefreshBtn.addEventListener("click", () => runPreflight({ requestMic: true }).catch(error => log(`Pre-flight failed: ${error.message}`)));
  els.sendTextBtn.addEventListener("click", sendTextTurn);
  els.textInput.addEventListener("keydown", event => { if (event.key === "Enter") sendTextTurn(); });
  if (els.teamsTestBtn) els.teamsTestBtn.addEventListener("click", sendTeamsTestText);
  if (els.teamsTestInput) els.teamsTestInput.addEventListener("keydown", event => { if (event.key === "Enter") sendTeamsTestText(); });
}

window.addEventListener("DOMContentLoaded", async () => {
  if (redirectToAcsSafeLocalhost()) return;
  bindUi();
  await loadConfig().catch(error => log(`Config failed: ${error.message}`));
  await runPreflight().catch(error => log(`Pre-flight failed: ${error.message}`));
  setInterval(() => refreshLatestInvite().catch(() => {}), 4000);
});

document.addEventListener("visibilitychange", () => {
  log(`Browser tab visibility changed: ${document.visibilityState}`, { event: "visibilitychange", visibility: document.visibilityState });
});

window.addEventListener("blur", () => log("Browser window lost focus", { event: "window_blur" }));
window.addEventListener("focus", () => log("Browser window focused", { event: "window_focus" }));

window.addEventListener("beforeunload", () => {
  try { if (state.ws?.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify({ type: "stop_session" })); } catch (_) {}
});
