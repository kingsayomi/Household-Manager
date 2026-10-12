const API = "/api";
let signedIn = false;
let started = false;

// ---------- Talking to the server ----------
async function api(path, method = "GET", body) {
  const res = await fetch(API + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    /* no JSON body */
  }
  if (!res.ok) {
    const detail = data && data.detail;
    const message = typeof detail === "string" ? detail : "Please check what you entered.";
    if (res.status === 401 && signedIn) {
      signedIn = false;
      showAuth();
    }
    throw new Error(message);
  }
  return data;
}

// Wraps a click/submit handler so server errors show as a simple message
function guard(fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (e) {
      if (e.message !== "Not signed in") alert(e.message);
    }
  };
}

// ---------- Sign in / sign up ----------
const authView = document.getElementById("authView");
const appView = document.getElementById("appView");
const userBar = document.getElementById("userBar");
const authError = document.getElementById("authError");
const loginForm = document.getElementById("loginForm");
const signupForm = document.getElementById("signupForm");
const tabLogin = document.getElementById("tabLogin");
const tabSignup = document.getElementById("tabSignup");

// Eye button: show / hide the password
document.querySelectorAll(".pw-eye").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = document.getElementById(btn.dataset.show);
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.classList.toggle("on", show);
    btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
  });
});

function showAuth() {
  authView.hidden = false;
  appView.hidden = true;
  userBar.hidden = true;
  clearAlarms();
}

function selectTab(which) {
  const login = which === "login";
  loginForm.hidden = !login;
  signupForm.hidden = login;
  tabLogin.classList.toggle("active", login);
  tabSignup.classList.toggle("active", !login);
  authError.textContent = "";
}
tabLogin.addEventListener("click", () => selectTab("login"));
tabSignup.addEventListener("click", () => selectTab("signup"));

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  authError.textContent = "";
  try {
    const me = await api("/auth/login", "POST", {
      email: document.getElementById("loginEmail").value,
      password: document.getElementById("loginPassword").value,
    });
    document.getElementById("loginPassword").value = "";
    showApp(me);
  } catch (err) {
    authError.textContent = err.message;
  }
});

signupForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  authError.textContent = "";
  try {
    const me = await api("/auth/signup", "POST", {
      name: document.getElementById("signupName").value,
      email: document.getElementById("signupEmail").value,
      password: document.getElementById("signupPassword").value,
      household_name: document.getElementById("signupHousehold").value,
      invite_code: document.getElementById("signupInvite").value,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    });
    document.getElementById("signupPassword").value = "";
    showApp(me);
  } catch (err) {
    authError.textContent = err.message;
  }
});

document.getElementById("logoutBtn").addEventListener(
  "click",
  guard(async () => {
    await api("/auth/logout", "POST");
    signedIn = false;
    chatMessages.innerHTML = "";
    ["shoppingList", "taskList", "reminderList", "eventList", "budgetList", "pantryList"].forEach(
      (id) => (document.getElementById(id).innerHTML = "")
    );
    showAuth();
  })
);

function renderMe(me) {
  document.getElementById("userName").textContent = me.user.name;
  document.getElementById("householdName").textContent = me.household.name;
  document.getElementById("memberNames").textContent =
    me.members.length > 1 ? `· ${me.members.join(", ")}` : "";
  document.getElementById("inviteCode").textContent = me.household.invite_code;
  document.getElementById("tzLabel").textContent = me.household.timezone;
}

function showApp(me) {
  signedIn = true;
  renderMe(me);
  authView.hidden = true;
  appView.hidden = false;
  userBar.hidden = false;
  if (!started) {
    started = true;
    startTimers();
  }
  loadChatHistory();
  loadAll();
  checkAlerts();
}

document.getElementById("tzBtn").addEventListener(
  "click",
  guard(async () => {
    const current = document.getElementById("tzLabel").textContent;
    const tz = prompt(
      "Household timezone (for example Africa/Lagos, Europe/London, America/New_York):",
      current
    );
    if (!tz || tz.trim() === current) return;
    renderMe(await api("/household", "PUT", { timezone: tz.trim() }));
  })
);

// ---------- Chat ----------
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const chatMessages = document.getElementById("chatMessages");

function addMessage(role, text) {
  const p = document.createElement("p");
  p.className = role;
  p.textContent = text;
  chatMessages.appendChild(p);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

async function loadChatHistory() {
  try {
    const history = await api("/chat/history");
    chatMessages.innerHTML = "";
    history.forEach((m) => addMessage(m.role, m.content));
  } catch (e) {
    /* not critical */
  }
}

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;

  addMessage("user", message);
  chatInput.value = "";

  try {
    const data = await api("/chat", "POST", { message });
    addMessage("assistant", data.reply);
  } catch (err) {
    addMessage("assistant", err.message);
  }
  loadAll();
});

