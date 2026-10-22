// Voice input: tap the mic, speak, tap again. Your words are typed into the chat box.
(function () {
  const micBtn = document.getElementById("micBtn");
  const input = document.getElementById("chatInput");
  if (!micBtn || !input) return;

  // Hide the button on browsers that can't record audio
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
    micBtn.hidden = true;
    return;
  }

  const MAX_SECONDS = 60;
  const SILENT_BELOW = 0.015; // loudest moment of a real voice is usually far above this
  let recorder = null;
  let stream = null;
  let chunks = [];
  let stopTimer = null;

  // Loudness meter, used to tell "you were silent" from "the speech model misheard"
  let audioCtx = null;
  let levelTimer = null;
  let peak = 0;
  let meterWorks = false;

  function pickMime() {
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    return types.find((t) => MediaRecorder.isTypeSupported(t)) || "";
  }

  function setState(state) {
    micBtn.classList.toggle("recording", state === "recording");
    micBtn.classList.toggle("busy", state === "busy");
    micBtn.disabled = state === "busy";
    const label =
      state === "recording" ? "Stop recording" : state === "busy" ? "Working..." : "Speak your message";
    micBtn.title = label;
    micBtn.setAttribute("aria-label", label);
  }

  function startMeter(mediaStream) {
    peak = 0;
    meterWorks = false;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      audioCtx = new Ctx();
      const source = audioCtx.createMediaStreamSource(mediaStream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      Promise.resolve(audioCtx.resume()).then(() => {
        meterWorks = audioCtx && audioCtx.state === "running";
      }).catch(() => {});
      levelTimer = setInterval(() => {
        analyser.getByteTimeDomainData(data);
        let loudest = 0;
        for (let i = 0; i < data.length; i++) loudest = Math.max(loudest, Math.abs(data[i] - 128));
        peak = Math.max(peak, loudest / 128);
      }, 80);
    } catch (e) {
      meterWorks = false;
    }
  }

  function stopMeter() {
    clearInterval(levelTimer);
    levelTimer = null;
    if (audioCtx) {
      try {
        audioCtx.close();
      } catch (e) {
        /* ignore */
      }
      audioCtx = null;
    }
  }

  async function start() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      alert("The microphone is blocked. Allow the microphone for this site in your browser settings, then try again.");
      return;
    }
    chunks = [];
    const mime = pickMime();
    recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size) chunks.push(e.data);
    };
    recorder.onstop = onStopped;
    startMeter(stream);
    recorder.start();
    setState("recording");
    stopTimer = setTimeout(stop, MAX_SECONDS * 1000);
  }

  function stop() {
    clearTimeout(stopTimer);
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }

  async function onStopped() {
    stream.getTracks().forEach((t) => t.stop());
    const heardSomething = !meterWorks || peak >= SILENT_BELOW;
    stopMeter();

    const type = (recorder.mimeType || "audio/webm").split(";")[0];
    const blob = new Blob(chunks, { type });
    chunks = [];
    if (blob.size < 1000) {
      setState("idle");
      return;
    }
    if (!heardSomething) {
      setState("idle");
      alert(
        "I couldn't hear anything from your microphone. Check that the right microphone is selected " +
          "and not muted, then try again."
      );
      return;
    }
    setState("busy");
    try {
      const res = await fetch("/api/transcribe", {
        method: "POST",
        headers: { "Content-Type": type },
        body: blob,
        credentials: "same-origin",
      });
      let data = null;
      try {
        data = await res.json();
      } catch (e) {
        /* no JSON body */
      }
      if (!res.ok) {
        const detail = data && typeof data.detail === "string" ? data.detail : "";
        throw new Error(detail || "I couldn't turn that into text. Please try again.");
      }
      const text = (data.text || "").trim();
      if (!text) {
        alert("I didn't catch any words. Please try again, a little closer to the microphone.");
      } else {
        input.value = input.value ? input.value + " " + text : text;
      }
      input.focus();
    } catch (e) {
      alert(e.message);
    } finally {
      setState("idle");
    }
  }

  micBtn.addEventListener("click", () => {
    if (recorder && recorder.state === "recording") stop();
    else start();
  });
})();
