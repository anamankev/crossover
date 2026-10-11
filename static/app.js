// Crossover front end. No framework: it fetches JSON from our own API
// and builds the page with plain DOM calls.
//
// Text from the API is always inserted with textContent, never innerHTML,
// so a film title or an LLM answer can never inject markup.

const $ = (id) => document.getElementById(id);

const PLACEHOLDERS = {
  movie: ["A film you love", "Another film", "One more film"],
  artist: ["An artist you love", "Another artist", "One more artist"],
};

let config = null;
let runId = 0; // lets a newer request cancel the display of an older one

// ---- small helpers ------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function fmtScore(x) {
  return x === null || x === undefined ? "n/a" : x.toFixed(3);
}

// Popularity arrives as a percentile such as 0.9993. "Top 0.07%" reads better.
function fmtTopPercent(percentile) {
  const top = (1 - percentile) * 100;
  if (top < 0.01) return "top 0.01%";
  if (top < 1) return `top ${top.toFixed(2)}%`;
  if (top < 10) return `top ${top.toFixed(1)}%`;
  return `top ${Math.round(top)}%`;
}

function image(url, alt) {
  if (!url) return el("div", "ph");
  const img = document.createElement("img");
  img.src = url;
  img.alt = alt || "";
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";
  img.onerror = () => img.replaceWith(el("div", "ph"));
  return img;
}

async function postJSON(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Something went wrong. Please try again.";
    throw new Error(detail);
  }
  return data;
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = false;
}

// ---- tooltip for the dots ----------------------------------------------

function attachTooltip(node, text) {
  const tip = $("tooltip");
  const show = (x, y) => {
    tip.textContent = text;
    tip.hidden = false;
    tip.style.left = `${Math.min(x + 12, window.innerWidth - 270)}px`;
    tip.style.top = `${y + 14}px`;
  };
  node.addEventListener("mousemove", (e) => show(e.clientX, e.clientY));
  node.addEventListener("mouseleave", () => (tip.hidden = true));
  node.addEventListener("focus", () => {
    const box = node.getBoundingClientRect();
    show(box.left, box.bottom);
  });
  node.addEventListener("blur", () => (tip.hidden = true));
}

// ---- the form -----------------------------------------------------------

function currentDirection() {
  return document.querySelector('input[name="direction"]:checked').value;
}

function otherDomain(domain) {
  return domain === "movie" ? "artist" : "movie";
}

function updatePlaceholders() {
  PLACEHOLDERS[currentDirection()].forEach((text, i) => ($(`seed${i}`).placeholder = text));
}

function fillForm(example) {
  document.querySelector(`input[name="direction"][value="${example.input_domain}"]`).checked = true;
  updatePlaceholders();
  example.seeds.forEach((seed, i) => ($(`seed${i}`).value = seed));
}

function renderExamples() {
  for (const example of config.examples) {
    const chip = el("button", "chip", example.label);
    chip.type = "button";
    chip.addEventListener("click", () => {
      fillForm(example);
      run();
    });
    $("examples").append(chip);
  }
}

// ---- drawing the results ------------------------------------------------

function renderSeeds(seeds) {
  const box = $("seeds");
  box.replaceChildren(el("span", null, "Matched in Qloo:"));
  for (const seed of seeds) {
    const chip = el("span", "seed");
    chip.append(image(seed.image_url, ""), el("span", null, seed.label));
    if (!seed.exact) chip.append(el("span", "fuzzy", `closest match for "${seed.query}"`));
    box.append(chip);
  }
}

function fitRow(pick, side) {
  const row = el("div", "fit");
  const bar = el("div", "bar");
  const fill = el("i", side);
  fill.style.width = `${Math.max(0, Math.min(1, pick.affinity)) * 100}%`;
  bar.append(fill);
  bar.title = `Taste fit ${fmtScore(pick.affinity)} on Qloo's 0 to 1 scale`;
  row.append(bar, el("span", "value", `fit ${fmtScore(pick.affinity)}`));
  return row;
}

function pickTitle(name, year, shared) {
  const title = el("div", "name", name);
  if (year) title.append(" ", el("span", "year", `(${year})`));
  if (shared) title.append(el("span", "badge", "in both lists"));
  return title;
}

