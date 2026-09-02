// Acquisition context: the facts about the two images that explain the result.
//
// Cross-mission registration is hard for reasons that live in the metadata --
// a five-times resolution gap, years between the two passes, a sun that had
// moved. The metrics panel says how well it went; this says what it was up
// against.

function row(label, value, note, accent) {
  return `
    <div class="ctx-row">
      <span class="eyebrow">${label}</span>
      <b class="num"${accent ? ` style="color:${accent}"` : ""}>${value}</b>
      <span class="ctx-note">${note}</span>
    </div>`;
}

function parseUtc(value) {
  if (!value) return null;
  const d = new Date(String(value).replace(" ", "T"));
  return Number.isNaN(d.getTime()) ? null : d;
}

export function renderContext(container, result) {
  const p = result.provenance || {};
  if (!p.lroc_product_id && !p.ch2_gsd_m) {
    container.innerHTML = "";
    return;
  }

  const ch2Gsd = p.ch2_gsd_m;
  const lrocGsd = p.lroc_gsd_m;
  const rows = [];

  if (ch2Gsd && lrocGsd) {
    const ratio = ch2Gsd / lrocGsd;
    const ground = (ch2Gsd * (result.patch_size || 1024)) / 1000;
    rows.push(row(
      "Resolution gap",
      `${ratio.toFixed(1)}x`,
      `${ch2Gsd} m/px on Chandrayaan-2 against ${lrocGsd} m/px on LROC. One CH2 pixel ` +
      `covers about ${ratio.toFixed(1)} LROC pixels, so the reference is resampled down ` +
      `to match before anything is compared.`,
    ));
    rows.push(row(
      "Ground covered",
      `${ground.toFixed(2)} km`,
      `Both patches show the same square of surface, ${ground.toFixed(2)} km on a side, ` +
      `which is why they can be overlaid directly.`,
    ));
  }

  // The sun moves. Two images years apart are lit from different angles, which
  // is the central difficulty this whole problem statement is about.
  const lrocTime = parseUtc(p.lroc_start_time);
  const ch2Time = parseUtc(p.ch2_start_time) || parseUtc(
    (p.lroc_filename || "").match(/_(20\d{6})T/)?.[1]);
  if (lrocTime && ch2Time) {
    const days = Math.abs(Math.round((lrocTime - ch2Time) / 86400000));
    rows.push(row(
      "Time apart",
      `${days.toLocaleString()} days`,
      `The two passes are ${days.toLocaleString()} days apart, so the sun sat at a ` +
      `different angle and the shadows fall differently. Matching across that is the ` +
      `hard part of the problem.`,
    ));
  } else if (lrocTime) {
    rows.push(row(
      "LROC acquired",
      lrocTime.toISOString().slice(0, 10),
      `The reference pass. Its sun angle differs from Chandrayaan-2's, which is what ` +
      `makes appearance-based matching difficult.`,
    ));
  }

  if (p.lroc_total_candidates) {
    rows.push(row(
      "Reference choice",
      `${p.lroc_candidates_tried || 1} of ${p.lroc_total_candidates}`,
      `${p.lroc_total_candidates} LROC images cover this coordinate. Attempts are spread ` +
      `across different spacecraft passes, so a failure under one sun angle still gets ` +
      `tried under another.`,
    ));
  }

  if (!rows.length) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML = `
    <div class="ctx">
      <span class="eyebrow ctx-title">What this run was up against</span>
      ${rows.join("")}
    </div>`;

  if (result.overlap_map_url) {
    container.insertAdjacentHTML("beforeend", `
      <figure class="chart ctx-map">
        <figcaption class="eyebrow">Geographic footprint overlap</figcaption>
        <img src="${result.overlap_map_url}" alt="Map of the Chandrayaan-2 patch against the LROC image footprint" />
        <p class="chart-explain">
          Where the Chandrayaan-2 patch sits inside the LROC image's footprint on the
          lunar surface. The two have to overlap on the ground before any pixel
          comparison is meaningful.
        </p>
      </figure>`);
  }
}
