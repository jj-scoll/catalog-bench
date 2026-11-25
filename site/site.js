"use strict";
// Renders site/data.json (built by `python -m bench.site`). No dependencies.
// Names, descriptions and error messages come from result files, so they only ever reach the page as text nodes.

const KNOWN = ["postgres", "ducklake-postgres", "iceberg-sql-postgres", "s3-tables", "lakekeeper", "polaris"];
const SVG = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) el.setAttribute(k, v);
  el.append(...children.flat().filter((c) => c != null && c !== false));
  return el;
}
function s(tag, attrs, text) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  if (text != null) el.textContent = text;
  return el;
}

const isNum = (v) => typeof v === "number" && isFinite(v);
const int = (v) => Math.round(v).toLocaleString("en-US");
const rate = (v) => !isNum(v) ? "not recorded" : v >= 100 ? int(v) : v.toLocaleString("en-US", { maximumFractionDigits: v >= 10 ? 1 : 2 });
const ms = (v) => !isNum(v) ? "not recorded" : v >= 1000 ? (v / 1000).toFixed(2) + " s" : v >= 100 ? Math.round(v) + " ms" : v >= 1 ? v.toFixed(1) + " ms" : v.toFixed(2) + " ms";
const pct = (v) => !isNum(v) ? "not reported" : v === 0 ? "0%" : v < 0.001 ? "under 0.1%" : (v * 100).toFixed(v < 0.1 ? 1 : 0) + "%";
const tick = (v) => v >= 1e6 ? v / 1e6 + "M" : v >= 1e4 ? v / 1e3 + "K" : v.toLocaleString("en-US");
const plural = (n, word) => `${int(n)} ${word}${n === 1 ? "" : "s"}`;

let data, colors = {}, current;

function color(name) { return colors[name] || "var(--s-other)"; }
function key(name) { return h("span", { class: "key", style: `--c:${color(name)}` }); }

// Colour follows the system, never its rank: known systems own a fixed slot, others take what is left in name order.
function assignColors() {
  const names = [...new Set(data.environments.flatMap((e) => e.systems.map((x) => x.name)))];
  const order = [...KNOWN, ...names.filter((n) => !KNOWN.includes(n)).sort()];
  order.forEach((n, i) => { if (i < 8) colors[n] = `var(--s${i + 1})`; });
}

function envTitle(e) {
  const length = isNum(e.target_seconds) ? `${e.target_seconds}-second runs` : "run length not recorded";
  return `${e.label} (${length}, ${plural(e.reps, "repetition")})${e.comparable ? "" : " (not comparable)"}`;
}

/* ---------- scaling chart ---------- */

