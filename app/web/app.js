const schemaSelect = document.getElementById("schema");
const contextSelect = document.getElementById("context");
const promptInput = document.getElementById("prompt");
let sttOutput = null;
const resultOutput = document.getElementById("result");
const lintXmlCheckbox = document.getElementById("lintXml");
const saveMissionCheckbox = document.getElementById("saveMission");
const tcpHostInput = document.getElementById("tcpHost");
const tcpPortInput = document.getElementById("tcpPort");
const debugModeCheckbox = document.getElementById("debugMode");
const micBtn = document.getElementById("micBtn");
const statusEl = document.getElementById("status");
const settingsBtn = document.getElementById("settingsBtn");
const settingsPanel = document.getElementById("settingsPanel");
const settingsWrapper = document.querySelector(".settings-wrapper");
const missionsBtn = document.getElementById("missionsBtn");
const missionsPanel = document.getElementById("missionsPanel");
const missionsWrapper = document.querySelector(".missions-wrapper");
const sttContainer = document.getElementById("sttContainer");
const sttTemplate = document.getElementById("sttTemplate");
const xmlToast = document.getElementById("xmlToast");
const xmlToastClose = document.getElementById("xmlToastClose");
const debugCard = document.getElementById("debugCard");
const mapEl = document.getElementById("map");
const debugPolygonBtn = document.getElementById("debugPolygonBtn");
const savedMissionsSelect = document.getElementById("savedMissions");
const loadSavedMissionBtn = document.getElementById("loadSavedMission");
const sendSavedMissionBtn = document.getElementById("sendSavedMission");
const clearSavedMissionBtn = document.getElementById("clearSavedMission");

let recording = false;
let mediaRecorder = null;
let audioChunks = [];
let recordingStartedAt = 0;
let debugEnabled = false;
let mapInstance = null;
let mapHullLayer = null;
let mapVisitLayer = null;
let activeBaseLayer = "Satellite";
let lastFitBounds = null;
let mapResizeTimer = null;

const savedMissionKey = "gpt-mission-planner.savedMissions";

const clearMapOverlays = () => {
  if (mapHullLayer && mapInstance) {
    mapInstance.removeLayer(mapHullLayer);
    mapHullLayer = null;
  }
  if (mapVisitLayer) {
    mapVisitLayer.clearLayers();
  }
};

const normalizeGpsPoints = (raw) => {
  if (!raw) return [];
  let data = raw;
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch (err) {
      return [];
    }
  }

  const points = [];
  const pushPoint = (candidate) => {
    if (!candidate) return;
    if (Array.isArray(candidate) && candidate.length >= 2) {
      const lon = Number(candidate[0]);
      const lat = Number(candidate[1]);
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        points.push({ lat, lon });
      }
      return;
    }

    const lat = Number(candidate.lat ?? candidate.latitude);
    const lon = Number(candidate.lon ?? candidate.longitude);
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      points.push({ lat, lon });
      return;
    }

    const coords = candidate.coordinates || candidate.geometry?.coordinates;
    if (Array.isArray(coords) && coords.length >= 2) {
      const lonFromCoords = Number(coords[0]);
      const latFromCoords = Number(coords[1]);
      if (Number.isFinite(latFromCoords) && Number.isFinite(lonFromCoords)) {
        points.push({ lat: latFromCoords, lon: lonFromCoords });
      }
    }
  };

  if (Array.isArray(data)) {
    data.forEach((item) => pushPoint(item));
  } else if (Array.isArray(data.points)) {
    data.points.forEach((item) => pushPoint(item));
  } else if (Array.isArray(data.features)) {
    data.features.forEach((feature) => pushPoint(feature));
  } else if (data.geometry || data.coordinates) {
    pushPoint(data);
  }

  return points;
};

