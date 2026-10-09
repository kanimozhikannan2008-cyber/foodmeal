const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let role = "donor", signup = false, user = null, tab = null;

async function api(path, body) {
  const r = await fetch("/api/" + path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "Something went wrong.");
  return j;
}
function toast(msg, bad) { const t = $("#toast"); t.textContent = msg; t.className = "show" + (bad ? " bad" : ""); setTimeout(() => (t.className = ""), 3000); }
const act = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

/* ---------- Auth ---------- */
document.querySelectorAll("#roleTabs button").forEach((b) => (b.onclick = () => {
  role = b.dataset.role;
  document.querySelectorAll("#roleTabs button").forEach((x) => x.classList.toggle("on", x === b));
}));
$("#toggle").onclick = (e) => {
  e.preventDefault(); signup = !signup;
  $("#signupFields").hidden = !signup; $("#rememberRow").hidden = signup;
  $("#authBtn").textContent = signup ? "Create account" : "Log in";
  $("#toggle").textContent = signup ? "Have an account? Log in" : "New here? Create an account";
};
$("#forgot").onclick = (e) => { e.preventDefault(); toast("Password reset: contact the emergency desk below until email reset is configured."); };
$("#authForm").onsubmit = act(async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target)); f.role = role; f.remember = !!f.remember;
  $("#authErr").textContent = "";
  try {
    if (signup) { await api("signup", f); toast("Account created. Logging you in."); }
    await api("login", f);
    await boot();
  } catch (err) { $("#authErr").textContent = err.message; }
});
$("#logout").onclick = async () => { await api("logout", {}); location.reload(); };

/* ---------- Views ---------- */
const TABS = {
  donor: { "Register food": vFoodForm, "Recipients": vRecipients, "Offers sent": vOffers, "Deliveries": vDeliveries, "Impact": vImpact },
  recipient: { "Find food": vFind, "Offers for me": vOffers, "My deliveries": vDeliveries, "Impact": vImpact },
  partner: { "Pickup jobs": vDeliveries, "Impact": vImpact },
};
function nav() {
  const t = TABS[user.role]; tab = tab && t[tab] ? tab : Object.keys(t)[0];
  $("#nav").innerHTML = Object.keys(t).map((k) => `<button class="${k === tab ? "on" : ""}" data-k="${esc(k)}">${esc(k)}</button>`).join("");
  $("#nav").querySelectorAll("button").forEach((b) => (b.onclick = () => { tab = b.dataset.k; nav(); }));
  act(t[tab])();
}
const view = (h) => ($("#view").innerHTML = h);

