// Lunar-MatchBench Web UI Frontend Controller
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("reg-form");
  const runBtn = document.getElementById("run-btn");
  const btnText = document.getElementById("btn-text");
  const pipelineCard = document.getElementById("pipeline-card");
  const summaryCard = document.getElementById("summary-card");
  const summaryTitle = document.getElementById("summary-title");
  const statusMsg = document.getElementById("status-msg");
  const metricsRow = document.getElementById("metrics-row");
  const provPre = document.getElementById("provenance-pre");
  const errorMsg = document.getElementById("error-msg");
  const retryBtn = document.getElementById("retry-btn");

  const stageViewer = document.getElementById("stage-viewer");
  const stageIndexEl = document.getElementById("stage-index");
  const stageTitleEl = document.getElementById("stage-title");
  const stageDescEl = document.getElementById("stage-desc");
  const stageCounterEl = document.getElementById("stage-counter");
  const stageImgA = document.getElementById("stage-img-a");
  const stageImgB = document.getElementById("stage-img-b");
  const stagePrevBtn = document.getElementById("stage-prev");
  const stageNextBtn = document.getElementById("stage-next");

  const STAGE_META = {
    extracted: { index: "STEP 1", title: "Source imagery located", desc: "CH2 patch + LROC candidate" },
    keypoints: { index: "STEP 2", title: "Keypoints detected", desc: "independent per-image feature detection" },
    matches:   { index: "STEP 3", title: "Candidate correspondences", desc: "raw matches, pre-verification" },
    inliers:   { index: "STEP 4", title: "MAGSAC++ verification", desc: "geometrically consistent vs rejected" },
    final:     { index: "STEP 5", title: "Result", desc: "" },
  };
  const STAGE_ORDER = Object.keys(STAGE_META);

  // Overlay carousel state: stages accumulate as they arrive from polling,
  // but only one is ever shown at a time -- crossfaded between two stacked
  // <img> elements so switching never causes a layout jump. `autoFollow`
  // keeps the view pinned to the newest stage as it streams in; the moment
  // the user clicks Prev it's turned off so their navigation isn't yanked
  // out from under them, and clicking Next back up to the latest turns it
  // on again.
  let stageKeys = [];
  let stageUrls = {};
  let currentIndex = -1;
  let autoFollow = true;
  let frontImg = stageImgA;
  let backImg = stageImgB;

  function renderStage() {
    if (currentIndex < 0 || currentIndex >= stageKeys.length) return;
    const key = stageKeys[currentIndex];
    const meta = STAGE_META[key];
    const url = stageUrls[key];

    backImg.src = url;
    backImg.alt = meta.title;
    backImg.onload = () => {
      frontImg.classList.remove("visible");
      backImg.classList.add("visible");
      [frontImg, backImg] = [backImg, frontImg];
    };

    stageIndexEl.textContent = meta.index;
    stageTitleEl.textContent = meta.title;
    stageDescEl.textContent = meta.desc;
    stageCounterEl.textContent = `${currentIndex + 1} / ${stageKeys.length}`;
    stagePrevBtn.disabled = currentIndex <= 0;
    stageNextBtn.disabled = currentIndex >= stageKeys.length - 1;
    stageViewer.hidden = false;
  }

  stagePrevBtn.addEventListener("click", () => {
    if (currentIndex <= 0) return;
    autoFollow = false;
    currentIndex -= 1;
    renderStage();
  });
  stageNextBtn.addEventListener("click", () => {
    if (currentIndex >= stageKeys.length - 1) return;
    currentIndex += 1;
    autoFollow = currentIndex === stageKeys.length - 1;
    renderStage();
  });
  document.addEventListener("keydown", (e) => {
    if (pipelineCard.hidden) return;
    if (e.key === "ArrowLeft") stagePrevBtn.click();
    if (e.key === "ArrowRight") stageNextBtn.click();
  });

  // Preset buttons
  document.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("lat").value = btn.dataset.lat;
      document.getElementById("lon").value = btn.dataset.lon;
    });
  });

  retryBtn.addEventListener("click", () => {
    summaryCard.hidden = true;
    pipelineCard.hidden = true;
    form.scrollIntoView({ behavior: "smooth" });
  });

  function resetStepper() {
    for (let i = 1; i <= 8; i++) {
      const stepEl = document.getElementById(`step-${i}`);
      if (stepEl) stepEl.className = "step";
    }
    stageKeys = [];
    stageUrls = {};
    currentIndex = -1;
    autoFollow = true;
    stageImgA.classList.remove("visible");
    stageImgB.classList.remove("visible");
    stageImgA.src = "";
    stageImgB.src = "";
    frontImg = stageImgA;
    backImg = stageImgB;
    stageViewer.hidden = true;
  }

  function setStep(stepIndex) {
    for (let i = 1; i <= 8; i++) {
      const stepEl = document.getElementById(`step-${i}`);
      if (!stepEl) continue;
      if (i < stepIndex) stepEl.className = "step done";
      else if (i === stepIndex) stepEl.className = "step active";
      else stepEl.className = "step";
    }
  }

  function revealStages(stepImageUrls) {
    if (!stepImageUrls) return;
    let added = false;
    for (const key of STAGE_ORDER) {
      if (stepImageUrls[key] && !(key in stageUrls)) {
        stageUrls[key] = stepImageUrls[key];
        stageKeys.push(key);
        added = true;
      }
    }
    if (!added) return;
    if (autoFollow) {
      currentIndex = stageKeys.length - 1;
      renderStage();
    } else {
      // Not following live -- still refresh the counter/Next button so the
      // user can see a new stage arrived without being yanked off what
      // they're currently looking at.
      stageCounterEl.textContent = `${currentIndex + 1} / ${stageKeys.length}`;
      stageNextBtn.disabled = currentIndex >= stageKeys.length - 1;
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const lat = parseFloat(document.getElementById("lat").value);
    const lon = parseFloat(document.getElementById("lon").value);
    const instrument = document.getElementById("instrument").value;
    const matcher = document.getElementById("matcher").value;

    summaryCard.hidden = true;
    resetStepper();
    pipelineCard.hidden = false;
    runBtn.disabled = true;
    btnText.textContent = "Processing…";
    setStep(1);
    statusMsg.textContent = "Submitting registration request…";
    pipelineCard.scrollIntoView({ behavior: "smooth" });

    try {
      const resp = await fetch("/api/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat, lon, instrument, matcher }),
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "Failed to start registration.");
      }

      const { job_id } = await resp.json();
      pollStatus(job_id);
    } catch (err) {
      showError(err.message);
    }
  });

  function pollStatus(jobId) {
    const timer = setInterval(async () => {
      try {
        const resp = await fetch(`/api/status/${jobId}`);
        if (!resp.ok) throw new Error("Status check failed");
        const data = await resp.json();

        if (data.progress_step) setStep(data.progress_step);
        if (data.progress_msg) statusMsg.textContent = data.progress_msg;
        revealStages(data.step_image_urls);

        if (data.status === "done" || data.status === "failed") {
          clearInterval(timer);
          setStep(9);
          fetchResult(jobId);
        }
      } catch (err) {
        clearInterval(timer);
        showError(err.message);
      }
    }, 1200);
  }

  async function fetchResult(jobId) {
    try {
      const resp = await fetch(`/api/result/${jobId}`);
      const data = await resp.json();

      revealStages(data.step_image_urls);
      runBtn.disabled = false;
      btnText.textContent = "Run Registration";

      if (data.status === "failed" || !data.metrics) {
        showSummary(false, data.error || "Registration did not converge.", data);
        return;
      }
      showSummary(true, null, data);
    } catch (err) {
      showError(err.message);
    }
  }

  function showSummary(success, errText, data) {
    summaryTitle.textContent = success ? "Registration succeeded" : "Registration did not converge";
    errorMsg.hidden = !!success;
    if (!success) errorMsg.textContent = errText;

    if (success) {
      const m = data.metrics;
      metricsRow.innerHTML = `
        <div class="metric-card highlight">
          <span class="metric-val">${m.n_inliers}</span>
          <span class="metric-label">Inlier tie-points</span>
        </div>
        <div class="metric-card">
          <span class="metric-val">${m.inlier_ratio_pct}%</span>
          <span class="metric-label">of ${m.n_raw_matches} raw</span>
        </div>
        <div class="metric-card">
          <span class="metric-val">${m.rmse_px.toFixed(3)} px</span>
          <span class="metric-label">Reprojection RMSE</span>
        </div>
        <div class="metric-card">
          <span class="metric-val">${(m.spatial_uniformity * 100).toFixed(1)}%</span>
          <span class="metric-label">Spatial coverage</span>
        </div>
        <div class="metric-card">
          <span class="metric-val">${m.elapsed_sec.toFixed(2)}s</span>
          <span class="metric-label">${m.matcher} runtime</span>
        </div>
      `;
    } else {
      metricsRow.innerHTML = `
        <div class="metric-card fail">
          <span class="metric-val">FAILED</span>
          <span class="metric-label">see step images above for where it broke down</span>
        </div>
      `;
    }

    if (data.provenance) {
      provPre.textContent = JSON.stringify(data.provenance, null, 2);
    }

    summaryCard.hidden = false;
    summaryCard.scrollIntoView({ behavior: "smooth" });
  }

  function showError(msg) {
    runBtn.disabled = false;
    btnText.textContent = "Run Registration";
    showSummary(false, msg, {});
  }
});