const convexHull = (points) => {
  if (!points || points.length < 3) return points || [];
  const sorted = [...points].sort((a, b) => {
    if (a.lon === b.lon) return a.lat - b.lat;
    return a.lon - b.lon;
  });

  const cross = (o, a, b) =>
    (a.lon - o.lon) * (b.lat - o.lat) - (a.lat - o.lat) * (b.lon - o.lon);

  const lower = [];
  for (const p of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
      lower.pop();
    }
    lower.push(p);
  }

  const upper = [];
  for (let i = sorted.length - 1; i >= 0; i -= 1) {
    const p = sorted[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
      upper.pop();
    }
    upper.push(p);
  }

  upper.pop();
  lower.pop();
  return lower.concat(upper);
};

const drawGpsHull = (rawPoints) => {
  if (!mapInstance || !window.L) return;
  const points = normalizeGpsPoints(rawPoints);
  if (!points.length) return;

  clearMapOverlays();

  const hull = convexHull(points);
  if (hull.length >= 3) {
    mapHullLayer = window.L.polygon(
      hull.map((p) => [p.lat, p.lon]),
      {
        color: "#ff7a00",
        weight: 2,
        fill: false,
      }
    ).addTo(mapInstance);
  }

  mapInstance.invalidateSize();
  if (points.length === 1) {
    mapInstance.setView([points[0].lat, points[0].lon], 20);
    return;
  }
  const fitPoints = hull.length >= 3 ? hull : points;
  const bounds = window.L.latLngBounds(fitPoints.map((p) => [p.lat, p.lon]));
  rememberAndFitBounds(bounds);
};

const getMapFitPadding = () => {
  const basePadding = 24;
  return {
    topLeft: window.L.point(basePadding, basePadding),
    bottomRight: window.L.point(basePadding, basePadding),
  };
};

const fitMapToBounds = (bounds, { animate = true } = {}) => {
  if (!mapInstance || !window.L || !bounds || !bounds.isValid()) return;
  const maxZoom = activeBaseLayer === "Satellite" ? 19 : 20;
  const padding = getMapFitPadding();
  mapInstance.fitBounds(bounds, {
    maxZoom,
    animate,
    paddingTopLeft: padding.topLeft,
    paddingBottomRight: padding.bottomRight,
  });
};

const rememberAndFitBounds = (bounds) => {
  lastFitBounds = bounds;
  fitMapToBounds(bounds);
};

const scheduleFitToLastBounds = () => {
  if (!lastFitBounds || !mapInstance) return;
  if (mapResizeTimer) {
    window.clearTimeout(mapResizeTimer);
  }
  mapResizeTimer = window.setTimeout(() => {
    mapInstance.invalidateSize();
    fitMapToBounds(lastFitBounds, { animate: false });
  }, 150);
};