function renderQlooList(picks, sharedIds) {
  const list = $("qloo-list");
  list.replaceChildren();
  picks.forEach((pick, i) => {
    const item = el("li", "pick");
    const body = el("div");
    body.append(pickTitle(pick.name, pick.year, sharedIds.has(pick.entity_id)));
    if (pick.genres.length) body.append(el("div", "genres", pick.genres.slice(0, 4).join(" · ")));
    if (pick.drivers.length) {
      const parts = pick.drivers.map((d) => `${d.seed} ${Math.round(d.share * 100)}%`);
      body.append(el("div", "drivers", `Driven by ${parts.join(", ")}`));
    }
    if (pick.affinity !== null) body.append(fitRow(pick, "qloo"));
    item.append(el("span", "rank", String(i + 1)), image(pick.image_url, pick.name), body);
    list.append(item);
  });
}

function renderLlmList(picks, sharedIds) {
  const list = $("llm-list");
  list.replaceChildren();
  picks.forEach((pick, i) => {
    const item = el("li", pick.measured ? "pick" : "pick unmeasured");
    const body = el("div");
    body.append(pickTitle(pick.llm_name, pick.llm_year, pick.measured && sharedIds.has(pick.entity_id)));
    if (pick.reason) body.append(el("div", "reason", pick.reason));
    if (pick.measured) {
      body.append(fitRow(pick, "llm"));
    } else {
      body.append(el("div", "why-not", `Not measured: ${pick.why_not}.`));
    }
    item.append(el("span", "rank", String(i + 1)), image(pick.image_url, pick.llm_name), body);
    list.append(item);
  });
}

function renderLlmMessage(message, isLoading) {
  const list = $("llm-list");
  list.replaceChildren();
  if (isLoading) {
    for (let i = 0; i < 6; i++) list.append(el("li", "skeleton"));
  }
  list.prepend(el("li", "pending", message));
}

// One line of a metric: who, a bar on a fixed 0 to 1 scale, the number,
// and how many picks the number is based on.
function sideRow(side, label, metric, pendingText) {
  const row = el("div", "side");
  const who = el("span", "who");
  who.append(el("i", `swatch ${side}`), label);
  row.append(who);
  if (!metric) {
    const note = el("span", "pending", pendingText);
    note.style.gridColumn = "2 / -1";
    row.append(note);
    return row;
  }
  const bar = el("div", "bar");
  if (metric.value !== null) {
    const fill = el("i", side);
    fill.style.width = `${metric.value * 100}%`;
    bar.append(fill);
  }
  row.append(bar, el("span", "value", metric.value === null ? "n/a" : fmtScore(metric.value)));
  row.append(el("span", "cover", metric.value === null
    ? "not enough measured picks to compute this"
    : `based on ${metric.n} of ${metric.of} picks`));
  return row;
}

// Every pick as a dot on Qloo's taste-fit scale. Dots show position, not
// length, so the axis can zoom in on the range the picks actually cover.
// Both ends of the axis are labelled so the zoom is never hidden.
function stripPlot(qlooPicks, llmPicks) {
  const points = [
    ...qlooPicks.filter((p) => p.affinity !== null).map((p) => ({ ...p, side: "qloo", label: p.name })),
    ...(llmPicks || []).filter((p) => p.measured && p.affinity !== null).map((p) => ({ ...p, side: "llm", label: p.name })),
  ];
  if (!points.length) return null;

  const lowest = Math.min(...points.map((p) => p.affinity));
  const min = Math.max(0, Math.floor((lowest - 0.02) * 20) / 20); // round down to a 0.05 step
  const max = 1;
  const W = 640, H = 78, left = 8, right = 632;
  const x = (v) => left + ((v - min) / (max - min)) * (right - left);
  const rowY = { qloo: 20, llm: 44 };

  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Each pick placed on Qloo's taste-fit scale. Blue dots are Qloo's picks, orange dots are the LLM's.");

  const add = (tag, attrs, text) => {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text) node.textContent = text;
    svg.append(node);
    return node;
  };

  add("line", { class: "axis", x1: left, x2: right, y1: 58, y2: 58 });
  const ticks = [];
  for (let t = min; t <= max + 1e-9; t += 0.05) ticks.push(Math.round(t * 100) / 100);
  for (const t of ticks) {
    add("line", { class: "axis", x1: x(t), x2: x(t), y1: 58, y2: 62 });
    const anchor = t === ticks[0] ? "start" : t === ticks[ticks.length - 1] ? "end" : "middle";
    add("text", { x: x(t), y: 75, "text-anchor": anchor }, t.toFixed(2));
  }

  for (const point of points) {
    const dot = add("circle", { class: point.side, cx: x(point.affinity), cy: rowY[point.side], r: 6, tabindex: 0 });
    const who = point.side === "qloo" ? "Qloo pick" : "LLM pick";
    attachTooltip(dot, `${point.label}: fit ${fmtScore(point.affinity)} (${who})`);
  }

  const wrap = el("div", "strip");
  wrap.append(svg);
  return wrap;
}

