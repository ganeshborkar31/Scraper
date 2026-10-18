const PAGE_SIZE = 25;
const state = { businessOffset: 0, jobOffset: 0, businessFilters: {}, activeView: "overview" };
const $ = (selector, parent = document) => parent.querySelector(selector);

function node(tag, text = "", className = "") {
  const item = document.createElement(tag);
  if (text !== null && text !== undefined) item.textContent = String(text);
  if (className) item.className = className;
  return item;
}

function clear(element) {
  element.replaceChildren();
}

function showNotice(message, kind = "success") {
  const notice = $("#notice");
  notice.textContent = message;
  notice.className = `notice ${kind}`;
  notice.hidden = false;
}

function clearNotice() {
  const notice = $("#notice");
  notice.hidden = true;
  notice.textContent = "";
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response;
  try {
    response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  } catch {
    throw new Error("Could not reach the application. Check the connection and try again.");
  }
  if (!response.ok) {
    let reason = `Request failed (${response.status}).`;
    try {
      const payload = await response.json();
      const detail = payload.detail;
      reason = typeof detail === "string" ? detail : detail?.message || JSON.stringify(detail || payload);
      if (response.status === 401) reason = "Your administrator authentication is required. Reload and sign in again.";
    } catch {
      // Keep the HTTP status message if the server returned a non-JSON response.
    }
    throw new Error(reason);
  }
  if (response.status === 204) return null;
  return response.json();
}

function date(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "—" : parsed.toLocaleString();
}

function badge(status) {
  const className = String(status || "unknown").replace(/[^a-z_]/gi, "").toLowerCase();
  return node("span", status || "unknown", `badge ${className}`);
}

function loading(container, message = "Loading…") {
  clear(container);
  container.append(node("p", message, "state-message"));
}

function empty(container, message) {
  clear(container);
  container.append(node("p", message, "state-message empty-state"));
}

function errorState(container, error, retry) {
  clear(container);
  const box = node("div", "", "inline-error");
  box.append(node("p", error.message || "Unable to load this section."));
  if (retry) {
    const button = node("button", "Try again", "button secondary");
    button.type = "button";
    button.addEventListener("click", retry);
    box.append(button);
  }
  container.append(box);
}

function table(headers, rows) {
  const wrapper = node("div", "", "table-wrap");
  const tableElement = node("table");
  const head = node("thead");
  const headerRow = node("tr");
  headers.forEach((header) => headerRow.append(node("th", header)));
  head.append(headerRow);
  const body = node("tbody");
  rows.forEach((row) => body.append(row));
  tableElement.append(head, body);
  wrapper.append(tableElement);
  return wrapper;
}

function textCell(value, className = "") {
  return node("td", value ?? "—", className);
}