// ---------- Voice ----------
let femaleVoice = null;

function loadVoice() {
  const voices = window.speechSynthesis.getVoices();
  femaleVoice =
    voices.find((v) => /female|zira|aria|jenny|samantha/i.test(v.name)) ||
    voices.find((v) => v.lang.startsWith("en")) ||
    voices[0] ||
    null;
}

window.speechSynthesis.onvoiceschanged = loadVoice;
loadVoice();

function speak(text) {
  const utterance = new SpeechSynthesisUtterance(text);
  if (femaleVoice) {
    utterance.voice = femaleVoice;
  }
  window.speechSynthesis.speak(utterance);
}

// ---------- Exact-time alarms (while the page is open) ----------
const scheduledAlarms = new Map(); // key -> timeout id

function scheduleAlarm(key, label, target) {
  if (scheduledAlarms.has(key)) return;
  const msUntil = target - new Date();
  if (msUntil < 0 || msUntil > 24 * 60 * 60 * 1000) return;

  const id = setTimeout(() => {
    speak(`${label}, right now`);
    addMessage("assistant", `${label}, right now`);
  }, msUntil);
  scheduledAlarms.set(key, id);
}

function clearAlarms() {
  for (const id of scheduledAlarms.values()) clearTimeout(id);
  scheduledAlarms.clear();
}

// Cancel alarms whose item was deleted or edited
function syncAlarms(prefix, currentKeys) {
  for (const [key, id] of scheduledAlarms) {
    if (key.startsWith(prefix) && !currentKeys.has(key)) {
      clearTimeout(id);
      scheduledAlarms.delete(key);
    }
  }
}

function scheduleReminderAlarms(reminders) {
  const keys = new Set();
  reminders.forEach((r) => {
    const target = new Date(r.remind_at.replace(" ", "T"));
    if (isNaN(target)) return;
    const key = `reminder:${r.id}:${r.remind_at}`;
    keys.add(key);
    scheduleAlarm(key, `Reminder: ${r.text}`, target);
  });
  syncAlarms("reminder:", keys);
}

function scheduleEventAlarms(events) {
  const keys = new Set();
  events.forEach((ev) => {
    const target = new Date(ev.starts_at.replace(" ", "T"));
    if (isNaN(target)) return;
    const key = `event:${ev.id}:${ev.starts_at}`;
    keys.add(key);
    scheduleAlarm(key, `You have ${ev.title}`, target);
  });
  syncAlarms("event:", keys);
}

function scheduleTaskAlarms(tasks) {
  const keys = new Set();
  tasks.forEach((t) => {
    if (!t.due_at) return;
    const target = new Date(t.due_at.replace(" ", "T"));
    if (isNaN(target)) return;
    const key = `task:${t.id}:${t.due_at}`;
    keys.add(key);
    scheduleAlarm(key, `Task due: ${t.title}`, target);
  });
  syncAlarms("task:", keys);
}

// Re-check alarms in the background without redrawing the lists
async function refreshAlarms() {
  if (!signedIn) return;
  try {
    const [r, e, t] = await Promise.all([api("/reminders"), api("/events"), api("/tasks")]);
    scheduleReminderAlarms(r);
    scheduleEventAlarms(e);
    scheduleTaskAlarms(t);
  } catch (err) {
    /* server briefly unreachable; try again next round */
  }
}

// ---------- Add to phone calendar (.ics file) ----------
function pad(n) {
  return String(n).padStart(2, "0");
}

function toIcsLocal(date) {
  return (
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `T${pad(date.getHours())}${pad(date.getMinutes())}00`
  );
}

function escapeIcsText(text) {
  return String(text)
    .replace(/\\/g, "\\\\")
    .replace(/;/g, "\\;")
    .replace(/,/g, "\\,")
    .replace(/\r?\n/g, "\\n");
}

