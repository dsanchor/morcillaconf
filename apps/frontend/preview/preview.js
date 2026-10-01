// Static preview only: simulates the waiter the way FakeBffClient does.
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const params = new URLSearchParams(location.search);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const frozen = Boolean(params.get("pausa"));
  const scene = $(".escena");
  const doorstep = $(".umbral");
  const nameField = $("#nombre");
  const warning = $(".umbral .aviso");
  const inside = $(".interior");
  const messages = $(".mensajes");
  const composer = $(".redactor");
  const messageField = $("#mensaje");
  const plan = $(".planta");
  const waiter = $(".planta .camarero");
  const waiterIcon = $("#icono-camarero").innerHTML;
  let customer = "";
  let turn = 0;

  const FEMININE = new Set(("abigail amparo ane asuncion beatriz belen carmen carol consuelo dolores " +
    "edurne encarnacion esther ester garazi ines ingrid irati irene isabel itziar karen leire leonor " +
    "lourdes luz maider maite mar mari maribel marisol mercedes miriam montserrat nicole nieves noemi " +
    "paz pilar rachel raquel rocio rosario rut ruth sol soledad trinidad uxue zoe").split(" "));
  const MASCULINE = new Set("bautista borja ezra joshua josema luca lucca nicola".split(" "));
  const fold = (text) => text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
  const address = (name) => {
    const first = fold(name.split(/\s+/)[0] || "");
    if (MASCULINE.has(first)) return "majo";
    return FEMININE.has(first) || first.endsWith("a") ? "maja" : "majo";
  };
  const greeting = (name) => {
    const treatment = address(name);
    const question = treatment === "maja"
      ? "¿Has venido sola o acompañada?"
      : "¿Has venido solo o acompañado?";
    return `Hombre, ${name}, ¿qué tal, ${treatment}? ${question}`;
  };
  const wait = (ms) => new Promise((done) => setTimeout(done, reduce ? Math.min(ms, 250) : ms));
  const scrollDown = () => { messages.scrollTop = messages.scrollHeight; };

  function add(who, text) {
    const row = document.createElement("div");
    row.className = `msg ${who}`;
    if (who === "camarero") row.insertAdjacentHTML("afterbegin", waiterIcon);
    const bubble = document.createElement("div");
    bubble.className = "burbuja";
    bubble.textContent = text;
    row.append(bubble);
    messages.append(row);
    scrollDown();
    return bubble;
  }

  async function waiterSays(text, pause = 900) {
    const bubble = add("camarero", "");
    bubble.classList.add("escribiendo");
    bubble.setAttribute("aria-label", "El camarero está escribiendo");
    bubble.innerHTML = "<i></i><i></i><i></i>";
    await wait(pause);
    bubble.classList.remove("escribiendo");
    bubble.removeAttribute("aria-label");
    bubble.textContent = text;
    scrollDown();
  }

  function card(title) {
    const box = document.createElement("div");
    box.className = "tarjeta";
    const heading = document.createElement("p");
    heading.textContent = title;
    box.append(heading);
    messages.append(box);
    scrollDown();
  }

  function reply(text) {
    const allergy = text.match(/al[eé]rgic[oa]s?\s+a\s+(.+)/i);
    const preference = text.match(/prefiero\s+(.+)/i);
    const found = allergy || preference;
    if (!found) return ["Tomo nota.", `Entendido, ${customer}.`, "Te escucho."][turn++ % 3];
    const value = found[1].replace(/[.!]+$/, "");
    return allergy ? `Apuntado: ${value}. Te lo preguntaré en cada visita.` : `Apuntado: ${value}.`;
  }

  function command(text) {
    const [name] = text.split(/\s+/);
    if (name === "/new") { messages.replaceChildren(); waiterSays(greeting(customer)); return; }
    if (name === "/exit") { leave(); return; }
    card("Ese comando no lo conozco: mira la lista de la izquierda.");
  }

  function settleWaiter() {
    plan.classList.remove("barra", "llegando");
    plan.classList.add("atendiendo");
    waiter.setAttribute("transform", "translate(192,272)");
  }

  async function enter(name, { skip = false } = {}) {
    customer = name;
    $(".quien .nombre").textContent = name;
    $(".cliente text", plan).textContent = name.length <= 16 ? name : `${name.slice(0, 15)}…`;
    doorstep.hidden = true;
    inside.hidden = false;
    if (skip) { scene.hidden = true; settleWaiter(); return; }
    scene.className = "escena abriendo";
    inside.classList.add("entrando");
    plan.classList.replace("barra", "llegando");
    if (frozen) return;
    await wait(2600);
    scene.hidden = true;
    await wait(500);
    await waiterSays(greeting(name));
    await wait(1500);
    settleWaiter();
  }

  function leave() {
    messages.replaceChildren();
    memories = [];
    nextId = 1;
    inside.hidden = true;
    inside.classList.remove("entrando", "menu-abierto");
    plan.classList.remove("atendiendo");
    plan.classList.add("barra");
    waiter.setAttribute("transform", "translate(560,124)");
    scene.hidden = false;
    scene.className = "escena cerrada";
    doorstep.hidden = false;
    nameField.value = "";
    nameField.focus();
  }

  doorstep.addEventListener("submit", (event) => {
    event.preventDefault();
    const name = nameField.value.trim().replace(/\s+/g, " ");
    if (!name) {
      scene.className = "escena cerrada";
      void scene.offsetWidth;
      scene.className = "escena llama";
      warning.hidden = false;
      nameField.setAttribute("aria-invalid", "true");
      nameField.focus();
      return;
    }
    warning.hidden = true;
    nameField.removeAttribute("aria-invalid");
    enter(name);
  });

  composer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = messageField.value.trim();
    if (!text) return;
    messageField.value = "";
    if (text.startsWith("/")) { command(text); return; }
    add("cliente", text);
    await waiterSays(reply(text), 700 + Math.random() * 500);
  });

  document.querySelectorAll(".comando").forEach((button) => {
    button.addEventListener("click", () => {
      inside.classList.remove("menu-abierto");
      const value = button.dataset.cmd;
      if (value.includes("<")) {
        messageField.value = `${value.split(" <")[0]} `;
        messageField.focus();
        return;
      }
      command(value);
    });
  });
  $(".abrir-menu").addEventListener("click", () => inside.classList.toggle("menu-abierto"));

  // Fixed states for screenshots: ?estado=dentro|llama|abriendo&nombre=Ana&muestra=1&pausa=ms
  const state = params.get("estado");
  const presetName = params.get("nombre") || "Ana";
  if (state === "dentro") {
    document.documentElement.classList.add("sin-animacion");
    enter(presetName, { skip: true });
    add("camarero", greeting(presetName));
    if (params.get("muestra")) {
      add("cliente", "Prefiero el agua con gas y soy alérgica a los frutos secos");
      add("camarero", "Apuntado: agua con gas. Y tu alergia a los frutos secos, te la preguntaré en cada visita.");
    }
  } else if (state === "llama") {
    scene.className = "escena llama";
    warning.hidden = false;
    nameField.setAttribute("aria-invalid", "true");
  } else if (state === "abriendo") {
    enter(presetName);
    const at = Number(params.get("pausa"));
    if (at) {
      requestAnimationFrame(() => document.getAnimations().forEach((animation) => {
        animation.pause();
        animation.currentTime = at;
      }));
    }
  } else {
    nameField.focus();
  }
})();