const drawVisitPins = (visitPoints) => {
  if (!mapInstance || !window.L || !visitPoints || !visitPoints.length) return;
  if (!mapVisitLayer) {
    mapVisitLayer = window.L.layerGroup().addTo(mapInstance);
  }

  mapVisitLayer.clearLayers();

  visitPoints.forEach((point) => {
    const order = Number(point.order);
    const lat = Number(point.lat);
    const lon = Number(point.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    const label = Number.isFinite(order) ? String(order) : "";
    const marker = window.L.marker([lat, lon], {
      icon: window.L.divIcon({
        className: "visit-pin",
        html: `<span>${label}</span>`,
        iconSize: [26, 26],
        iconAnchor: [13, 13],
      }),
      interactive: false,
    });
    marker.addTo(mapVisitLayer);
  });
};

const loadDebugPolygon = async () => {
  setStatus("Loading debug polygon...");
  try {
    const res = await fetch("/debug_polygon");
    if (!res.ok) {
      setStatus("Failed to load debug polygon");
      return;
    }
    const payload = await res.json();
    if (payload.error) {
      setStatus(payload.error);
      return;
    }
    if (payload.treePoints) {
      drawGpsHull(payload.treePoints);
      if (payload.visitPoints) {
        drawVisitPins(payload.visitPoints);
      }
      setStatus(`Debug polygon loaded (${payload.treePoints.length} points)`);
      return;
    }
    setStatus("No debug polygon data received");
  } catch (err) {
    setStatus("Failed to load debug polygon");
  }
};

const loadSavedMission = async () => {
  if (!savedMissionsSelect) return;
  const missionId = savedMissionsSelect.value;
  if (!missionId) return;
  setStatus("Loading saved mission...");
  try {
    const res = await fetch(`/missions/${encodeURIComponent(missionId)}`);
    if (!res.ok) {
      setStatus("Failed to load saved mission");
      return;
    }
    const payload = await res.json();
    if (payload.error) {
      setStatus(payload.error);
      return;
    }
    if (payload.result && resultOutput) {
      resultOutput.value = payload.result;
    }
    if (payload.treePoints) {
      drawGpsHull(payload.treePoints);
    }
    if (payload.visitPoints) {
      drawVisitPins(payload.visitPoints);
    }
    setStatus("Saved mission loaded");
  } catch (err) {
    setStatus("Failed to load saved mission");
  }
};

const sendSavedMission = async () => {
  if (!savedMissionsSelect) return;
  const missionId = savedMissionsSelect.value;
  if (!missionId) return;
  setStatus("Sending saved mission...");
  try {
    const res = await fetch(`/missions/${encodeURIComponent(missionId)}/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tcpHost: tcpHostInput ? tcpHostInput.value.trim() || null : null,
        tcpPort: tcpPortInput ? tcpPortInput.value.trim() || null : null,
      }),
    });
    if (!res.ok) {
      setStatus("Failed to send mission");
      return;
    }
    const payload = await res.json();
    if (payload.error) {
      setStatus(payload.error);
      return;
    }
    if (payload.result && resultOutput) {
      resultOutput.value = payload.result;
      if (debugEnabled) {
        setToastVisible(true);
      }
    }
    setStatus("Mission sent to robot");
  } catch (err) {
    setStatus("Failed to send mission");
  }
};

const setStatus = (text) => {
  if (!statusEl) return;
  statusEl.textContent = text;
};

const loadSavedMissions = () => {
  try {
    const raw = localStorage.getItem(savedMissionKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    return [];
  }
};

const mergeMissionLists = (existing, incoming) => {
  const merged = new Map();
  existing.forEach((mission) => {
    if (mission && mission.id) {
      merged.set(mission.id, { ...mission });
    }
  });
  incoming.forEach((mission) => {
    if (!mission || !mission.id) return;
    const current = merged.get(mission.id);
    if (!current || (mission.createdAt || 0) >= (current.createdAt || 0)) {
      merged.set(mission.id, { ...current, ...mission });
      return;
    }
    merged.set(mission.id, { ...mission, ...current });
  });
  return Array.from(merged.values()).sort(
    (a, b) => (b.createdAt || 0) - (a.createdAt || 0)
  );
};

const hydrateSavedMissionsFromServer = async () => {
  try {
    const res = await fetch("/missions");
    if (!res.ok) return;
    const payload = await res.json();
    const missions = Array.isArray(payload.missions) ? payload.missions : [];
    if (!missions.length) return;
    const merged = mergeMissionLists(loadSavedMissions(), missions).slice(0, 25);
    localStorage.setItem(savedMissionKey, JSON.stringify(merged));
    populateSavedMissions();
  } catch (err) {
    // Ignore hydration errors to avoid breaking the UI.
  }
};

const saveMissionToStorage = (mission) => {
  if (!mission || !mission.id) return;
  const missions = loadSavedMissions();
  const existingIndex = missions.findIndex((item) => item.id === mission.id);
  if (existingIndex >= 0) {
    missions[existingIndex] = mission;
  } else {
    missions.unshift(mission);
  }
  const trimmed = missions.slice(0, 25);
  localStorage.setItem(savedMissionKey, JSON.stringify(trimmed));
};

const clearSavedMission = () => {
  if (!savedMissionsSelect) return;
  const missionId = savedMissionsSelect.value;
  if (!missionId) return;
  const missions = loadSavedMissions().filter((item) => item.id !== missionId);
  localStorage.setItem(savedMissionKey, JSON.stringify(missions));
  populateSavedMissions();
  setStatus("Saved mission cleared");
};

const populateSavedMissions = () => {
  if (!savedMissionsSelect) return;
  const missions = loadSavedMissions();
  savedMissionsSelect.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = missions.length ? "Select a mission" : "No saved missions";
  savedMissionsSelect.appendChild(placeholder);
  missions.forEach((mission) => {
    const option = document.createElement("option");
    option.value = mission.id;
    const promptLabel = mission.prompt ? mission.prompt.replace(/\s+/g, " ").trim() : mission.id;
    const dateLabel = mission.createdAt
      ? new Date(mission.createdAt * 1000).toLocaleString()
      : "";
    option.textContent = dateLabel ? `${dateLabel} — ${promptLabel.slice(0, 64)}` : promptLabel.slice(0, 64);
    savedMissionsSelect.appendChild(option);
  });
};

const setHidden = (element, hidden) => {
  if (!element) return;
  element.hidden = hidden;
  element.classList.toggle("is-hidden", hidden);
};

const isHidden = (element) => !element || element.hidden || element.classList.contains("is-hidden");

const setPanelOpen = (panel, button, open) => {
  if (!panel || !button) return;
  setHidden(panel, !open);
  button.setAttribute("aria-expanded", open ? "true" : "false");
};

const togglePanel = (panel, button) => {
  if (!panel || !button) return;
  setPanelOpen(panel, button, isHidden(panel));
};

const fetchSchemas = async () => {
  if (!schemaSelect) return;
  try {
    const res = await fetch("/schemas");
    const data = await res.json();
    schemaSelect.innerHTML = "";
    data.schemas.forEach((schema) => {
      const option = document.createElement("option");
      option.value = schema;
      option.textContent = schema;
      schemaSelect.appendChild(option);
    });
  } catch (err) {
    setStatus("Failed to load schemas");
  }
};

const fetchContextFiles = async () => {
  if (!contextSelect) return;
  try {
    const res = await fetch("/context_files");
    const data = await res.json();
    contextSelect.innerHTML = "";
    const noneOption = document.createElement("option");
    noneOption.value = "";
    noneOption.textContent = "No context";
    contextSelect.appendChild(noneOption);
    data.files.forEach((file) => {
      const option = document.createElement("option");
      option.value = file;
      option.textContent = file;
      contextSelect.appendChild(option);
    });
  } catch (err) {
    setStatus("Failed to load context files");
  }
};

const fetchTcpDefaults = async () => {
  try {
    const res = await fetch("/tcp_defaults");
    if (!res.ok) return;
    const data = await res.json();
    if (tcpHostInput && data.host) {
      tcpHostInput.value = String(data.host);
    }
    if (tcpPortInput && data.port) {
      tcpPortInput.value = String(data.port);
    }
  } catch (err) {
    // ignore
  }
};

const getSelectedContext = () => {
  if (!contextSelect) return [];
  const value = contextSelect.value?.trim();
  return value ? [value] : [];
};

const setMicState = (state) => {
  if (!micBtn) return;
  micBtn.classList.remove("mic-idle", "mic-recording", "mic-loading");
  if (state) {
    micBtn.classList.add(state);
  }
};

const autoGrowPrompt = () => {
  if (!promptInput) return;
  promptInput.style.height = "auto";
  promptInput.style.height = `${promptInput.scrollHeight}px`;
};

const mountSttOutput = () => {
  if (!sttContainer || !sttTemplate || sttContainer.childElementCount > 0) return;
  sttContainer.appendChild(sttTemplate.content.cloneNode(true));
  sttOutput = document.getElementById("stt");
};

const unmountSttOutput = () => {
  if (!sttContainer) return;
  sttContainer.innerHTML = "";
  sttOutput = null;
};

const setDebugVisibility = (enabled) => {
  debugEnabled = Boolean(enabled);
  if (debugEnabled) {
    setHidden(debugCard, false);
    mountSttOutput();
    return;
  }
  unmountSttOutput();
  setHidden(debugCard, true);
  setHidden(xmlToast, true);
};

const setToastVisible = (visible) => {
  setHidden(xmlToast, !visible);
};

const initLeafletMap = () => {
  if (!mapEl || !window.L) return;
  mapInstance = window.L.map(mapEl, {
    zoomControl: true,
    maxZoom: 20,
  }).setView([0, 0], 2);

  const streets = window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap contributors",
  });

  const satellite = window.L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 20,
      attribution: "Tiles &copy; Esri",
    }
  );

  const labels = window.L.tileLayer(
    "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 20,
      attribution: "Labels &copy; Esri",
    }
  );

  satellite.addTo(mapInstance);
  labels.addTo(mapInstance);

  const baseLayers = {
    Streets: streets,
    Satellite: satellite,
  };

  const overlays = {
    Labels: labels,
  };

  window.L.control.layers(baseLayers, overlays, { collapsed: true }).addTo(mapInstance);

  mapInstance.on("baselayerchange", (event) => {
    if (event && event.name) {
      activeBaseLayer = event.name;
    }
    scheduleFitToLastBounds();
  });
};

const startRecording = async () => {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus("Audio recording not supported");
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    let totalBytes = 0;
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        audioChunks.push(event.data);
        totalBytes += event.data.size;
      }
    };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      recording = false;
      setMicState("mic-loading");
      setStatus("Audio ready to upload");
      const durationMs = Date.now() - recordingStartedAt;
      await uploadAudio({ durationMs, recordedBytes: totalBytes });
    };
    mediaRecorder.start();
    recording = true;
    recordingStartedAt = Date.now();
    setMicState("mic-recording");
    setStatus("Recording audio...");
  } catch (err) {
    setStatus("Unable to access microphone");
  }
};

const uploadAudio = async ({ durationMs, recordedBytes }) => {
  if (!audioChunks.length) {
    setStatus("No audio recorded");
    setMicState("mic-idle");
    return;
  }

  const minDurationMs = 800;
  const minBytes = 2048;
  if ((durationMs || 0) < minDurationMs || (recordedBytes || 0) < minBytes) {
    setStatus("No speech detected");
    setMicState("mic-idle");
    return;
  }

  const audioBlob = new Blob(audioChunks, { type: "audio/webm" });
  await sendRequest({ audioBlob, includeText: false });
  setMicState("mic-idle");
};

const sendRequest = async ({ audioBlob, includeText = true } = {}) => {
  const payload = {
    text: includeText && promptInput ? promptInput.value.trim() || null : null,
    schema: schemaSelect ? schemaSelect.value : null,
    contextFiles: getSelectedContext(),
    lintXml: lintXmlCheckbox ? lintXmlCheckbox.checked : true,
    saveMission: saveMissionCheckbox ? saveMissionCheckbox.checked : true,
    tcpHost: tcpHostInput ? tcpHostInput.value.trim() || null : null,
    tcpPort: tcpPortInput ? tcpPortInput.value.trim() || null : null,
  };

  if (!payload.schema) {
    setStatus("Please select a schema");
    setMicState("mic-idle");
    return;
  }

  const formData = new FormData();
  formData.append("request", JSON.stringify(payload));
  if (audioBlob) {
    formData.append("file", audioBlob, "audio.webm");
  }

  if (resultOutput) resultOutput.value = "";
  setToastVisible(false);
  if (sttOutput) sttOutput.value = "";
  setStatus("Generating...");

  const response = await fetch("/generate", {
    method: "POST",
    body: formData,
  });

  if (!response.ok || !response.body) {
    setStatus("Generation failed");
    setMicState("mic-idle");
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    lines.forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      try {
        const message = JSON.parse(trimmed);
        if (message.stt) {
          if (sttOutput) sttOutput.value = message.stt;
          if (!payload.text && promptInput) {
            promptInput.value = message.stt;
          }
        }
        if (message.error) {
          setStatus(message.error);
        }
        if (message.result) {
          if (resultOutput) resultOutput.value = message.result;
          if (debugEnabled) {
            setToastVisible(true);
          }
        }
        if (message.mission) {
          saveMissionToStorage(message.mission);
          populateSavedMissions();
        }
        if (message.treePoints) {
          drawGpsHull(message.treePoints);
        }
        if (message.visitPoints) {
          drawVisitPins(message.visitPoints);
        }
      } catch (err) {
        console.error("Bad NDJSON", err);
      }
    });
  }

  setStatus("Done");
  setMicState("mic-idle");
};

if (micBtn) {
  micBtn.addEventListener("click", async () => {
    if (recording) {
      if (mediaRecorder) {
        mediaRecorder.stop();
      }
      return;
    }
    await startRecording();
  });
}
if (settingsBtn) {
  settingsBtn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    togglePanel(settingsPanel, settingsBtn);
  });
}
if (missionsBtn) {
  missionsBtn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    togglePanel(missionsPanel, missionsBtn);
  });
}
if (settingsPanel) {
  settingsPanel.addEventListener("click", (event) => {
    event.stopPropagation();
  });
}
if (missionsPanel) {
  missionsPanel.addEventListener("click", (event) => {
    event.stopPropagation();
  });
}
document.addEventListener("click", (event) => {
  if (!isHidden(settingsPanel)) {
    if (
      (settingsWrapper && settingsWrapper.contains(event.target)) ||
      (settingsPanel && settingsPanel.contains(event.target)) ||
      (settingsBtn && settingsBtn.contains(event.target))
    ) {
      return;
    }
    setPanelOpen(settingsPanel, settingsBtn, false);
  }
  if (!isHidden(missionsPanel)) {
    if (
      (missionsWrapper && missionsWrapper.contains(event.target)) ||
      (missionsPanel && missionsPanel.contains(event.target)) ||
      (missionsBtn && missionsBtn.contains(event.target))
    ) {
      return;
    }
    setPanelOpen(missionsPanel, missionsBtn, false);
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setPanelOpen(settingsPanel, settingsBtn, false);
    setPanelOpen(missionsPanel, missionsBtn, false);
  }
});

if (xmlToastClose) {
  xmlToastClose.addEventListener("click", () => {
    setToastVisible(false);
  });
}

if (promptInput) {
  promptInput.addEventListener("input", autoGrowPrompt);
  autoGrowPrompt();
}
if (debugModeCheckbox) {
  setDebugVisibility(debugModeCheckbox.checked);
  debugModeCheckbox.addEventListener("change", (event) => {
    setDebugVisibility(event.target.checked);
  });
} else {
  setDebugVisibility(false);
}
if (debugPolygonBtn) {
  debugPolygonBtn.addEventListener("click", () => {
    loadDebugPolygon();
  });
}
if (loadSavedMissionBtn) {
  loadSavedMissionBtn.addEventListener("click", () => {
    loadSavedMission();
  });
}
if (sendSavedMissionBtn) {
  sendSavedMissionBtn.addEventListener("click", () => {
    sendSavedMission();
  });
}
if (clearSavedMissionBtn) {
  clearSavedMissionBtn.addEventListener("click", () => {
    clearSavedMission();
  });
}
fetchSchemas();
fetchContextFiles();
fetchTcpDefaults();
initLeafletMap();
window.addEventListener("resize", scheduleFitToLastBounds);
hydrateSavedMissionsFromServer();
populateSavedMissions();
setStatus("Ready");