function drawChart(env) {
  const host = $("chart");
  const series = env.systems
    .map((x) => ({ name: x.name, pts: x.points.filter((p) => p.commits_per_s > 0) }))
    .filter((x) => x.pts.length);
  host.replaceChildren();
  if (!series.length) return;

  const W = Math.max(300, host.clientWidth), wide = W >= 640;
  const H = Math.round(Math.max(270, Math.min(430, W * 0.52)));
  const m = { l: 48, r: wide ? 158 : 18, t: 26, b: 40 };
  const xs = [...new Set(series.flatMap((x) => x.pts.map((p) => p.writers)))].sort((a, b) => a - b);
  const x0 = m.l + 14, x1 = W - m.r - 14;
  const x = (w) => xs.length === 1 ? (x0 + x1) / 2 : x0 + (Math.log2(w / xs[0]) / Math.log2(xs.at(-1) / xs[0])) * (x1 - x0);
  const all = series.flatMap((x) => x.pts.flatMap((p) => [p.min, p.max, p.commits_per_s])).filter((v) => v > 0);
  let d0 = Math.floor(Math.log10(Math.min(...all))), d1 = Math.ceil(Math.log10(Math.max(...all)));
  if (d0 === d1) d1 += 1;
  const y = (v) => H - m.b - ((Math.log10(v) - d0) / (d1 - d0)) * (H - m.b - m.t);

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, tabindex: 0, role: "img",
    "aria-label": "Line chart of commits per second against concurrent writers, one line per system, logarithmic vertical axis. Use the left and right arrow keys to read each writer count. The same numbers are in the table below." });
  for (let d = d0; d <= d1; d++) {
    for (const mult of d1 - d0 <= 2 && d < d1 ? [1, 2, 5] : [1]) {
      const v = mult * 10 ** d;
      svg.append(s("line", { class: d === d0 && mult === 1 ? "axis" : "grid", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
      svg.append(s("text", { x: m.l - 8, y: y(v), "text-anchor": "end", "dominant-baseline": "middle" }, tick(v)));
    }
  }
  svg.append(s("text", { x: 8, y: 12 }, "commits per second, log scale"));
  for (const w of xs) svg.append(s("text", { x: x(w), y: H - m.b + 16, "text-anchor": "middle" }, w));
  svg.append(s("text", { x: (x0 + x1) / 2, y: H - 6, "text-anchor": "middle" }, "concurrent writers"));

  const cross = s("line", { class: "cross", y1: m.t, y2: H - m.b, visibility: "hidden" });
  svg.append(cross);

  for (const sr of series) {
    const c = color(sr.name);
    if (sr.pts.some((p) => p.reps > 1)) {
      const up = sr.pts.map((p) => `${x(p.writers)},${y(p.max)}`), down = sr.pts.map((p) => `${x(p.writers)},${y(p.min)}`).reverse();
      svg.append(s("polygon", { points: [...up, ...down].join(" "), fill: c, "fill-opacity": 0.12 }));
      for (const p of sr.pts) if (p.reps > 1) svg.append(s("line", { x1: x(p.writers), x2: x(p.writers), y1: y(p.max), y2: y(p.min), stroke: c, "stroke-width": 2, "stroke-opacity": 0.5, "stroke-linecap": "round" }));
    }
    svg.append(s("polyline", { points: sr.pts.map((p) => `${x(p.writers)},${y(p.commits_per_s)}`).join(" "), fill: "none", stroke: c, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  }
  for (const sr of series) for (const p of sr.pts) svg.append(s("circle", { cx: x(p.writers), cy: y(p.commits_per_s), r: 4, fill: color(sr.name), stroke: "var(--surface)", "stroke-width": 2 }));

  if (wide) { // end labels only where they do not collide; the legend always names every line
    let last = -Infinity;
    for (const sr of series.map((sr) => ({ sr, p: sr.pts.at(-1) })).sort((a, b) => y(a.p.commits_per_s) - y(b.p.commits_per_s))) {
      const ly = y(sr.p.commits_per_s);
      if (ly - last < 15 || sr.sr.name.length > 22) continue;
      svg.append(s("text", { class: "endlabel", x: x(sr.p.writers) + 10, y: ly, "dominant-baseline": "middle" }, sr.sr.name));
      last = ly;
    }
  }

  const tip = h("div", { class: "tip", hidden: "" });
  let at = -1;
  function show(i) {
    at = i;
    if (i < 0) { tip.hidden = true; cross.setAttribute("visibility", "hidden"); return; }
    const w = xs[i], px = x(w);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    const rows = series.map((sr) => ({ name: sr.name, p: sr.pts.find((p) => p.writers === w) })).filter((r) => r.p).sort((a, b) => b.p.commits_per_s - a.p.commits_per_s);
    tip.replaceChildren(h("div", { class: "head" }, plural(w, "writer")), ...rows.map((r) => h("span", { class: "row" }, key(r.name), h("b", {}, rate(r.p.commits_per_s)), " ", h("span", { class: "name" }, r.name),
      r.p.reps > 1 ? h("span", { class: "spread" }, ` (${rate(r.p.min)} to ${rate(r.p.max)})`) : null)));
    tip.hidden = false;
    const scale = svg.getBoundingClientRect().width / W, left = px * scale;
    if (left > host.clientWidth / 2) { tip.style.left = ""; tip.style.right = host.clientWidth - left + 12 + "px"; } else { tip.style.right = ""; tip.style.left = left + 12 + "px"; }
  }
  svg.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * W;
    show(xs.reduce((best, w, i) => Math.abs(x(w) - px) < Math.abs(x(xs[best]) - px) ? i : best, 0));
  });
  svg.addEventListener("pointerleave", () => show(-1));
  svg.addEventListener("focus", () => show(Math.max(at, 0)));
  svg.addEventListener("blur", () => show(-1));
  svg.addEventListener("keydown", (e) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
    if (step) { e.preventDefault(); show(Math.min(xs.length - 1, Math.max(0, at + step))); }
  });
  host.append(svg, tip);
}

function renderScaling(env) {
  const repeated = env.systems.some((x) => x.points.some((p) => p.reps > 1));
  $("scaling-sub").textContent = "One line per system; the vertical axis is logarithmic, so equal steps are equal ratios. "
    + (repeated ? "Lines join the medians across repetitions; the shaded band and vertical bars span the lowest and highest repetition."
      : "Each point is a single run, so there is no repetition spread to show.");
  $("legend").replaceChildren(...env.systems.map((x) => h("span", {}, key(x.name), x.name)));
  drawChart(env);

  $("scaling-readout").replaceChildren(...env.systems.filter((x) => x.points.length > 1).map((x) => {
    const a = x.points[0], b = x.points.at(-1);
    return h("li", {}, key(x.name), x.name, ": ", h("b", {}, rate(a.commits_per_s)), ` with ${plural(a.writers, "writer")}, `, h("b", {}, rate(b.commits_per_s)), ` with ${int(b.writers)}`,
      a.commits_per_s > 0 ? ` (${(b.commits_per_s / a.commits_per_s).toFixed(1)} times)` : "");
  }));

  const writers = [...new Set(env.systems.flatMap((x) => x.points.map((p) => p.writers)))].sort((a, b) => a - b);
  $("scaling-table").replaceChildren(h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Commits per second"), ...writers.map((w) => h("th", { class: "n" }, plural(w, "writer"))))),
    h("tbody", {}, ...env.systems.map((x) => h("tr", {}, h("td", { class: "sysname" }, key(x.name), x.name), ...writers.map((w) => {
      const p = x.points.find((q) => q.writers === w);
      return h("td", { class: "n" }, p ? rate(p.commits_per_s) : "", p && p.reps > 1 ? h("small", {}, `${rate(p.min)} to ${rate(p.max)}`) : null);
    }))))));
}

/* ---------- leaderboard ---------- */

function verifiedMark(v) {
  return v === true ? h("span", { class: "mark yes" }, "Verified") : v === false ? h("span", { class: "mark no" }, "Rows did not match") : h("span", { class: "mark" }, "Not recorded");
}

function renderBoard(env) {
  const cols = ["System", "Peak commits per second", "Writers at peak", "Median commit time", "Catalog call share", "Failed commits", "Verified"];
  const cell = (i, cls, ...kids) => h("td", { class: cls, "data-label": cols[i] }, h("span", {}, ...kids));
  $("board").replaceChildren(h("table", {},
    h("thead", {}, h("tr", {}, ...cols.map((c, i) => h("th", { class: i && i < 6 ? "n" : null }, c)))),
    h("tbody", {}, ...env.systems.map((x) => {
      const p = x.peak;
      return h("tr", {},
        cell(0, "sysname", key(x.name), x.name, x.system === "postgres" ? h("span", { class: "tag" }, "baseline, not a catalog") : null),
        cell(1, "n", h("span", { class: "big" }, rate(p.commits_per_s)), h("small", {}, p.reps > 1 ? `${rate(p.min)} to ${rate(p.max)} over ${int(p.reps)} runs` : "single run, no range")),
        cell(2, "n", int(p.writers)),
        cell(3, "n", ms(p.p50_ms)),
        cell(4, "n", pct(p.catalog_share), isNum(p.catalog_share) ? h("span", { class: "meter" }, h("i", { style: `width:${Math.min(100, p.catalog_share * 100)}%` })) : null),
        cell(5, "n", isNum(p.error_rate) ? pct(p.error_rate) : "not recorded"),
        cell(6, null, verifiedMark(x.verified)));
    }))));
}

/* ---------- fairness ---------- */

function renderFairness(env) {
  let worst = null;
  $("fair").replaceChildren(...env.systems.map((x) => h("div", {},
    h("h4", {}, key(x.name), x.name),
    h("table", {},
      h("thead", {}, h("tr", {}, h("th", { class: "n" }, "Writers"), h("th", {}, "Commits per writer, fewest to most"), h("th", { class: "n" }, "Median commit"), h("th", { class: "n" }, "Slowest commit"))),
      h("tbody", {}, ...x.points.map((p) => {
        const has = isNum(p.writer_min) && isNum(p.writer_max);
        const gap = has && p.writers > 1 ? (p.writer_max - p.writer_min) / (p.writer_max || 1) : -1;
        if (gap > 0 && (!worst || gap > worst.gap || (gap === worst.gap && p.writer_max > worst.p.writer_max))) worst = { gap, p, name: x.name };
        return h("tr", {},
          h("td", { class: "n" }, int(p.writers)),
          has ? h("td", {}, p.writer_min === p.writer_max ? int(p.writer_min) : `${int(p.writer_min)} to ${int(p.writer_max)}`,
            h("span", { class: "range", style: `--c:${color(x.name)}` }, h("i", { style: `left:${p.writer_max ? (p.writer_min / p.writer_max) * 100 : 100}%` })))
            : h("td", { class: "none" }, "not recorded"),
          h("td", { class: "n" }, ms(p.p50_ms)),
          h("td", { class: "n" }, ms(p.max_ms)));
      }))))));
  const callout = $("fairness-callout");
  callout.hidden = !worst;
  if (worst) {
    const p = worst.p;
    callout.textContent = `Widest gap here: ${worst.name} with ${int(p.writers)} writers. One writer got ${plural(p.writer_min, "commit")} while another got ${int(p.writer_max)}`
      + (isNum(p.max_ms) && isNum(p.p50_ms) ? `; the slowest commit took ${ms(p.max_ms)} against a median of ${ms(p.p50_ms)}.` : ".")
      + (p.reps > 1 ? ` (Fewest and most are taken across ${int(p.reps)} repetitions.)` : "");
  }
}

/* ---------- per-system detail ---------- */

function renderDetail(env) {
  $("detail").replaceChildren(...env.systems.map((x) => h("details", {},
    h("summary", {}, key(x.name), x.name),
    h("dl", {},
      h("dt", {}, "What is measured"), h("dd", {}, x.description || "No description in the result files."),
      h("dt", {}, "Versions"), h("dd", {}, x.version || "Not recorded."),
      h("dt", {}, "Client"), h("dd", {}, x.client != null ? String(x.client) : "No separate client label in the result files; see the description and versions."),
      h("dt", {}, "Error samples"), h("dd", {}, x.error_samples.length
        ? h("ul", {}, ...x.error_samples.map((e) => h("li", {}, `${int(e.count)} × `, h("code", {}, e.message))))
        : "No failed commits recorded.")))));
}

/* ---------- pipeline workload ---------- */

// Milliseconds under one second, otherwise seconds with one decimal (and minutes alongside once that stops being readable).
function dur(v) {
  if (!isNum(v)) return "not recorded";
  if (v < 1) return "under 1 ms";
  if (Math.round(v) < 1000) return Math.round(v) + " ms";
  const sec = v / 1000, text = sec.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " s";
  return sec >= 120 ? `${text} (${Math.round(sec / 60)} min)` : text;
}
const flag = (text) => h("strong", { class: "flag" }, text);
const small = (...kids) => h("small", {}, ...kids);

function shapeLoad(shape) {
  const parts = [];
  if (isNum(shape.traces) && isNum(shape.batches_per_trace)) {
    parts.push(`${plural(shape.traces, "trace")} of ${int(shape.batches_per_trace)} batches` + (shape.traces > 1 ? ` (${int(shape.traces * shape.batches_per_trace)} batches in all)` : ""));
  }
  parts.push(isNum(shape.target_batches_per_s) ? `paced at ${rate(shape.target_batches_per_s)} batches per second` : "unpaced, so workers take batches as fast as the option accepts them");
  for (const [k, word] of [["rows_per_batch", "rows per batch"], ["workers", "workers"]]) {
    const said = [...new Set(shape.runs.map((r) => r[k]).filter(isNum))];
    if (said.length === 1) parts.push(`${int(said[0])} ${word}`);
  }
  return parts.join(", ") + ".";
}

function keptUp(r) {
  const achieved = isNum(r.batches_per_s) ? rate(r.batches_per_s) : null;
  if (!isNum(r.target_batches_per_s)) return [achieved ? h("span", { class: "big" }, achieved + " batches/s") : "not recorded", small("unpaced, no schedule to keep")];
  return [
    r.kept_up === true ? h("span", { class: "mark yes" }, "Kept up") : r.kept_up === false ? flag("Fell behind") : h("span", { class: "mark" }, "Not recorded"),
    small(`${achieved || "?"} of ${rate(r.target_batches_per_s)} batches/s asked`),
    isNum(r.behind_max_ms) ? small(r.behind_max_ms < 1 ? "on schedule throughout" : `up to ${dur(r.behind_max_ms)} late`) : null,
  ];
}

function verifiedCell(r) {
  if (r.all_verified === true) return [h("span", { class: "mark yes" }, "Verified")];
  if (r.all_verified !== false) return [h("span", { class: "mark" }, "Not recorded")];
  const why = [["rows_missing", "missing"], ["rows_duplicated", "duplicated"], ["rows_stale", "stale"]].filter(([k]) => r[k] > 0).map(([k, word]) => `${int(r[k])} rows ${word}`);
  if (r.verified === false && !why.length) why.push("rows did not match");
  if (r.reprocess_verified === false) why.push("reprocessed trace did not match");
  if (r.verified_after_maintain === false) why.push("did not match after compaction");
  return [flag("Not verified"), small(why.join("; "))];
}

function pipelineRow(r, cols) {
  const cell = (i, cls, ...kids) => h("td", { class: cls, "data-label": cols[i] }, ...kids.flat());
  const name = h("th", { scope: "row", class: "sysname" }, r.option, small(r.backing_label || r.backing || ""), r.note ? small(r.note) : null);
  if (r.failed != null) return h("tr", {}, name, h("td", { colspan: cols.length - 1 }, flag("Run failed"), small(r.failed)));
  const lost = r.batches_failed > 0;
  return h("tr", { class: r.kept_up === false || r.all_verified === false || lost ? "trouble" : null }, name,
    cell(1, null, keptUp(r)),
    cell(2, "n", dur(r.commit_p50_ms), small("p95 " + dur(r.commit_p95_ms))),
    cell(3, "n", dur(r.finalize_max_ms), r.finalize_retries > 0 ? small(plural(r.finalize_retries, "retry").replace("retrys", "retries")) : null),
    cell(4, "n", dur(r.read_trace_p50_ms), isNum(r.read_trace_after_maintain_p50_ms) ? small("after compaction " + dur(r.read_trace_after_maintain_p50_ms)) : null),
    cell(5, "n", !isNum(r.write_errors) ? "not recorded" : r.write_errors === 0 ? "none" : int(r.write_errors),
      r.write_errors > 0 && !lost ? small("all delivered again") : null,
      lost ? [flag(`${plural(r.batches_failed, "batch").replace("batchs", "batches")} never written`)] : null),
    cell(6, null, verifiedCell(r)));
}

function renderPipeline(p) {
  const status = $("pipeline-status");
  const shapes = (p && p.shapes) || [];
  if (!shapes.length) {
    status.textContent = 'No pipeline results yet. Run "python -m bench.pipeline" so it writes a file into results/pipeline/, then run "python -m bench.site".';
    return;
  }
  status.remove();
  if (isNum(p.behind_limit_ms) && p.behind_limit_ms !== 1000) $("behind-limit").textContent = dur(p.behind_limit_ms);
  const cols = ["Option and backing", "Kept up", "Batch write, median and p95", "Close a trace, slowest", "Read one trace, median", "Failed writes", "Verified"];
  $("pipeline-shapes").replaceChildren(...shapes.map((shape) => {
    const id = "shape-" + shape.id.replace(/[^a-z0-9]+/gi, "-");
    const trouble = shape.runs.filter((r) => r.failed != null || r.kept_up === false || r.all_verified === false || r.batches_failed > 0).length;
    return h("section", { class: "shape" },
      h("h3", { id }, shape.name),
      h("p", { class: "sub" }, shapeLoad(shape) + " " + (trouble ? `${plural(trouble, "run")} of ${int(shape.runs.length)} had a problem, marked in the row.` : `${shape.runs.length === 1 ? "The run" : "All " + int(shape.runs.length) + " runs"} ${isNum(shape.target_batches_per_s) ? "kept up and verified" : "verified"}.`)),
      h("div", { class: "scroll", tabindex: 0, role: "region", "aria-labelledby": id },
        h("table", { class: "pipeline" },
          h("caption", {}, `${shape.name}: one row per option and backing. Each row is a single run.`),
          h("thead", {}, h("tr", {}, ...cols.map((c, i) => h("th", { scope: "col", class: i >= 2 && i <= 5 ? "n" : null }, c)))),
          h("tbody", {}, ...shape.runs.map((r) => pipelineRow(r, cols))))));
  }));

  $("pipeline-columns").hidden = false;
  const options = p.options || [];
  $("pipeline-options").hidden = !options.length;
  $("pipeline-detail").replaceChildren(...options.map((o) => h("details", {},
    h("summary", {}, o.name),
    h("dl", {},
      h("dt", {}, "What it does"), h("dd", {}, o.description ? o.description.replace(/\s*\n\s*/g, " ") : "No description in the result files."),
      h("dt", {}, "Versions"), h("dd", {}, o.versions && o.versions.length ? h("ul", {}, ...o.versions.map((v) => h("li", {}, v))) : "Not recorded.")))));

  const files = shapes.flatMap((s) => s.runs.map((r) => r.file)), when = (p.ran_at || []).map((t) => t.replace("T", " ").replace(/:\d\d\+00:00$/, ""));
  $("pipeline-sources").replaceChildren(
    h("p", {}, `${plural(new Set(files).size, "result file")} in results/pipeline/` + (p.environments && p.environments.length ? `, run on ${p.environments.join("; ")}` : "")
      + (when.length ? `, started between ${when[0]} and ${when.at(-1)} UTC.` : ".")),
    ...(p.machines || []).map((mc) => h("p", {}, "Machine: " + Object.entries(mc).map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ") + ".")),
    ...(p.skipped && p.skipped.length ? [h("p", {}, "Skipped: " + p.skipped.map((f) => `${f.file} (${f.reason})`).join("; ") + ".")] : []));
}

/* ---------- page ---------- */

function render() {
  const env = current;
  const notice = $("notice");
  notice.hidden = env.comparable;
  notice.replaceChildren(h("strong", {}, "Not comparable. "), `This is a smoke run: ${env.caveats.join("; ")}. Read it as a check that things work, not as a ranking.`);
  renderScaling(env); renderBoard(env); renderFairness(env); renderDetail(env);

  const ceiling = $("ceiling");
  ceiling.hidden = !env.harness_ceiling.length;
  ceiling.textContent = "Harness ceiling, the rate the load generator reaches against a system that does nothing: at least "
    + `${int(Math.min(...env.harness_ceiling.map((c) => c.commits_per_s)))} commits per second at every writer count.`;
  $("sources").replaceChildren(
    h("p", {}, `Result files in results/: ${env.files.join(", ")}.` + (env.ran_at.length ? ` Run started (UTC): ${env.ran_at.join(", ")}.` : "")),
    ...env.machines.map((mc) => h("p", {}, "Machine: " + Object.entries(mc).map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ") + ".")),
    ...(data.skipped.length ? [h("p", {}, "Skipped: " + data.skipped.map((f) => `${f.file} (${f.reason})`).join("; ") + ".")] : []));
}

function choose(id, push) {
  current = data.environments.find((e) => e.id === id) || current;
  $("env").value = current.id;
  if (push) history.replaceState(null, "", "#env=" + encodeURIComponent(current.id));
  render();
}

async function start() {
  const theme = new URLSearchParams(location.search).get("theme");
  if (theme === "dark" || theme === "light") document.documentElement.dataset.theme = theme;
  try {
    const res = await fetch("data.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`data.json returned ${res.status}`);
    data = await res.json();
  } catch (e) {
    $("status").textContent = `Could not load data.json (${e.message}). Build it with "python -m bench.site" and open this page through a static web server rather than as a file.`;
    $("pipeline-status").textContent = "Could not load data.json.";
    return;
  }
  renderPipeline(data.pipeline);
  if (!data.environments || !data.environments.length) {
    $("status").textContent = 'No results yet. Run the benchmark so it writes a file into results/, then run "python -m bench.site".';
    return;
  }
  $("status").remove();
  assignColors();
  // comparable environments first, then the longest runs
  const order = [...data.environments].sort((a, b) => b.comparable - a.comparable || (b.target_seconds || 0) - (a.target_seconds || 0));
  $("env").replaceChildren(...order.map((e) => h("option", { value: e.id }, envTitle(e))));
  $("env").addEventListener("change", (e) => choose(e.target.value, true));
  for (const id of ["scaling", "leaderboard", "fairness", "systems"]) $(id).hidden = false;
  if (data.repository) $("repo").href = "https://github.com/" + data.repository;
  current = order[0];
  choose(decodeURIComponent((location.hash.match(/^#env=(.*)$/) || [])[1] || ""), false);

  let width = $("chart").clientWidth;
  new ResizeObserver(() => { const w = $("chart").clientWidth; if (w !== width) { width = w; drawChart(current); } }).observe($("chart"));
}

start();