function metricBlock(title, explanation) {
  const block = el("div", "metric");
  const head = el("div");
  head.append(el("h3", null, title), el("p", null, explanation));
  const sides = el("div", "sides");
  block.append(head, sides);
  return { block, sides };
}

function renderScoreboard(grounded, baseline, baselineStatus, nouns) {
  const board = $("scoreboard");
  board.replaceChildren();
  const q = grounded.metrics;
  const b = baseline ? baseline.metrics : null;
  const pending = baselineStatus;

  // 1. Taste fit
  const fit = metricBlock("Taste fit",
    "Average Qloo affinity between each pick and your three inputs. 0 is no fit, 1 is the strongest.");
  fit.sides.append(sideRow("qloo", "Qloo", q.affinity), sideRow("llm", "LLM", b && b.affinity, pending));
  const strip = stripPlot(grounded.picks, baseline && baseline.picks);
  if (strip) fit.sides.append(strip);
  let fitNote = "Qloo's list is ranked by this score, so it leads here by construction. The dots show every pick on the same scale. Hover one to see which it is.";
  if (b && b.affinity.value !== null && q.affinity.value !== null) {
    const gap = q.affinity.value - b.affinity.value;
    fitNote = `The LLM's measured picks average ${Math.abs(gap).toFixed(3)} ${gap >= 0 ? "below" : "above"} Qloo's. ` + fitNote;
  }
  fit.block.append(el("p", "caveat", fitNote));
  board.append(fit.block);

  // 2. How mainstream
  const pop = metricBlock("How mainstream",
    `Where the middle pick of each list ranks among everything in Qloo's ${nouns} catalog, by popularity.`);
  const popLine = (side, label, metric) => {
    const row = el("div", "side");
    const who = el("span", "who");
    who.append(el("i", `swatch ${side}`), label);
    const text = el("span", metric ? "stat" : "pending");
    if (!metric) text.textContent = pending;
    else if (metric.value === null) text.textContent = "not enough measured picks";
    else {
      text.append("Middle pick is in the ", el("b", null, fmtTopPercent(metric.value)),
        ` most popular (percentile ${metric.value.toFixed(4)}, ${metric.n} of ${metric.of} picks)`);
    }
    text.style.gridColumn = "2 / -1";
    row.append(who, text);
    return row;
  };
  pop.sides.append(popLine("qloo", "Qloo", q.popularity), popLine("llm", "LLM", b && b.popularity));
  pop.block.append(el("p", "caveat",
    "A smaller top-percent means more mainstream. Neither direction is better by itself: this shows whether a list leans on the obvious."));
  board.append(pop.block);

  // 3. Variety
  const variety = metricBlock("Variety",
    "How different the picks are from each other, from Qloo's genre tags. 0 means all the same genres, 1 means no two picks share a genre.");
  variety.sides.append(sideRow("qloo", "Qloo", q.variety), sideRow("llm", "LLM", b && b.variety, pending));
  board.append(variety.block);

  // 4. Agreement and coverage
  const agree = metricBlock("Agreement",
    "How much the two lists have in common, and how much of the LLM's list could be checked at all.");
  const line = el("div", baseline ? "stat" : "pending");
  line.style.gridColumn = "1 / -1";
  if (!baseline) {
    line.textContent = pending;
  } else {
    line.append(el("b", null, `${baseline.overlap.count} of ${grounded.picks.length}`),
      " of Qloo's picks were also named by the LLM. ",
      el("b", null, `${baseline.measured.count} of ${baseline.measured.of}`),
      " LLM picks were found in Qloo's catalog and scored. The rest are marked \"not measured\" below and left out of every number above.");
  }
  agree.sides.append(line);
  board.append(agree.block);
}

