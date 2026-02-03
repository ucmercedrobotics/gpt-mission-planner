const schemaSelect = document.getElementById("schema");
const contextSelect = document.getElementById("context");
const promptInput = document.getElementById("prompt");
const sttOutput = document.getElementById("stt");
const resultOutput = document.getElementById("result");
const lintXmlCheckbox = document.getElementById("lintXml");
const sendBtn = document.getElementById("sendBtn");
const micBtn = document.getElementById("micBtn");
const recordBtn = document.getElementById("recordBtn");
const statusEl = document.getElementById("status");

let recognition = null;
let recording = false;
let mediaRecorder = null;
let audioChunks = [];

const setStatus = (text) => {
  statusEl.textContent = text;
};

const fetchSchemas = async () => {
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
  try {
    const res = await fetch("/context_files");
    const data = await res.json();
    contextSelect.innerHTML = "";
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

const getSelectedContext = () =>
  Array.from(contextSelect.selectedOptions).map((opt) => opt.value);

const initSpeechRecognition = () => {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    micBtn.disabled = true;
    micBtn.textContent = "Dictation not supported";
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.continuous = false;
  recognition.interimResults = true;

  recognition.onresult = (event) => {
    let transcript = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      transcript += event.results[i][0].transcript;
    }
    sttOutput.value = transcript.trim();
    promptInput.value = transcript.trim();
  };

  recognition.onerror = () => {
    setStatus("Dictation error");
  };

  recognition.onend = () => {
    micBtn.textContent = "Start dictation";
    setStatus("Dictation stopped");
  };
};

const toggleDictation = () => {
  if (!recognition) {
    return;
  }
  if (micBtn.textContent.includes("Stop")) {
    recognition.stop();
    micBtn.textContent = "Start dictation";
    return;
  }
  recognition.start();
  micBtn.textContent = "Stop dictation";
  setStatus("Listening...");
};

const startRecording = async () => {
  if (recording) {
    mediaRecorder.stop();
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus("Audio recording not supported");
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        audioChunks.push(event.data);
      }
    };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      recording = false;
      recordBtn.textContent = "Record & upload audio";
      setStatus("Audio ready to upload");
      await uploadAudio();
    };
    mediaRecorder.start();
    recording = true;
    recordBtn.textContent = "Stop recording";
    setStatus("Recording audio...");
  } catch (err) {
    setStatus("Unable to access microphone");
  }
};

const uploadAudio = async () => {
  if (!audioChunks.length) {
    setStatus("No audio recorded");
    return;
  }

  const audioBlob = new Blob(audioChunks, { type: "audio/webm" });
  await sendRequest({ audioBlob });
};

const sendRequest = async ({ audioBlob } = {}) => {
  const payload = {
    text: promptInput.value.trim() || null,
    schema: schemaSelect.value,
    contextFiles: getSelectedContext(),
    lintXml: lintXmlCheckbox.checked,
  };

  if (!payload.schema) {
    setStatus("Please select a schema");
    return;
  }

  const formData = new FormData();
  formData.append("request", JSON.stringify(payload));
  if (audioBlob) {
    formData.append("file", audioBlob, "audio.webm");
  }

  resultOutput.value = "";
  sttOutput.value = "";
  setStatus("Generating...");

  const response = await fetch("/generate", {
    method: "POST",
    body: formData,
  });

  if (!response.ok || !response.body) {
    setStatus("Generation failed");
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
          sttOutput.value = message.stt;
          if (!payload.text) {
            promptInput.value = message.stt;
          }
        }
        if (message.error) {
          setStatus(message.error);
        }
        if (message.result) {
          resultOutput.value = message.result;
        }
      } catch (err) {
        console.error("Bad NDJSON", err);
      }
    });
  }

  setStatus("Done");
};

sendBtn.addEventListener("click", () => sendRequest());
micBtn.addEventListener("click", toggleDictation);
recordBtn.addEventListener("click", async () => {
  await startRecording();
});

fetchSchemas();
fetchContextFiles();
initSpeechRecognition();