async function vFoodForm() {
  const foods = await api("foods");
  view(`<form class="card form" id="ff"><input name="item" placeholder="Food (e.g. veg meals)" required>
    <select name="diet"><option value="vegetarian">Vegetarian</option><option value="non-vegetarian">Non-vegetarian</option></select>
    <input name="qty" type="number" min="1" placeholder="Meals" required><input name="hours" type="number" min="2" step="0.5" placeholder="Safe for how many hours?" required>
    <button class="primary">Register surplus</button></form>
    <div class="list">${foods.map((f) => `<div class="item"><div><b>${esc(f.item)}</b><small>${esc(f.diet)} · safe for ${f.hours_left}h</small></div><span class="pill">${f.remaining}/${f.qty} left</span></div>`).join("") || "<p>Nothing listed yet. Register your first surplus above.</p>"}</div>`);
  $("#ff").onsubmit = act(async (e) => { e.preventDefault(); await api("foods", Object.fromEntries(new FormData(e.target))); toast("Surplus registered."); vFoodForm(); });
}
async function vRecipients() {
  const [rs, foods] = await Promise.all([api("recipients"), api("foods")]);
  const open = foods.filter((f) => f.remaining > 0 && f.hours_left > 1);
  view(`<p>Offer food to any recipient, even one with no open request. They accept or decline.</p>
    <select id="of">${open.map((f) => `<option value="${f.id}" data-max="${f.remaining}">${esc(f.item)} (${f.remaining} left)</option>`).join("") || "<option value=''>No available food</option>"}</select>
    <div class="list">${rs.map((r) => `<div class="item"><div><b>${esc(r.org)}</b><small>${r.distance} km · needs ${r.needs} · received ${r.received}</small></div>
      <span><input type="number" min="1" value="10" style="width:5rem" id="q${r.id}"> <button data-r="${r.id}">Send offer</button></span></div>`).join("")}</div>`);
  $("#view").querySelectorAll("button[data-r]").forEach((b) => (b.onclick = act(async () => {
    await api("offers", { food_id: +$("#of").value, recipient_id: +b.dataset.r, qty: +$("#q" + b.dataset.r).value }); toast("Offer sent.");
  })));
}
async function vFind() {
  view(`<form class="card form" id="mf"><select name="diet"><option value="any">Any food</option><option value="vegetarian">Vegetarian only</option></select>
    <input name="qty" type="number" min="1" placeholder="Meals needed" required><button class="primary">Find matches</button></form><div id="res" class="list"></div>`);
  $("#mf").onsubmit = act(async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    await api("requests", f);
    const ms = await api("match", f);
    $("#res").innerHTML = ms.map((m) => `<div class="item"><div><b>${esc(m.donor)}</b><small>${esc(m.item)} · ${m.distance} km · ${m.hours_left}h safe · ${m.available} available</small></div>
      <span><span class="pill">${m.allocate} meals</span> <button class="primary" data-f="${m.food_id}" data-q="${m.allocate}">Confirm request</button></span></div>`).join("") || "<p>No safe matches right now. Your request stays open and donors can offer you food.</p>";
    $("#res").querySelectorAll("button").forEach((b) => (b.onclick = act(async () => { await api("allocate", { food_id: +b.dataset.f, qty: +b.dataset.q }); toast("Request confirmed."); tab = "My deliveries"; nav(); })));
  });
}
async function vOffers() {
  const os = await api("offers");
  view(`<div class="list">${os.map((o) => `<div class="item"><div><b>${esc(o.item)} · ${o.qty} meals</b><small>${esc(o.donor)} → ${esc(o.recipient)}</small></div>
    ${user.role === "recipient" && o.status === "pending" ? `<span><button class="primary" data-o="${o.id}" data-a="1">Accept</button> <button data-o="${o.id}">Decline</button></span>` : `<span class="pill">${esc(o.status)}</span>`}</div>`).join("") || "<p>No offers yet.</p>"}</div>`);
  $("#view").querySelectorAll("button[data-o]").forEach((b) => (b.onclick = act(async () => { await api(`offers/${b.dataset.o}/respond`, { accept: !!b.dataset.a }); vOffers(); })));
}
async function vDeliveries() {
  const as = await api("allocations");
  const next = { Confirmed: "Ready for Pickup", "Ready for Pickup": "In Transit", "In Transit": "Delivered" };
  view(`<div class="list">${as.map((a) => {
    let btn = "";
    if (user.role === "partner" && !a.partner_id) btn = `<button class="primary" data-c="${a.id}">Claim job</button>`;
    else if (user.role !== "recipient" && next[a.status]) btn = `<button class="primary" data-s="${a.id}" data-n="${next[a.status]}">Mark ${next[a.status]}</button> <button data-s="${a.id}" data-n="Failed">Report failure</button>`;
    return `<div class="item"><div><b>${esc(a.item)} · ${a.qty} meals</b><small>${esc(a.pickup)} → ${esc(a.dropoff)}</small></div><span><span class="pill">${esc(a.status)}</span> ${btn}</span></div>`;
  }).join("") || "<p>No deliveries yet.</p>"}</div>`);
  $("#view").querySelectorAll("[data-c]").forEach((b) => (b.onclick = act(async () => { await api(`allocations/${b.dataset.c}/claim`, {}); vDeliveries(); })));
  $("#view").querySelectorAll("[data-s]").forEach((b) => (b.onclick = act(async () => { await api(`allocations/${b.dataset.s}/status`, { status: b.dataset.n }); vDeliveries(); })));
}
async function vImpact() {
  const s = await api("stats");
  const box = (n, l) => `<div class="stat"><b>${n}</b>${l}</div>`;
  view(`<div class="stats">${box(s.mine ?? 0, user.role === "partner" ? "Deliveries completed by you" : "Your meals delivered")}${box(s.delivered, "Meals delivered platform-wide")}
    ${box(s.success_rate + "%", "Redistribution success rate")}${box(s.open_demand, "Unmet meal demand")}${box(s.failed, "Failed or cancelled")}${box(s.recipients, "Organizations served")}</div>`);
}

/* ---------- Boot ---------- */
async function boot() {
  const { user: u } = await api("me");
  if (!u) return;
  user = u; $("#auth").hidden = true; $("#app").hidden = false;
  $("#who").textContent = `${u.org || u.name} · ${u.role}`;
  nav();
}
(async () => {
  const { emergency: e } = await api("config");
  $("#emergency").innerHTML = `<h3>Emergency? Contact the response desk</h3><p><b>${esc(e.name)}</b></p><p>${esc(e.address)}</p><p><a href="tel:${esc(e.phone)}">${esc(e.phone)}</a> · <a href="mailto:${esc(e.email)}">${esc(e.email)}</a></p>`;
  boot();
})();