function renderTrace(trace) {
  const list = el("ol", "trace");
  for (const step of trace) {
    const item = el("li");
    const text = el("span");
    text.append(el("strong", null, step.step), `: ${step.detail}`);
    item.append(text, el("code", null, step.ms ? `${step.call} · ${step.ms} ms` : step.call));
    list.append(item);
  }
  const intro = el("p", "small muted", "What the Qloo-grounded agent did for this request:");
  $("trace").replaceChildren(intro, list);
}

// ---- running a comparison -----------------------------------------------

async function run() {
  const seeds = [0, 1, 2].map((i) => $(`seed${i}`).value.trim()).filter(Boolean);
  $("error").hidden = true;
  if (!seeds.length) {
    showError("Enter at least one thing you love, or pick an example.");
    return;
  }
  const input_domain = currentDirection();
  const body = { input_domain, output_domain: otherDomain(input_domain), seeds };
  const nouns = config.domains[body.output_domain].noun;
  const mine = ++runId;
  // Put the inputs in the address bar so this exact comparison can be shared as a link.
  const query = new URLSearchParams({ from: input_domain });
  seeds.forEach((seed) => query.append("q", seed));
  history.replaceState(null, "", `?${query}`);
  $("go").disabled = true;
  $("go").textContent = "Working…";

  try {
    // Qloo side first: it is fast, so the page fills in right away.
    const grounded = await postJSON("/api/grounded", body);
    if (mine !== runId) return;
    $("results").hidden = false;
    renderSeeds(grounded.seeds);
    renderQlooList(grounded.picks, new Set());
    renderTrace(grounded.trace);
    $("prompt").textContent = "Waiting for the LLM…";
    $("llm-model").textContent = config.llm_configured ? config.llm_model : "";

    if (!config.llm_configured) {
      const message = "The LLM baseline is not connected on this server yet, so there is nothing to compare against. No numbers are shown for it rather than made-up ones.";
      renderScoreboard(grounded, null, "LLM baseline not connected", nouns);
      renderLlmMessage(message, false);
      $("prompt").textContent = "No prompt was sent.";
      return;
    }

    renderScoreboard(grounded, null, "waiting for the LLM…", nouns);
    renderLlmMessage("The LLM is answering from memory, then Qloo scores its picks…", true);
    $("results").scrollIntoView({ behavior: "smooth", block: "start" });

    try {
      const baseline = await postJSON("/api/baseline", body);
      if (mine !== runId) return;
      const shared = new Set(baseline.overlap.ids);
      renderQlooList(grounded.picks, shared);
      renderLlmList(baseline.picks, shared);
      renderScoreboard(grounded, baseline, "", nouns);
      $("llm-model").textContent = baseline.model;
      $("prompt").textContent = baseline.prompt;
    } catch (err) {
      if (mine !== runId) return;
      renderScoreboard(grounded, null, "LLM baseline unavailable", nouns);
      renderLlmMessage(`The LLM baseline failed: ${err.message}`, false);
      $("prompt").textContent = "No answer was received.";
    }
  } catch (err) {
    if (mine !== runId) return;
    $("results").hidden = true;
    showError(err.message);
  } finally {
    if (mine === runId) {
      $("go").disabled = false;
      $("go").textContent = "Cross over";
    }
  }
}

// ---- start up -----------------------------------------------------------

async function start() {
  updatePlaceholders();
  document.querySelectorAll('input[name="direction"]').forEach((radio) =>
    radio.addEventListener("change", updatePlaceholders));
  $("form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (config) run();
  });

  // If the server is asleep this first call is slow. Say so after a moment.
  const wakingTimer = setTimeout(() => ($("waking").hidden = false), 1500);
  for (let attempt = 0; attempt < 6 && !config; attempt++) {
    try {
      const response = await fetch("/api/config");
      if (response.ok) config = await response.json();
    } catch (err) {
      // server still waking up
    }
    if (!config) await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  clearTimeout(wakingTimer);
  $("waking").hidden = true;
  if (!config) {
    showError("The server did not respond. Please reload the page in a minute.");
    return;
  }
  if (config.repo_url) {
    const link = el("a", null, "Source on GitHub.");
    link.href = config.repo_url;
    link.rel = "noopener";
    $("repo-link").append(link);
  }
  renderExamples();

  // A shared link such as ?from=movie&q=Dune&q=Alien runs straight away.
  const params = new URLSearchParams(location.search);
  const shared = params.getAll("q").slice(0, 3);
  if (shared.length && config.domains[params.get("from")]) {
    fillForm({ input_domain: params.get("from"), seeds: shared });
    run();
  }
}

start();
