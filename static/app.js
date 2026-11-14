// Live osvezavanje, obavestenja i AJAX forme. Bez biblioteka.
(() => {
  const body = document.body;
  const box = document.getElementById("toasts");

  const dismiss = (el) => { setTimeout(() => el.classList.add("out"), 4200); setTimeout(() => el.remove(), 4600); };
  document.querySelectorAll(".toast").forEach(dismiss);
  const toast = (text) => {
    const el = document.createElement("div");
    el.className = "toast"; el.setAttribute("role", "status"); el.textContent = text;
    box.append(el); dismiss(el);
  };

  // zvuk se moze pustiti tek posle prvog dodira
  let ac;
  document.addEventListener("pointerdown", () => { ac ||= new (window.AudioContext || window.webkitAudioContext)(); }, { once: true });
  const beep = () => {
    if (!ac) return;
    const o = ac.createOscillator(), g = ac.createGain(), t = ac.currentTime;
    o.frequency.setValueAtTime(660, t); o.frequency.setValueAtTime(990, t + .12);
    g.gain.setValueAtTime(.2, t); g.gain.exponentialRampToValueAtTime(.001, t + .45);
    o.connect(g).connect(ac.destination); o.start(t); o.stop(t + .45);
  };
  const notify = (text) => {
    toast(text); beep(); navigator.vibrate?.([120, 60, 120]);
    if (document.hidden && window.Notification?.permission === "granted") new Notification("DD", { body: text, icon: "/static/icon.svg" });
  };

  // dugme za obavestenja: vidi se samo dok dozvola nije data
  const askBtns = document.querySelectorAll("[data-notif]");
  const syncAsk = () => askBtns.forEach((b) => { b.hidden = !("Notification" in window) || Notification.permission !== "default"; });
  askBtns.forEach((b) => b.addEventListener("click", async () => { await Notification.requestPermission(); syncAsk(); beep(); }));
  syncAsk();

  // zamena [data-live] delova stranice svezim HTML-om
  let busy = false;
  const refresh = async () => {
    if (busy) return; busy = true;
    try {
      const html = await (await fetch(location.href, { credentials: "same-origin" })).text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      document.querySelectorAll("[data-live]").forEach((el) => {
        const fresh = doc.getElementById(el.id);
        // ne diraj deo u kome korisnik upravo kuca
        const typing = el.contains(document.activeElement) && document.activeElement.matches("input, textarea, select");
        if (fresh && !typing) el.replaceWith(fresh);
      });
    } catch {} finally { busy = false; }
  };

  // forme sa data-ajax: bez reload-a
  document.addEventListener("submit", async (e) => {
    const f = e.target;
    if (!f.matches("[data-ajax]")) return;
    e.preventDefault();
    const btn = f.querySelector("button"); btn?.setAttribute("aria-busy", "true");
    try {
      const r = await fetch(f.action, { method: "POST", body: new FormData(f), credentials: "same-origin" });
      if (r.redirected && !r.url.startsWith(location.href.split("#")[0].split("?")[0])) { location.href = r.url; return; }
      await refresh();
    } finally { btn?.removeAttribute("aria-busy"); }
  });

  // TV: samo osvezavaj. Ostali: pitaj server sta je novo.
  if (body.classList.contains("tv")) { setInterval(refresh, 5000); return; }
  const every = +body.dataset.poll;
  if (!every) return;
  let since = +body.dataset.since || 0;
  // ponytail: polling umesto WebSocket/SSE, jer cPanel/Passenger ne voli duge konekcije
  setInterval(async () => {
    try {
      const r = await fetch("/api/poll?since=" + since, { credentials: "same-origin" });
      if (!r.ok) return;
      const d = await r.json();
      since = d.last;
      d.events.filter(Boolean).forEach(notify);
      if (d.events.length) refresh();
    } catch {}
  }, every);

  document.querySelector('.tabs [aria-current="page"]')?.scrollIntoView({ inline: "center", block: "nearest" });
})();
