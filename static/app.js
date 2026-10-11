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
let current = null; // the comparison on screen

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

// Browser storage can be switched off. The page must still work without it.
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch (err) { /* private mode */ }
}
function recall(key) {
  try { return localStorage.getItem(key); } catch (err) { return null; }
}

// ---- tooltip for the dots ----------------------------------------------

function attachTooltip(node, text) {
  const tip = $("tooltip");
  const show = (x, y) => {
    tip.textContent = text;
    tip.hidden = false;
    tip.style.left = `${Math.max(8, Math.min(x + 12, window.innerWidth - 270))}px`;
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
  [0, 1, 2].forEach((i) => ($(`seed${i}`).value = example.seeds[i] || ""));
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

// ---- matched inputs -----------------------------------------------------

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

// ---- step 1: the blind choice -------------------------------------------

function blindItem(name, year, imageUrl) {
  const item = el("li", "pick");
  const title = el("div", "name", name);
  if (year) title.append(" ", el("span", "year", `(${year})`));
  item.append(image(imageUrl, name), title);
  return item;
}

function renderBlind(data) {
  // Which side is "A" is decided by a coin flip for each comparison.
  const agentIsA = Math.random() < 0.5;
  current.sideOf = { a: agentIsA ? "agent" : "llm", b: agentIsA ? "llm" : "agent" };
  const lists = {
    agent: data.agent.picks.map((p) => blindItem(p.name, p.year, p.image_url)),
    llm: data.baseline.picks.map((p) => blindItem(p.llm_name, p.llm_year, p.image_url)),
  };
  $("blind-a").replaceChildren(...lists[current.sideOf.a]);
  $("blind-b").replaceChildren(...lists[current.sideOf.b]);
  $("blind").hidden = false;
  $("revealed").hidden = true;
}

async function choose(pick) {
  const choice = pick === "same" ? "same" : current.sideOf[pick];
  let tally = null;
  try {
    tally = await postJSON("/api/vote", { comparison_id: current.data.comparison_id, choice });
  } catch (err) {
    // The vote did not get through. Show the result anyway.
  }
  remember(`voted:${current.data.comparison_id}`, choice);
  reveal(choice, tally);
}

function renderVerdict(choice, tally) {
  const box = $("verdict");
  box.replaceChildren();
  if (!choice) {
    box.hidden = true;
    return;
  }
  const said = {
    agent: "You picked the list from the agent with Qloo.",
    llm: "You picked the list from the same model without Qloo.",
    same: "You couldn't choose between them.",
  }[choice];
  box.append(el("strong", null, said));
  if (tally) {
    const total = tally.agent + tally.llm + tally.same;
    box.append(` Blind votes on this server so far: ${tally.agent} for the agent with Qloo, ` +
      `${tally.llm} for the plain LLM, ${tally.same} couldn't choose (${total} in total).`);
  }
  box.hidden = false;
}

// ---- step 2: the reveal -------------------------------------------------

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

function pickTitle(name, year, badges) {
  const title = el("div", "name", name);
  if (year) title.append(" ", el("span", "year", `(${year})`));
  for (const badge of badges) title.append(el("span", "badge", badge));
  return title;
}

function renderAgentList(agent, sharedIds) {
  const list = $("qloo-list");
  list.replaceChildren();
  agent.picks.forEach((pick, i) => {
    const item = el("li", "pick");
    const body = el("div");
    const badges = [];
    if (pick.source === "own") badges.push("its own idea, verified");
    if (sharedIds.has(pick.entity_id)) badges.push("in both lists");
    body.append(pickTitle(pick.name, pick.year, badges));
    if (pick.reason) body.append(el("div", "reason", pick.reason));
    if (pick.genres.length) body.append(el("div", "genres", pick.genres.slice(0, 4).join(" · ")));
    if (pick.affinity !== null) body.append(fitRow(pick, "qloo"));
    item.append(el("span", "rank", String(i + 1)), image(pick.image_url, pick.name), body);
    list.append(item);
  });
  $("agent-model").textContent = agent.model || "no LLM";
  $("agent-note").textContent = agent.note || "";
  $("agent-note").hidden = !agent.note;
}

function renderLlmList(data, sharedIds) {
  const list = $("llm-list");
  list.replaceChildren();
  if (!data.baseline) {
    list.append(el("li", "pending",
      `No plain-LLM answer: ${data.baseline_error}. Nothing is shown for it rather than made-up results.`));
    $("llm-model").textContent = "";
    return;
  }
  data.baseline.picks.forEach((pick, i) => {
    const item = el("li", pick.measured ? "pick" : "pick unmeasured");
    const body = el("div");
    const badges = pick.measured && sharedIds.has(pick.entity_id) ? ["in both lists"] : [];
    body.append(pickTitle(pick.llm_name, pick.llm_year, badges));
    if (pick.reason) body.append(el("div", "reason", pick.reason));
    if (pick.measured) body.append(fitRow(pick, "llm"));
    else body.append(el("div", "why-not", `Not measured: ${pick.why_not}.`));
    item.append(el("span", "rank", String(i + 1)), image(pick.image_url, pick.llm_name), body);
    list.append(item);
  });
  $("llm-model").textContent = data.baseline.model;
}

// One line of a metric: who, a bar on a fixed 0 to 1 scale, the number,
// and how many picks the number is based on.
function sideRow(side, label, metric, missingText) {
  const row = el("div", "side");
  const who = el("span", "who");
  who.append(el("i", `swatch ${side}`), label);
  row.append(who);
  if (!metric) {
    const note = el("span", "pending", missingText);
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
function stripPlot(agentPicks, llmPicks) {
  const points = [
    ...agentPicks.filter((p) => p.affinity !== null).map((p) => ({ ...p, side: "qloo" })),
    ...(llmPicks || []).filter((p) => p.measured && p.affinity !== null).map((p) => ({ ...p, side: "llm" })),
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
  svg.setAttribute("aria-label", "Each pick placed on Qloo's taste-fit scale. Blue dots are the agent's picks, orange dots are the plain LLM's.");

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
    const who = point.side === "qloo" ? "agent with Qloo" : "plain LLM";
    attachTooltip(dot, `${point.name}: fit ${fmtScore(point.affinity)} (${who})`);
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

function statLine(side, label, parts) {
  const row = el("div", "side");
  const who = el("span", "who");
  who.append(el("i", `swatch ${side}`), label);
  const text = el("span", "stat");
  text.append(...parts);
  text.style.gridColumn = "2 / -1";
  row.append(who, text);
  return row;
}

function renderScoreboard(data, nouns) {
  const board = $("scoreboard");
  board.replaceChildren();
  const a = data.agent.metrics;
  const b = data.baseline ? data.baseline.metrics : null;
  const missing = "no plain-LLM answer";
  const agentLabel = data.agent.agent_ran ? "Agent" : "Qloo only";

  // 1. Taste fit
  const fit = metricBlock("Taste fit",
    "Average Qloo affinity between each pick and your inputs. 0 is no fit, 1 is the strongest.");
  fit.sides.append(sideRow("qloo", agentLabel, a.affinity), sideRow("llm", "Plain LLM", b && b.affinity, missing));
  const strip = stripPlot(data.agent.picks, data.baseline && data.baseline.picks);
  if (strip) fit.sides.append(strip);
  let fitNote = "The agent chooses using this score, so it leads here by design. Each dot is one pick on the same scale. Hover a dot to see which it is.";
  if (b && b.affinity.value !== null && a.affinity.value !== null) {
    const gap = a.affinity.value - b.affinity.value;
    fitNote = `The plain LLM's measured picks average ${Math.abs(gap).toFixed(3)} ${gap >= 0 ? "below" : "above"} the agent's. ` + fitNote;
  }
  fit.block.append(el("p", "caveat", fitNote));
  board.append(fit.block);

  // 2. Verified
  const verified = metricBlock("Verified in Qloo",
    "How many picks match a real entry in Qloo's catalog and could be scored.");
  const n = data.agent.picks.length;
  verified.sides.append(statLine("qloo", agentLabel, [el("b", null, `${n} of ${n}`),
    " verified. It is only allowed to submit picks Qloo has checked, so this is by rule."]));
  if (data.baseline) {
    const m = data.baseline.measured;
    const parts = [el("b", null, `${m.count} of ${m.of}`), " verified."];
    if (m.count < m.of) parts.push(" The rest are marked \"not measured\" below and left out of every number here.");
    verified.sides.append(statLine("llm", "Plain LLM", parts));
    verified.block.append(el("p", "caveat",
      `${data.overlap.count} of the agent's ${n} picks were also named by the plain LLM.`));
  } else {
    verified.sides.append(sideRow("llm", "Plain LLM", null, missing));
  }
  board.append(verified.block);

  // 3. How mainstream
  const pop = metricBlock("How mainstream",
    `Where the middle pick of each list ranks among everything in Qloo's ${nouns} catalog, by popularity.`);
  const popParts = (metric) => metric.value === null
    ? ["not enough measured picks"]
    : ["Middle pick is in the ", el("b", null, fmtTopPercent(metric.value)),
       ` most popular (percentile ${metric.value.toFixed(4)}, ${metric.n} of ${metric.of} picks)`];
  pop.sides.append(statLine("qloo", agentLabel, popParts(a.popularity)));
  pop.sides.append(b ? statLine("llm", "Plain LLM", popParts(b.popularity)) : sideRow("llm", "Plain LLM", null, missing));
  pop.block.append(el("p", "caveat",
    "A smaller top-percent means more mainstream. Neither direction is better in itself."));
  board.append(pop.block);

  // 4. Variety
  const variety = metricBlock("Variety",
    "How different the picks are from each other, from Qloo's genre tags. 0 means all the same genres, 1 means no two picks share a genre.");
  variety.sides.append(sideRow("qloo", agentLabel, a.variety), sideRow("llm", "Plain LLM", b && b.variety, missing));
  board.append(variety.block);
}

function renderTrace(agent) {
  const list = el("ol", "trace");
  for (const step of agent.trace) {
    const item = el("li");
    const text = el("span");
    text.append(el("strong", null, step.title));
    if (step.detail) text.append(`: ${step.detail}`);
    if (step.items) {
      const ideas = el("div", "ideas");
      for (const idea of step.items) {
        const chip = el("span", idea.kept ? "idea kept" : "idea");
        const verdict = idea.fit === null ? "not in Qloo" : `fit ${fmtScore(idea.fit)}`;
        chip.textContent = `${idea.name} · ${verdict} · ${idea.kept ? "kept" : "dropped"}`;
        if (idea.why_not) chip.title = idea.why_not;
        ideas.append(chip);
      }
      text.append(ideas);
    }
    const call = step.call ? (step.ms ? `${step.call} · ${step.ms} ms` : step.call) : "";
    item.append(text, el("code", null, call));
    list.append(item);
  }
  $("trace").replaceChildren(list);
  $("agent-meta").textContent = agent.agent_ran
    ? `${agent.llm_calls} LLM calls, tools chosen by the model`
    : "the LLM was unavailable";
}

function renderCurl(data) {
  const body = {
    input_domain: current.body.input_domain,
    output_domain: current.body.output_domain,
    likes: data.agent.seeds.map((s) => s.name),
    candidates: data.baseline
      ? data.baseline.picks.slice(0, 3).map((p) => (p.llm_year ? { name: p.llm_name, year: p.llm_year } : { name: p.llm_name }))
      : [{ name: "..." }],
  };
  $("curl").textContent =
    `curl -X POST ${location.origin}/api/check \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify(body).replace(/'/g, "\\u0027")}'`;
}

function reveal(choice, tally) {
  const data = current.data;
  const nouns = config.domains[current.body.output_domain].noun;
  const shared = new Set(data.overlap ? data.overlap.ids : []);
  $("blind").hidden = true;
  renderVerdict(choice, tally);
  renderScoreboard(data, nouns);
  renderTrace(data.agent);
  renderAgentList(data.agent, shared);
  renderLlmList(data, shared);
  renderCurl(data);
  $("prompt").textContent = data.baseline ? data.baseline.prompt : "No prompt was answered.";
  $("revealed").hidden = false;
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
  const mine = ++runId;

  // Put the inputs in the address bar so this exact comparison can be shared as a link.
  const query = new URLSearchParams({ from: input_domain });
  seeds.forEach((seed) => query.append("q", seed));
  if (new URLSearchParams(location.search).has("labels")) query.set("labels", "1");
  history.replaceState(null, "", `?${query}`);

  $("go").disabled = true;
  $("go").textContent = "Working…";
  $("results").hidden = true;
  $("working").hidden = false;

  try {
    const data = await postJSON("/api/compare", body);
    if (mine !== runId) return;
    current = { data, body, sideOf: null };
    renderSeeds(data.agent.seeds);
    $("results").hidden = false;

    // The blind choice only makes sense when both real answers exist and
    // this visitor has not already seen the labels for this comparison.
    // Adding &labels=1 to a link skips the blind step, for sharing the numbers directly.
    const earlier = recall(`voted:${data.comparison_id}`);
    const skipBlind = new URLSearchParams(location.search).has("labels");
    if (data.baseline && data.agent.agent_ran && !earlier && !skipBlind) {
      renderBlind(data);
    } else {
      let tally = null;
      if (earlier) {
        try { tally = await (await fetch("/api/votes")).json(); } catch (err) { /* show without it */ }
      }
      reveal(earlier && earlier !== "skipped" ? earlier : null, tally);
    }
  } catch (err) {
    if (mine !== runId) return;
    showError(err.message);
  } finally {
    if (mine === runId) {
      $("working").hidden = true;
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
  document.querySelectorAll(".vote").forEach((button) =>
    button.addEventListener("click", () => choose(button.dataset.pick)));
  $("skip-blind").addEventListener("click", () => {
    remember(`voted:${current.data.comparison_id}`, "skipped");
    reveal(null, null);
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
