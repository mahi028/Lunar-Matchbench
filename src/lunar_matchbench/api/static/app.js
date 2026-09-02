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
  const diagnosis = document.getElementById("diagnosis");

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
    const stepper = document.getElementById("stepper");
    if (stepper) stepper.classList.remove("stepper--failed");
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

  function markPipelineOutcome(success) {
    // Eight green "done" pills above a failure banner is a contradiction; the
    // steps did all execute, but they should not read as a successful run.
    const stepper = document.getElementById("stepper");
    if (stepper) stepper.classList.toggle("stepper--failed", !success);
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

  function fmtMB(bytes) {
    if (!bytes) return "0 MB";
    return (bytes / 1e6).toFixed(1) + " MB";
  }

  // Two very different failures look identical in a bare error string:
  // "these images genuinely do not correspond" and "we may have been looking
  // at the wrong part of the strip". The pipeline already distinguishes them
  // via the scan-line lock, so say which one this was.
  function renderDiagnosis(data) {
    const prov = data.provenance || {};
    const loc = prov.lroc_localization;
    const tx = data.transfer;
    const rows = [];

    if (loc) {
      const ok = loc.confident;
      rows.push(`
        <div class="diag-row ${ok ? "diag-good" : "diag-warn"}">
          <span class="diag-label">Scan-line lock</span>
          <span class="diag-value">${ok ? "CONFIDENT" : "NOT VERIFIED"}</span>
          <span class="diag-detail">
            ${loc.best_n} correlated matches vs ${loc.min_confident_matches} threshold
            &middot; searched ${Math.round((loc.strip_fraction_searched || 0) * 100)}% of the
            ${loc.total_lines}-line strip
            (${loc.windows_fetched} window${loc.windows_fetched === 1 ? "" : "s"})
          </span>
        </div>`);

      if (!ok && loc.whole_strip_searched) {
        // The strongest honest statement available: everything was looked at.
        rows.push(`
          <p class="diag-note">
            The <strong>entire LROC strip was searched</strong>
            (${loc.lines_searched} of ${loc.total_lines} lines) and no location
            correlated above the threshold &mdash; the best anywhere was
            ${loc.best_n}. This is a <strong>genuine content or illumination
            mismatch</strong>, not a mislocalized patch. This coordinate really
            does not register against this reference image.
          </p>`);
      } else if (!ok) {
        const drift = Math.abs(
          (loc.used_center_line || 0) - (loc.approx_center_line || 0));
        const pct = Math.round((loc.strip_fraction_searched || 0) * 100);
        rows.push(`
          <p class="diag-note">
            Only <strong>${pct}% of the LROC strip was searched</strong>, so this patch
            is the raw pushbroom geometry estimate rather than a visually verified
            location. This run therefore does <em>not</em> show that the two images fail
            to correspond &mdash; it may simply not have been looking at the matching
            part of the strip.
            ${drift ? `Geometry and search peak differ by ${drift} lines.` : ""}
          </p>`);
      } else {
        const drift = Math.abs(
          (loc.used_center_line || 0) - (loc.approx_center_line || 0));
        rows.push(`
          <p class="diag-note">
            The patch was visually locked onto the LROC strip, ${drift} lines from the
            raw geometry estimate, so this result reflects the imagery itself.
          </p>`);
      }
    }

    if (tx && (tx.fetched_bytes || tx.cached_bytes)) {
      const fetched = tx.fetched_bytes || 0;
      const cached = tx.cached_bytes || 0;
      const total = tx.product_bytes
        ? ` of a ${fmtMB(tx.product_bytes)} product` : "";
      // Headlining "0 MB" when every read came from disk reads as though nothing
      // happened. Lead with whichever number describes what actually occurred,
      // and always say which of the two it was -- a pre-warmed demo must never
      // look like a live fetch.
      const fromCache = fetched === 0 && cached > 0;
      rows.push(`
        <div class="diag-row diag-info">
          <span class="diag-label">${fromCache ? "Served from cache" : "Data streamed"}</span>
          <span class="diag-value">${fmtMB(fromCache ? cached : fetched)}</span>
          <span class="diag-detail">
            ${fromCache
              ? `read from the local range cache${total} &mdash; no imagery pulled over the network this run`
              : `over ${tx.requests} byte-range request${tx.requests === 1 ? "" : "s"}${total}` +
                (cached ? ` &middot; plus ${fmtMB(cached)} from cache` : "")}
          </span>
        </div>`);
    }

    diagnosis.innerHTML = rows.join("");
    diagnosis.hidden = rows.length === 0;
  }

  function showSummary(success, errText, data) {
    markPipelineOutcome(success);
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

    renderDiagnosis(data);

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