function addToCalendar(title, dateString) {
  const start = new Date(dateString.replace(" ", "T"));
  if (isNaN(start)) {
    alert("This item has no valid date and time, so it can't be added to a calendar.");
    return;
  }
  const end = new Date(start.getTime() + 15 * 60 * 1000);
  const stamp = new Date().toISOString().replace(/[-:]/g, "").split(".")[0] + "Z";
  const uid = `${Date.now()}-${Math.random().toString(36).slice(2)}@household-manager`;
  const safeTitle = escapeIcsText(title);

  const ics = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Household Manager//EN",
    "BEGIN:VEVENT",
    `UID:${uid}`,
    `DTSTAMP:${stamp}`,
    `DTSTART:${toIcsLocal(start)}`,
    `DTEND:${toIcsLocal(end)}`,
    `SUMMARY:${safeTitle}`,
    "BEGIN:VALARM",
    "ACTION:DISPLAY",
    `DESCRIPTION:${safeTitle}`,
    "TRIGGER:PT0S",
    "END:VALARM",
    "BEGIN:VALARM",
    "ACTION:DISPLAY",
    `DESCRIPTION:${safeTitle} in 1 hour`,
    "TRIGGER:-PT1H",
    "END:VALARM",
    "END:VEVENT",
    "END:VCALENDAR",
  ].join("\r\n");

  const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "household-reminder.ics";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------- Small UI helpers ----------
function makeButton(text, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = text;
  button.addEventListener("click", guard(onClick));
  return button;
}

function makeInput(type, value) {
  const input = document.createElement("input");
  input.type = type;
  input.value = value || "";
  return input;
}

// Builds one list row: label on the left, buttons on the right.
// editFields(li) returns {inputs: [...], save: async () => {...}} when Edit is clicked.
function buildRow(labelText, extraButtons, editFields, removeText, onRemove) {
  const li = document.createElement("li");
  const label = document.createElement("span");
  label.textContent = labelText;
  li.appendChild(label);
  const group = document.createElement("span");
  extraButtons.forEach((b) => group.appendChild(b));
  group.appendChild(
    makeButton("Edit", () => {
      const { inputs, save } = editFields();
      li.innerHTML = "";
      inputs.forEach((i) => li.appendChild(i));
      li.appendChild(makeButton("Save", save));
    })
  );
  group.appendChild(makeButton(removeText, onRemove));
  li.appendChild(group);
  return li;
}

function dtLocal(value) {
  return value ? value.replace(" ", "T") : "";
}
function dtServer(value) {
  return value ? value.replace("T", " ") : "";
}

// ---------- Shopping ----------
async function loadShopping() {
  const items = await api("/shopping");
  const list = document.getElementById("shoppingList");
  list.innerHTML = "";
  items.forEach((item) => {
    list.appendChild(
      buildRow(
        item.name,
        [],
        () => {
          const input = makeInput("text", item.name);
          return {
            inputs: [input],
            save: async () => {
              await api(`/shopping/${item.id}`, "PUT", { name: input.value });
              loadShopping();
            },
          };
        },
        "Got it",
        async () => {
          await api(`/shopping/${item.id}`, "DELETE");
          loadShopping();
        }
      )
    );
  });
}

// ---------- Tasks (title + optional due date/time) ----------
document.getElementById("taskForm").addEventListener(
  "submit",
  guard(async (e) => {
    e.preventDefault();
    const title = document.getElementById("taskTitle").value.trim();
    const due = document.getElementById("taskDue").value;
    if (!title) return;
    await api("/tasks", "POST", { title, due_at: dtServer(due) });
    document.getElementById("taskTitle").value = "";
    document.getElementById("taskDue").value = "";
    loadTasks();
  })
);

async function loadTasks() {
  const tasks = await api("/tasks");
  const list = document.getElementById("taskList");
  list.innerHTML = "";
  tasks.forEach((task) => {
    const extra = task.due_at
      ? [makeButton("Calendar", () => addToCalendar(`Task: ${task.title}`, task.due_at))]
      : [];
    list.appendChild(
      buildRow(
        task.due_at ? `${task.title} — due ${task.due_at}` : task.title,
        extra,
        () => {
          const titleInput = makeInput("text", task.title);
          const dueInput = makeInput("datetime-local", dtLocal(task.due_at));
          return {
            inputs: [titleInput, dueInput],
            save: async () => {
              await api(`/tasks/${task.id}`, "PUT", {
                title: titleInput.value,
                due_at: dtServer(dueInput.value),
              });
              loadTasks();
            },
          };
        },
        "Done",
        async () => {
          await api(`/tasks/${task.id}`, "DELETE");
          loadTasks();
        }
      )
    );
  });
  scheduleTaskAlarms(tasks);
}

// ---------- Reminders ----------
async function loadReminders() {
  const reminders = await api("/reminders");
  const list = document.getElementById("reminderList");
  list.innerHTML = "";
  reminders.forEach((r) => {
    list.appendChild(
      buildRow(
        `${r.text} — ${r.remind_at}`,
        [makeButton("Calendar", () => addToCalendar(r.text, r.remind_at))],
        () => {
          const textInput = makeInput("text", r.text);
          const dateInput = makeInput("text", r.remind_at);
          return {
            inputs: [textInput, dateInput],
            save: async () => {
              await api(`/reminders/${r.id}`, "PUT", {
                text: textInput.value,
                remind_at: dateInput.value,
              });
              loadReminders();
            },
          };
        },
        "Remove",
        async () => {
          await api(`/reminders/${r.id}`, "DELETE");
          loadReminders();
        }
      )
    );
  });
  scheduleReminderAlarms(reminders);
}

