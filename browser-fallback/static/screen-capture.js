// screen-capture.js — Lisa screen-share vision watcher.
//
// Subscribes to ScreenSharing remote video streams on an ACS Call, renders
// them to an off-screen element, and on demand (or automatically once on
// share-start) captures a JPEG frame and POSTs it to the backend's
// /api/vision/screen-frame endpoint. The backend then runs a multimodal AOAI
// call and injects the description into the running Voice Live session so
// Lisa speaks her comment through the avatar.
//
// Exposes a single global: window.LisaVision = { attach, detach,
// captureAndSend, hasActiveShare, setStatusElement }.

(function () {
  "use strict";

  const SCREEN_SHARE_TYPES = new Set(["ScreenSharing", "screenSharing", "ScreenShare", "screensharing"]);

  const state = {
    call: null,
    clientId: null,
    autoOnShareStart: true,
    maxDim: 512,
    periodicIntervalS: 0,
    periodicTimer: null,
    lastSentSignature: null,
    statusEl: null,
    participantsHandler: null,
    perParticipant: new Map(),   // participant -> { handler }
    wiredStreams: new WeakSet(),  // RemoteVideoStream -> isAvailableChanged wired
    activeStream: null,
    activeParticipant: null,
    activeRenderer: null,
    activeView: null,
    activeVideoEl: null,
    autoNarrated: new WeakSet(), // RemoteVideoStream -> already auto-narrated once
    captureCanvas: null,
    captureCtx: null,
  };

  function log(msg, extra) {
    const stamp = new Date().toISOString().slice(11, 19);
    if (extra !== undefined) {
      console.log(`[vision ${stamp}] ${msg}`, extra);
    } else {
      console.log(`[vision ${stamp}] ${msg}`);
    }
  }

  function setStatus(label, kind) {
    if (!state.statusEl) return;
    state.statusEl.textContent = `Vision: ${label}`;
    state.statusEl.className = "pill" + (kind ? ` ${kind}` : "");
  }

  function isScreenShareStream(stream) {
    if (!stream) return false;
    const t = stream.mediaStreamType || stream.kind || "";
    return SCREEN_SHARE_TYPES.has(t);
  }

  // A Teams screen-share remote stream is frequently added while its
  // isAvailable flag is still false; it flips to true a moment later via the
  // stream's own isAvailableChanged event (NOT videoStreamsUpdated). Subscribe
  // once per share stream so we re-evaluate when it actually becomes available.
  function wireShareStreamAvailability(stream, participant) {
    if (!stream || state.wiredStreams.has(stream)) return;
    state.wiredStreams.add(stream);
    try {
      stream.on("isAvailableChanged", () => reconsiderParticipant(participant));
    } catch (err) {
      log(`isAvailableChanged subscribe failed: ${err.message}`);
    }
  }

  function findShareStream(participant) {
    const streams = (participant && participant.videoStreams) || [];
    for (const s of streams) {
      if (!isScreenShareStream(s)) continue;
      wireShareStreamAvailability(s, participant);
      if (s.isAvailable === true || typeof s.isAvailable === "undefined") {
        return s;
      }
      log("Screen-share stream present but not yet available; waiting for isAvailableChanged");
    }
    return null;
  }

  async function attachStream(stream, participant) {
    if (state.activeStream === stream) return;
    await detachStream();
    const calling = window.AzureCommunicationCalling;
    if (!calling || !calling.VideoStreamRenderer) {
      log("AzureCommunicationCalling.VideoStreamRenderer unavailable; cannot render share");
      setStatus("unsupported", "err");
      return;
    }
    try {
      state.activeRenderer = new calling.VideoStreamRenderer(stream);
      state.activeView = await state.activeRenderer.createView({ scalingMode: "Fit" });
      const target = state.activeView && state.activeView.target;
      if (target) {
        target.style.position = "absolute";
        target.style.left = "-99999px";
        target.style.top = "-99999px";
        // Keep the offscreen render small so ACS subscribes to a lighter
        // simulcast layer of the share (roughly half the decode cost vs 720p),
        // which frees the main thread shared with the avatar matte loop. Stays
        // >=512px on the long edge so captured frames remain legible — vision
        // downsamples to maxDim (512) anyway, and drawImage() samples the video's
        // intrinsic resolution regardless of this element's CSS size.
        target.style.width = "640px";
        target.style.height = "360px";
        target.style.pointerEvents = "none";
        target.setAttribute("aria-hidden", "true");
        document.body.appendChild(target);
      }
      state.activeVideoEl = (target && target.querySelector && target.querySelector("video")) || target;
      state.activeStream = stream;
      state.activeParticipant = participant;
      setStatus("share active", "ok");
      log("Attached to remote screen-share stream");
      if (state.autoOnShareStart && !state.autoNarrated.has(stream)) {
        state.autoNarrated.add(stream);
        // Give the first frame ~1.5s to render before we capture.
        setTimeout(() => {
          captureAndSend("share_start").catch(err => log(`auto narrate failed: ${err.message}`));
        }, 1500);
      }
    } catch (err) {
      log(`Failed to attach screen-share renderer: ${err.message}`);
      setStatus("attach error", "err");
      await detachStream();
    }
  }

  async function detachStream() {
    if (state.activeView) {
      try { state.activeView.dispose(); } catch (_) {}
      const target = state.activeView.target;
      if (target && target.parentNode) {
        try { target.parentNode.removeChild(target); } catch (_) {}
      }
    }
    if (state.activeRenderer) {
      try { state.activeRenderer.dispose(); } catch (_) {}
    }
    state.activeView = null;
    state.activeRenderer = null;
    state.activeVideoEl = null;
    state.activeStream = null;
    state.activeParticipant = null;
    setStatus(state.call ? "no share" : "idle");
  }

  function reconsiderParticipant(participant) {
    if (!state.call) return;
    const stream = findShareStream(participant);
    if (stream) {
      if (stream !== state.activeStream) {
        attachStream(stream, participant).catch(err => log(`attach failed: ${err.message}`));
      }
      return;
    }
    // No share from this participant. If they were the active one, drop it.
    if (state.activeParticipant === participant && state.activeStream) {
      detachStream().catch(() => {});
    }
  }

  function wireParticipant(participant) {
    if (!participant || state.perParticipant.has(participant)) return;
    const handler = () => reconsiderParticipant(participant);
    try {
      participant.on("videoStreamsUpdated", handler);
    } catch (err) {
      log(`videoStreamsUpdated subscribe failed: ${err.message}`);
      return;
    }
    state.perParticipant.set(participant, { handler });
    reconsiderParticipant(participant);
  }

  function unwireParticipant(participant) {
    const entry = state.perParticipant.get(participant);
    if (!entry) return;
    try { participant.off("videoStreamsUpdated", entry.handler); } catch (_) {}
    state.perParticipant.delete(participant);
    if (state.activeParticipant === participant) {
      detachStream().catch(() => {});
    }
  }

  function ensureCaptureCanvas() {
    if (state.captureCanvas) return;
    state.captureCanvas = document.createElement("canvas");
    state.captureCtx = state.captureCanvas.getContext("2d");
  }

  // Cheap perceptual signature: average luma over a coarse grid. Used to skip
  // periodic re-reviews when the shared screen has not materially changed, so
  // Lisa does not talk over the presenter or repeat herself.
  function computeFrameSignature(ctx, w, h) {
    const grid = 12;
    const STEP = 4; // sample every 4th pixel: ~16x cheaper, plenty for change detection
    try {
      const data = ctx.getImageData(0, 0, w, h).data;
      const sums = new Float64Array(grid * grid);
      const counts = new Uint32Array(grid * grid);
      for (let y = 0; y < h; y += STEP) {
        const gy = Math.min(grid - 1, (y * grid / h) | 0);
        for (let x = 0; x < w; x += STEP) {
          const gx = Math.min(grid - 1, (x * grid / w) | 0);
          const i = (y * w + x) * 4;
          const luma = data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114;
          const cell = gy * grid + gx;
          sums[cell] += luma;
          counts[cell]++;
        }
      }
      const sig = new Float64Array(grid * grid);
      for (let k = 0; k < sig.length; k++) sig[k] = counts[k] ? sums[k] / counts[k] : 0;
      return sig;
    } catch (err) {
      // Tainted canvas or other failure: treat as "changed" so we always send.
      return null;
    }
  }

  function signaturesSimilar(a, b, threshold) {
    if (!a || !b || a.length !== b.length) return false;
    let sum = 0;
    for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
    return (sum / a.length) < (threshold || 7);
  }

  function startPeriodicTimer() {
    stopPeriodicTimer();
    if (!state.periodicIntervalS) return;
    state.periodicTimer = setInterval(() => {
      if (!hasActiveShare()) return;
      captureAndSend("periodic").catch(err => log(`periodic capture failed: ${err.message}`));
    }, state.periodicIntervalS * 1000);
    log(`Periodic re-review enabled every ${state.periodicIntervalS}s`);
  }

  function stopPeriodicTimer() {
    if (state.periodicTimer) {
      clearInterval(state.periodicTimer);
      state.periodicTimer = null;
    }
  }

  async function captureAndSend(trigger) {
    const triggerName = trigger || "on_demand";
    if (!state.clientId) {
      log("captureAndSend called with no clientId; ignoring");
      return { ok: false, reason: "no_client_id" };
    }
    if (!state.activeVideoEl) {
      setStatus("no share", "err");
      return { ok: false, reason: "no_active_share" };
    }
    setStatus("thinking");
    ensureCaptureCanvas();
    const v = state.activeVideoEl;
    const srcW = v.videoWidth || (v.naturalWidth || 1280);
    const srcH = v.videoHeight || (v.naturalHeight || 720);
    if (!srcW || !srcH) {
      setStatus("no frame yet", "err");
      return { ok: false, reason: "no_frame_yet" };
    }
    const scale = Math.min(1, state.maxDim / Math.max(srcW, srcH));
    const cw = Math.max(64, Math.floor(srcW * scale));
    const ch = Math.max(64, Math.floor(srcH * scale));
    state.captureCanvas.width = cw;
    state.captureCanvas.height = ch;
    try {
      state.captureCtx.drawImage(v, 0, 0, cw, ch);
    } catch (err) {
      log(`drawImage failed: ${err.message}`);
      setStatus("draw error", "err");
      return { ok: false, reason: "draw_failed", error: err.message };
    }

    // Change-detection guard: for automatic periodic re-reviews, skip the
    // round-trip + model call + speech when the screen looks essentially the
    // same as the last frame we acted on. On-demand and share-start always go.
    const signature = computeFrameSignature(state.captureCtx, cw, ch);
    if (triggerName === "periodic" && signaturesSimilar(signature, state.lastSentSignature)) {
      setStatus("share active", "ok");
      return { ok: true, spoke: false, reason: "no_significant_change", skipped: true };
    }
    let dataUrl;
    try {
      dataUrl = state.captureCanvas.toDataURL("image/jpeg", 0.7);
    } catch (err) {
      log(`toDataURL failed: ${err.message}`);
      setStatus("encode error", "err");
      return { ok: false, reason: "encode_failed", error: err.message };
    }
    const jpegBase64 = (dataUrl.split(",", 2)[1] || "").trim();
    if (!jpegBase64) {
      setStatus("encode error", "err");
      return { ok: false, reason: "empty_encoding" };
    }
    // We are about to act on this frame; anchor the change-detection baseline
    // to it so subsequent periodic frames are compared against what Lisa saw.
    if (signature) state.lastSentSignature = signature;
    try {
      const resp = await fetch("/api/vision/screen-frame", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientId: state.clientId, jpegBase64, trigger: triggerName }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || data.ok === false) {
        const errMsg = data.error || data.reason || `HTTP ${resp.status}`;
        setStatus(`error: ${errMsg}`, "err");
        log(`vision POST failed: ${errMsg}`);
        return { ok: false, reason: data.reason || "http_error", error: errMsg };
      }
      if (data.spoke) {
        setStatus("spoke", "ok");
        log(`Lisa commented (trigger=${triggerName}): ${data.description || ""}`);
        setTimeout(() => { if (state.activeStream) setStatus("share active", "ok"); }, 2500);
      } else {
        setStatus("share active", "ok");
        log(`vision stored but did not speak (reason=${data.reason || "?"})`);
      }
      return data;
    } catch (err) {
      log(`vision fetch failed: ${err.message}`);
      setStatus(`error: ${err.message}`, "err");
      return { ok: false, reason: "fetch_failed", error: err.message };
    }
  }

  function hasActiveShare() {
    return Boolean(state.activeStream && state.activeVideoEl);
  }

  function setStatusElement(el) {
    state.statusEl = el || null;
    if (state.statusEl) setStatus(state.call ? (hasActiveShare() ? "share active" : "no share") : "idle");
  }

  function attach(call, opts) {
    const options = opts || {};
    if (state.call) detach();
    if (!call) return;
    state.call = call;
    state.clientId = options.clientId || null;
    state.autoOnShareStart = options.autoOnShareStart !== false;
    state.maxDim = options.maxDim || 512;
    state.periodicIntervalS = Math.max(0, Number(options.periodicIntervalS) || 0);
    state.lastSentSignature = null;
    if (options.statusEl) state.statusEl = options.statusEl;
    setStatus("watching", "ok");
    startPeriodicTimer();
    state.participantsHandler = ({ added, removed }) => {
      (added || []).forEach(wireParticipant);
      (removed || []).forEach(unwireParticipant);
    };
    try {
      call.on("remoteParticipantsUpdated", state.participantsHandler);
    } catch (err) {
      log(`remoteParticipantsUpdated subscribe failed: ${err.message}`);
    }
    (call.remoteParticipants || []).forEach(wireParticipant);
    log("Watcher attached to ACS call");
  }

  function detach() {
    if (state.participantsHandler && state.call) {
      try { state.call.off("remoteParticipantsUpdated", state.participantsHandler); } catch (_) {}
    }
    stopPeriodicTimer();
    state.lastSentSignature = null;
    state.participantsHandler = null;
    state.perParticipant.forEach((entry, participant) => {
      try { participant.off("videoStreamsUpdated", entry.handler); } catch (_) {}
    });
    state.perParticipant.clear();
    detachStream().catch(() => {});
    state.call = null;
    state.clientId = null;
    setStatus("idle");
    log("Watcher detached");
  }

  window.LisaVision = {
    attach,
    detach,
    captureAndSend,
    hasActiveShare,
    setStatusElement,
  };
})();