function safeExternalLink(url, label) {
  if (!url) return node("span", "—", "muted");
  try {
    const parsed = new URL(url, window.location.origin);
    if (!["http:", "https:"].includes(parsed.protocol)) return node("span", label || url);
    const link = node("a", label || url);
    link.href = parsed.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch {
    return node("span", label || url);
  }
}

function showDialog(title, content) {
  $("#detail-title").textContent = title;
  const target = $("#detail-content");
  clear(target);
  target.append(content);
  $("#detail-dialog").showModal();
}

function definitionList(entries) {
  const list = node("dl", "", "details-list");
  entries.forEach(([label, value]) => {
    list.append(node("dt", label), node("dd", value === null || value === "" ? "—" : value));
  });
  return list;
}

function showView(view) {
  state.activeView = view;
  clearNotice();
  document.querySelectorAll(".view").forEach((section) => {
    section.hidden = section.id !== `view-${view}`;
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const loaders = {
    overview: loadOverview,
    businesses: loadBusinesses,
    sources: loadSources,
    jobs: loadJobs,
    schedules: loadSchedules,
  };
  loaders[view]?.();
  history.replaceState(null, "", `#${view}`);
}

async function loadOverview() {
  const cards = $("#overview-cards");
  const jobs = $("#overview-jobs");
  const failures = $("#overview-failures");
  const activity = $("#overview-activity");
  loading(cards);
  loading(jobs);
  loading(failures);
  loading(activity);
  try {
    const data = await api("/dashboard/overview");
    clear(cards);
    const metrics = [
      ["Businesses", data.business_count],
      ["Sources", `${data.enabled_source_count} enabled / ${data.source_count} total`],
      ["Jobs in progress", (data.job_counts.queued || 0) + (data.job_counts.running || 0) + (data.job_counts.retrying || 0)],
      ["Failed or partial jobs", (data.job_counts.failed || 0) + (data.job_counts.partial_success || 0)],
    ];
    metrics.forEach(([label, value]) => {
      const card = node("article", "", "metric-card");
      card.append(node("p", label, "metric-label"), node("p", value, "metric-value"));
      cards.append(card);
    });
    renderJobRows(jobs, data.recent_jobs, false);
    renderJobRows(failures, data.recent_failures, false, "No failed or partial jobs are recorded.");
    if (!data.recent_activity.length) empty(activity, "No collected source records yet.");
    else {
      const rows = data.recent_activity.map((item) => {
        const row = node("tr");
        const businessCell = node("td");
        const open = node("button", item.business_name, "link-button");
        open.addEventListener("click", () => openBusiness(item.business_id));
        businessCell.append(open);
        row.append(businessCell, textCell(item.source_name), textCell(date(item.collected_at)));
        const sourceCell = node("td");
        if (item.source_url) sourceCell.append(safeExternalLink(item.source_url, "Source"));
        else sourceCell.textContent = "—";
        row.append(sourceCell);
        return row;
      });
      clear(activity);
      activity.append(table(["Business", "Source", "Collected", "Provenance"], rows));
    }
  } catch (error) {
    errorState(cards, error, loadOverview);
    [jobs, failures, activity].forEach((container) => empty(container, "Overview data is unavailable."));
  }
}

function renderJobRows(container, items, actions = true, emptyMessage = "No jobs have been submitted.") {
  if (!items?.length) return empty(container, emptyMessage);
  const rows = items.map((job) => {
    const outcome = job.outcome || {};
    const row = node("tr");
    const idCell = node("td");
    const open = node("button", job.query || `Job ${job.id.slice(0, 8)}`, "link-button");
    open.addEventListener("click", () => openJob(job.id));
    idCell.append(open);
    row.append(idCell, textCell(job.country), textCell(job.trigger_type), textCell(job.status));
    const countCell = node("td", `${job.accepted ?? outcome.accepted ?? 0} accepted · ${job.rejected ?? outcome.rejected ?? 0} rejected`);
    row.append(countCell, textCell(date(job.created_at)));
    if (actions) {
      const cell = node("td");
      if (["queued", "running", "retrying", "cancel_requested"].includes(job.status)) {
        const cancel = node("button", job.status === "cancel_requested" ? "Cancel requested" : "Cancel", "button danger small");
        cancel.disabled = job.status === "cancel_requested";
        cancel.addEventListener("click", () => cancelJob(job.id));
        cell.append(cancel);
      }
      row.append(cell);
    }
    return row;
  });
  clear(container);
  container.append(table(["Job", "Country", "Trigger", "Status", "Records", "Created", ...(actions ? ["Actions"] : [])], rows));
}

async function loadSources() {
  const container = $("#source-results");
  loading(container);
  try {
    state.sources = await api("/sources");
    renderSources(container);
    populateSourceSelects();
  } catch (error) {
    errorState(container, error, loadSources);
  }
}

function renderSources(container) {
  if (!state.sources.length) return empty(container, "No sources are configured.");
  clear(container);
  state.sources.forEach((source) => {
    const card = node("article", "", "panel source-card");
    const heading = node("div", "", "source-heading");
    const title = node("div");
    title.append(node("h2", source.name), node("p", `${source.code} · ${source.connector}`, "muted"));
    heading.append(title, badge(source.enabled ? "enabled" : "disabled"));
    card.append(heading);
    const details = node("div", "", "source-details");
    details.append(definitionList([
      ["Review", source.review_status],
      ["Countries", (source.countries || []).join(", ") || "Not restricted in source metadata"],
      ["Last run", date(source.last_run_at)],
      ["Last successful run", date(source.last_success_at)],
    ]));
    const config = node("div");
    config.append(node("h3", "Supported configuration"));
    config.append(node("pre", JSON.stringify(source.supported_configuration || {}, null, 2), "config-block"));
    details.append(config);
    card.append(details);
    if (source.license) card.append(node("p", source.license, "muted source-license"));
    if (source.attribution) card.append(node("p", source.attribution, "muted source-license"));
    const links = node("p", "", "source-links");
    if (source.docs_url) links.append(safeExternalLink(source.docs_url, "Documentation"));
    if (source.terms_url) links.append(safeExternalLink(source.terms_url, "Terms and license"));
    card.append(links);
    if (source.last_error) card.append(node("p", `Last error: ${source.last_error}`, "inline-error-text"));
    const control = node("button", source.enabled ? "Disable source" : "Enable source", source.enabled ? "button secondary" : "button primary");
    control.disabled = source.review_status !== "approved";
    control.setAttribute("aria-label", `${source.enabled ? "Disable" : "Enable"} ${source.name}`);
    control.addEventListener("click", () => toggleSource(source));
    card.append(control);
    container.append(card);
  });
}

function populateSourceSelects() {
  const select = $("#business-filter [name=source_id]");
  const selected = select.value;
  select.replaceChildren(node("option", "All sources"));
  select.firstElementChild.value = "";
  state.sources.forEach((source) => {
    const option = node("option", source.name);
    option.value = source.id;
    select.append(option);
  });
  select.value = selected;

  const picker = $("#schedule-source-options");
  clear(picker);
  if (!state.sources.length) return empty(picker, "Configure a source before creating a schedule.");
  state.sources.forEach((source) => {
    const label = node("label", "", "source-option");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.name = "source_ids";
    checkbox.value = source.id;
    checkbox.disabled = !source.enabled || source.review_status !== "approved";
    const caption = node("span", `${source.name}${checkbox.disabled ? ` (${source.review_status})` : ""}`);
    const cap = node("input");
    cap.type = "number";
    cap.min = "1";
    cap.max = "1000";
    cap.placeholder = "Cap";
    cap.setAttribute("aria-label", `Optional per-job record cap for ${source.name}`);
    cap.dataset.sourceLimit = source.id;
    cap.disabled = checkbox.disabled;
    checkbox.addEventListener("change", () => { cap.disabled = !checkbox.checked; });
    label.append(checkbox, caption, cap);
    picker.append(label);
  });
}

async function toggleSource(source) {
  clearNotice();
  try {
    await api(`/sources/${encodeURIComponent(source.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !source.enabled }),
    });
    showNotice(`${source.name} ${source.enabled ? "disabled" : "enabled"}.`);
    await loadSources();
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function loadBusinesses() {
  const container = $("#business-results");
  loading(container);
  const params = new URLSearchParams({ limit: PAGE_SIZE, offset: state.businessOffset });
  Object.entries(state.businessFilters).forEach(([key, value]) => { if (value) params.set(key, value); });
  try {
    const data = await api(`/businesses?${params}`);
    $("#business-total").textContent = `${data.total} records`;
    $("#business-page-label").textContent = data.total ? `${state.businessOffset + 1}–${Math.min(state.businessOffset + data.items.length, data.total)} of ${data.total}` : "0 records";
    const previous = document.querySelector('[data-page="businesses"][data-delta="-1"]');
    const next = document.querySelector('[data-page="businesses"][data-delta="1"]');
    previous.disabled = state.businessOffset === 0;
    next.disabled = state.businessOffset + PAGE_SIZE >= data.total;
    if (!data.items.length) return empty(container, "No businesses match these filters.");
    const rows = data.items.map((business) => {
      const row = node("tr");
      const nameCell = node("td");
      const open = node("button", business.name, "link-button");
      open.addEventListener("click", () => openBusiness(business.id));
      nameCell.append(open);
      row.append(nameCell, textCell(business.country), textCell(business.region), textCell(business.city), textCell((business.categories || []).join(", ")), textCell(business.synthetic ? "Synthetic sample" : "Collected"));
      return row;
    });
    clear(container);
    container.append(table(["Business", "Country", "Region", "City", "Category", "Record type"], rows));
  } catch (error) {
    errorState(container, error, loadBusinesses);
  }
}

async function openBusiness(id) {
  try {
    const business = await api(`/businesses/${encodeURIComponent(id)}`);
    const content = node("div");
    content.append(definitionList([
      ["Country", business.country], ["Region", business.region], ["City", business.city],
      ["Address", business.address], ["Category", (business.categories || []).join(", ")],
      ["Website", business.website], ["Domain", business.domain], ["Telephone", business.phone],
      ["Email", business.email], ["Coordinates", business.latitude == null ? null : `${business.latitude}, ${business.longitude}`],
      ["Record type", business.synthetic ? "Synthetic sample" : "Collected business"],
    ]));
    content.append(node("h3", "Source provenance"));
    if (!business.provenance?.length) content.append(node("p", "No source observations are attached.", "muted"));
    else {
      const list = node("ul", "", "provenance-list");
      business.provenance.forEach((record) => {
        const item = node("li");
        item.append(node("strong", record.source), node("span", ` · ${date(record.collected_at)} · Match: ${record.match_classification || "unclassified"}`));
        if (record.attribution) item.append(node("p", record.attribution, "muted"));
        if (record.source_url) item.append(safeExternalLink(record.source_url, "Open source record"));
        const raw = node("details");
        raw.append(node("summary", "Original source fields"), node("pre", JSON.stringify(record.raw_values || {}, null, 2), "config-block"));
        item.append(raw);
        list.append(item);
      });
      content.append(list);
    }
    showDialog(business.name, content);
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function loadJobs() {
  const container = $("#job-results");
  loading(container);
  try {
    const items = await api(`/jobs?limit=${PAGE_SIZE}&offset=${state.jobOffset}`);
    $("#job-page-label").textContent = items.length ? `Showing ${state.jobOffset + 1}–${state.jobOffset + items.length}` : "No jobs";
    document.querySelector('[data-page="jobs"][data-delta="-1"]').disabled = state.jobOffset === 0;
    document.querySelector('[data-page="jobs"][data-delta="1"]').disabled = items.length < PAGE_SIZE;
    renderJobRows(container, items, true);
  } catch (error) {
    errorState(container, error, loadJobs);
  }
}

async function openJob(id) {
  try {
    const job = await api(`/jobs/${encodeURIComponent(id)}`);
    const content = node("div");
    content.append(badge(job.status));
    content.append(definitionList([
      ["Job ID", job.id], ["Query", job.query], ["Country", job.country], ["Trigger", job.trigger_type],
      ["Sources", (job.source_ids || []).join(", ")], ["Created", date(job.created_at)],
      ["Started", date(job.started_at)], ["Completed", date(job.completed_at)],
      ["Retries", `${job.retry_count} / ${job.retry_limit}`], ["Timeout", `${job.execution_timeout_seconds} seconds`],
      ["Error", job.error],
    ]));
    content.append(node("h3", "Collection summary"));
    const outcome = job.outcome || {};
    content.append(definitionList([
      ["Discovered", outcome.discovered ?? 0], ["Accepted", outcome.accepted ?? 0],
      ["Rejected", outcome.rejected ?? 0], ["Inserted", outcome.inserted ?? outcome.created ?? 0],
      ["Updated", outcome.updated ?? 0],
    ]));
    const sources = outcome.sources || {};
    const sourceEntries = Object.entries(sources);
    if (sourceEntries.length) {
      content.append(node("h3", "Per-source outcomes"));
      const list = node("div", "", "stack");
      sourceEntries.forEach(([source, result]) => {
        const section = node("article", "", "source-outcome");
        section.append(node("strong", source), badge(result.status));
        section.append(node("p", result.error || result.reason || result.errors?.map((issue) => issue.message).join("; ") || "No source error recorded."));
        section.append(node("p", `${result.discovered ?? 0} discovered · ${result.accepted ?? 0} accepted · ${result.rejected ?? 0} rejected · ${result.inserted ?? result.created ?? 0} inserted · ${result.updated ?? 0} updated`));
        list.append(section);
      });
      content.append(list);
    }
    if (["queued", "running", "retrying", "cancel_requested"].includes(job.status)) {
      const cancel = node("button", job.status === "cancel_requested" ? "Cancellation requested" : "Request cancellation", "button danger");
      cancel.disabled = job.status === "cancel_requested";
      cancel.addEventListener("click", async () => { await cancelJob(job.id); $("#detail-dialog").close(); });
      content.append(cancel);
    }
    showDialog(`Job ${job.id.slice(0, 8)}`, content);
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function cancelJob(id) {
  try {
    const result = await api(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
    showNotice(result.status === "cancel_requested" ? "Cancellation requested. The worker will acknowledge at a connector boundary." : `Job status: ${result.status}.`);
    await loadJobs();
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function loadSchedules() {
  const container = $("#schedule-results");
  loading(container);
  try {
    const schedules = await api("/schedules");
    if (!schedules.length) return empty(container, "No schedules are configured.");
    clear(container);
    schedules.forEach((schedule) => {
      const card = node("article", "", "panel schedule-card");
      const head = node("div", "", "schedule-heading");
      head.append(node("div", "", ""));
      const title = node("div");
      title.append(node("h3", schedule.query), node("p", `${(schedule.countries || [schedule.country]).join(", ")} · ${schedule.category || "Any configured category"}`, "muted"));
      head.replaceChildren(title, badge(schedule.archived ? "archived" : schedule.enabled ? "enabled" : "paused"));
      card.append(head);
      card.append(definitionList([
        ["Frequency", `${schedule.interval_minutes} minutes`], ["Per-country maximum", schedule.max_results],
        ["Source IDs", (schedule.source_ids || []).join(", ") || "Automatically eligible sources"],
        ["Per-source caps", JSON.stringify(schedule.source_limits || {})], ["Next run", date(schedule.next_run_at)],
        ["Last run", date(schedule.last_run_at)], ["Last error", schedule.last_error],
      ]));
      if (!schedule.archived) {
        const actions = node("div", "", "button-row");
        const toggle = node("button", schedule.enabled ? "Pause" : "Resume", "button secondary");
        toggle.addEventListener("click", () => toggleSchedule(schedule));
        const archive = node("button", "Archive", "button danger");
        archive.addEventListener("click", () => archiveSchedule(schedule));
        const historyButton = node("button", "Execution history", "button secondary");
        historyButton.addEventListener("click", () => showScheduleRuns(schedule));
        actions.append(toggle, archive, historyButton);
        card.append(actions);
      }
      container.append(card);
    });
  } catch (error) {
    errorState(container, error, loadSchedules);
  }
}

async function toggleSchedule(schedule) {
  try {
    await api(`/schedules/${encodeURIComponent(schedule.id)}?enabled=${!schedule.enabled}`, { method: "PATCH" });
    showNotice(`Schedule ${schedule.enabled ? "paused" : "resumed"}.`);
    await loadSchedules();
  } catch (error) { showNotice(error.message, "error"); }
}

async function archiveSchedule(schedule) {
  if (!window.confirm(`Archive the schedule “${schedule.query}”? Its execution history will be retained.`)) return;
  try {
    await api(`/schedules/${encodeURIComponent(schedule.id)}`, { method: "DELETE" });
    showNotice("Schedule archived.");
    await loadSchedules();
  } catch (error) { showNotice(error.message, "error"); }
}

async function showScheduleRuns(schedule) {
  try {
    const data = await api(`/schedules/${encodeURIComponent(schedule.id)}/runs?limit=50&offset=0`);
    const content = node("div");
    if (!data.items.length) content.append(node("p", "No execution history is recorded.", "muted"));
    else {
      const rows = data.items.map((run) => {
        const row = node("tr");
        row.append(textCell(date(run.scheduled_for)), textCell(run.status), textCell((run.job_ids || []).join(", ") || "—"), textCell(run.error));
        return row;
      });
      content.append(table(["Scheduled for", "Status", "Job IDs", "Error"], rows));
    }
    showDialog(`Schedule history · ${schedule.query}`, content);
  } catch (error) { showNotice(error.message, "error"); }
}

async function createSchedule(event) {
  event.preventDefault();
  clearNotice();
  const form = event.currentTarget;
  const values = new FormData(form);
  const selectedSources = [...form.querySelectorAll('input[name="source_ids"]:checked')].map((input) => input.value);
  const sourceLimits = {};
  selectedSources.forEach((id) => {
    const value = form.querySelector(`[data-source-limit="${CSS.escape(id)}"]`).value;
    if (value) sourceLimits[id] = Number(value);
  });
  const boundNames = ["south", "west", "north", "east"];
  const boundValues = boundNames.map((key) => values.get(key));
  let bounds = null;
  if (boundValues.some((value) => value !== "")) {
    if (boundValues.some((value) => value === "")) return showNotice("Enter all four geographic bounds or leave them all blank.", "error");
    bounds = Object.fromEntries(boundNames.map((key) => [key, Number(values.get(key))]));
  }
  const payload = {
    query: values.get("query"),
    countries: String(values.get("countries")).split(",").map((country) => country.trim()).filter(Boolean),
    region: values.get("region") || null,
    city: values.get("city") || null,
    category: values.get("category") || null,
    source_ids: selectedSources.length ? selectedSources : null,
    interval_minutes: Number(values.get("interval_minutes")),
    max_results: Number(values.get("max_results")),
    source_limits: sourceLimits,
    bounds,
    enabled: values.get("enabled") === "on",
    allow_overlap: values.get("allow_overlap") === "on",
    retry_attempts: Number(values.get("retry_attempts")),
  };
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  submit.textContent = "Creating…";
  try {
    const result = await api("/schedules", { method: "POST", body: JSON.stringify(payload) });
    form.reset();
    populateSourceSelects();
    showNotice(`Schedule created${result.enabled ? " and enabled" : ""}.`);
    await loadSchedules();
  } catch (error) { showNotice(error.message, "error"); }
  finally { submit.disabled = false; submit.textContent = "Create schedule"; }
}

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
document.querySelectorAll("[data-open-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.openView)));
document.querySelectorAll("[data-refresh]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.refresh)));
document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => {
  const amount = Number(button.dataset.delta) * PAGE_SIZE;
  if (button.dataset.page === "businesses") {
    state.businessOffset = Math.max(0, state.businessOffset + amount);
    loadBusinesses();
  } else {
    state.jobOffset = Math.max(0, state.jobOffset + amount);
    loadJobs();
  }
}));
$("#business-filter").addEventListener("submit", (event) => {
  event.preventDefault();
  state.businessFilters = Object.fromEntries([...new FormData(event.currentTarget)].map(([key, value]) => [key, String(value).trim()]));
  state.businessOffset = 0;
  loadBusinesses();
});
$("#schedule-form").addEventListener("submit", createSchedule);
$("[data-close-dialog]").addEventListener("click", () => $("#detail-dialog").close());
$("#detail-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
window.addEventListener("hashchange", () => {
  const view = window.location.hash.slice(1);
  if (["overview", "businesses", "sources", "jobs", "schedules"].includes(view)) showView(view);
});

async function initialize() {
  const requestedView = window.location.hash.slice(1);
  await loadSources();
  showView(["overview", "businesses", "sources", "jobs", "schedules"].includes(requestedView) ? requestedView : "overview");
}

initialize();
window.setInterval(() => {
  if (state.activeView === "overview") loadOverview();
  if (state.activeView === "jobs") loadJobs();
}, 30000);
