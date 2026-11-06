// PaySathi demo: renders every string from locales/<lang>.json, falling back
// to English (marked) for anything not translated yet.

const NATIVE = {
  en: "English", hi: "हिन्दी", ta: "தமிழ்", bn: "বাংলা", mr: "मराठी",
  te: "తెలుగు", kn: "ಕನ್ನಡ", ml: "മലയാളം", gu: "ગુજરાતી", pa: "ਪੰਜਾਬੀ", or: "ଓଡ଼ିଆ",
};

const cache = {};
let approvals = null;

// Written by `bhasha release merge`: who approved each language, and a SHA-256
// of the exact strings they approved.
async function loadApprovals() {
  if (approvals === null) {
    try {
      const res = await fetch("locales/approvals.json", { cache: "no-store" });
      approvals = res.ok ? await res.json() : {};
    } catch (_) {
      approvals = {};
    }
  }
  return approvals;
}

// The same canonical JSON as bhasha/integrity.py: keys sorted, no whitespace, UTF-8.
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function sha256(text) {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return "sha256:" + Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function showApproval(code, strings) {
  const el = document.getElementById("approval");
  const data = await loadApprovals();
  const a = data.languages && data.languages[code];
  el.className = "approval";
  el.hidden = code === "en" || !a;
  if (el.hidden) return;
  let verified = null;
  try {
    verified = (await sha256(canonical(strings))) === a.strings_sha256;
  } catch (_) {
    // No WebCrypto outside a secure context (https or localhost): say who approved, without the check.
  }
  if (verified === false) {
    el.classList.add("changed");
    el.textContent = `⚠ These ${a.name} strings changed after ${a.approved_by} approved them for release ${data.release}.`;
    return;
  }
  el.classList.add("ok");
  const where = a.approved_in ? ` in #${a.approved_in}` : "";
  el.textContent = `✓ ${a.name} approved by ${a.approved_by}${where} for release ${data.release}` +
    (verified ? ` · verified in your browser: these are exactly the strings approved (${a.strings_sha256.slice(7, 19)})` : "");
}

async function load(code) {
  if (!cache[code]) {
    const res = await fetch(`locales/${code}.json`, { cache: "no-store" });
    cache[code] = res.ok ? await res.json() : {};
  }
  return cache[code];
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Only <b> survives, and only as markup we write ourselves.
function render(template, vars) {
  let html = escapeHtml(template).replace(/&lt;(\/?)b&gt;/g, "<$1b>");
  for (const [name, value] of Object.entries(vars || {})) {
    html = html.split(`{${name}}`).join(`<span class="var">${escapeHtml(String(value))}</span>`);
  }
  return html;
}

async function show(code) {
  const [en, strings] = await Promise.all([load("en"), load(code)]);
  const plural = new Intl.PluralRules(code === "en" ? "en" : code);
  let total = 0, translated = 0;
  const seen = new Set();

  document.querySelectorAll("[data-key]").forEach((el) => {
    let key = el.dataset.key;
    const vars = el.dataset.vars ? JSON.parse(el.dataset.vars) : {};
    if (el.dataset.count) {
      const count = Number(el.dataset.count);
      key = `${key}.${plural.select(count) === "one" ? "one" : "other"}`;
      vars.count = count;
    }
    const text = strings[key];
    const missing = code !== "en" && (text === undefined || text === "");
    el.innerHTML = render(missing ? en[key] || key : text || en[key] || key, vars);
    el.classList.toggle("missing", missing);
    el.title = missing ? `${key}: not translated yet` : key;
    if (!seen.has(key)) {
      seen.add(key);
      total += 1;
      translated += missing ? 0 : 1;
    }
  });

  document.documentElement.lang = code;
  document.querySelectorAll("#langs button").forEach((b) => b.classList.toggle("on", b.dataset.code === code));
  document.getElementById("coverage").textContent =
    code === "en" ? "" : `${NATIVE[code] || code}: ${translated} of ${total} strings on screen are translated.`;
  try { localStorage.setItem("paysathi.lang", code); } catch (_) { /* private mode */ }
  history.replaceState(null, "", `?lang=${code}`);
  await showApproval(code, strings);
}

async function start() {
  const res = await fetch("locales/index.json", { cache: "no-store" });
  const codes = res.ok ? await res.json() : ["en"];
  const nav = document.getElementById("langs");
  codes.forEach((code) => {
    const b = document.createElement("button");
    b.dataset.code = code;
    b.textContent = NATIVE[code] || code;
    b.addEventListener("click", () => show(code));
    nav.appendChild(b);
  });
  let saved = null;
  try { saved = localStorage.getItem("paysathi.lang"); } catch (_) { /* private mode */ }
  const wanted = new URLSearchParams(location.search).get("lang") || saved || "en";
  show(codes.includes(wanted) ? wanted : "en");
}

start();