// ---------- Events ----------
async function loadEvents() {
  const events = await api("/events");
  const list = document.getElementById("eventList");
  list.innerHTML = "";
  events.forEach((ev) => {
    list.appendChild(
      buildRow(
        `${ev.title} — ${ev.starts_at}`,
        [makeButton("Calendar", () => addToCalendar(ev.title, ev.starts_at))],
        () => {
          const titleInput = makeInput("text", ev.title);
          const dateInput = makeInput("text", ev.starts_at);
          return {
            inputs: [titleInput, dateInput],
            save: async () => {
              await api(`/events/${ev.id}`, "PUT", {
                title: titleInput.value,
                starts_at: dateInput.value,
              });
              loadEvents();
            },
          };
        },
        "Remove",
        async () => {
          await api(`/events/${ev.id}`, "DELETE");
          loadEvents();
        }
      )
    );
  });
  scheduleEventAlarms(events);
}

// ---------- Pantry ----------
document.getElementById("pantryForm").addEventListener(
  "submit",
  guard(async (e) => {
    e.preventDefault();
    const name = document.getElementById("pantryName").value.trim();
    const quantity = document.getElementById("pantryQty").value.trim();
    if (!name) return;
    await api("/pantry", "POST", { name, quantity });
    document.getElementById("pantryName").value = "";
    document.getElementById("pantryQty").value = "";
    loadPantry();
  })
);

async function loadPantry() {
  const pantry = await api("/pantry");
  const list = document.getElementById("pantryList");
  list.innerHTML = "";
  pantry.forEach((p) => {
    list.appendChild(
      buildRow(
        `${p.name} — ${p.quantity}`,
        [],
        () => {
          const nameInput = makeInput("text", p.name);
          const qtyInput = makeInput("text", p.quantity);
          return {
            inputs: [nameInput, qtyInput],
            save: async () => {
              await api(`/pantry/${p.id}`, "PUT", {
                name: nameInput.value,
                quantity: qtyInput.value,
              });
              loadPantry();
            },
          };
        },
        "Used up",
        async () => {
          await api(`/pantry/${p.id}`, "DELETE");
          loadPantry();
        }
      )
    );
  });
}

// ---------- Budget ----------
async function loadBudget() {
  const budget = await api("/budget");
  const summary = document.getElementById("budgetSummary");
  const list = document.getElementById("budgetList");
  list.innerHTML = "";
  if (!budget.total) {
    summary.textContent = "No budget set yet — tell the manager your budget in chat to get started.";
    return;
  }
  const categories = budget.categories || {};
  const allocated = Object.values(categories).reduce((sum, val) => sum + val, 0);
  const unallocated = budget.total - allocated;
  summary.textContent = `₦${budget.total.toLocaleString()} per ${budget.period} — ₦${unallocated.toLocaleString()} unallocated`;
  Object.entries(categories).forEach(([category, amount]) => {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = `${category} — ₦${amount.toLocaleString()}`;
    li.appendChild(label);
    li.appendChild(
      makeButton("Edit", () => {
        const amountInput = makeInput("number", amount);
        li.innerHTML = "";
        li.appendChild(amountInput);
        li.appendChild(
          makeButton("Save", async () => {
            await api("/budget/category", "PUT", { category, amount: Number(amountInput.value) });
            loadBudget();
          })
        );
      })
    );
    list.appendChild(li);
  });
}

function loadAll() {
  [loadShopping, loadTasks, loadReminders, loadEvents, loadPantry, loadBudget].forEach((fn) =>
    fn().catch(() => {})
  );
}

// ---------- Day-before / hour-before spoken alerts (from the server) ----------
async function checkAlerts() {
  if (!signedIn) return;
  try {
    const data = await api("/alerts");
    data.alerts.forEach((alert) => {
      speak(alert.message);
      addMessage("assistant", alert.message);
    });
  } catch (e) {
    /* try again next minute */
  }
}

// ---------- Start everything ----------
function startTimers() {
  setInterval(checkAlerts, 60000);
  setInterval(refreshAlarms, 5 * 60 * 1000);
}

(async function init() {
  try {
    showApp(await api("/me"));
  } catch (e) {
    showAuth();
  }
})();
