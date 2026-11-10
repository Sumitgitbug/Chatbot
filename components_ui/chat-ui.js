/* UI behavior: collect questions, call the chat API, and show answers/sources. */
const form = document.getElementById("chatForm");
const input = document.getElementById("question");
const messages = document.getElementById("messages");
const scrollArea = document.getElementById("scrollArea");
const sendButton = document.getElementById("send");
const welcome = document.getElementById("welcome");
const widget = document.getElementById("widget");
const chatPanel = document.getElementById("chatPanel");
const launcher = document.getElementById("launcher");
const conversationHistory = [];
if (new URLSearchParams(window.location.search).has("embed")) document.body.classList.add("embedded");

document.querySelectorAll(".logo-crop img").forEach((logo) => {
  const badge = logo.closest(".logo-window");
  const useLogo = () => badge.classList.add("has-logo");
  logo.addEventListener("load", useLogo, { once: true });
  logo.addEventListener("error", () => badge.classList.remove("has-logo"), { once: true });
  if (logo.complete && logo.naturalWidth) useLogo();
});

function setWidgetOpen(open) {
  widget.classList.toggle("open", open);
  launcher.setAttribute("aria-expanded", String(open));
  launcher.setAttribute("aria-label", open ? "Close RCB Assistant" : "Open RCB Assistant");
  chatPanel.setAttribute("aria-hidden", String(!open));
  chatPanel.toggleAttribute("inert", !open);
  window.parent.postMessage({ type: "rcb-chat-widget-state", open }, "*");
  if (open) input.focus();
}

launcher.addEventListener("click", () => setWidgetOpen(!widget.classList.contains("open")));
document.getElementById("minimize").addEventListener("click", () => setWidgetOpen(false));
document.getElementById("newChat").addEventListener("click", () => {
  conversationHistory.length = 0;
  messages.replaceChildren();
  welcome.hidden = false;
  scrollArea.scrollTop = 0;
  input.value = "";
  input.style.height = "auto";
  input.focus();
});

function scrollToLatestMessage() {
  scrollArea.scrollTop = scrollArea.scrollHeight;
}

/* Add the visitor's message as plain text, without interpreting HTML input. */
function addUserMessage(text) {
  const message = document.createElement("div");
  message.className = "message user";
  message.textContent = text;
  messages.append(message);
  welcome.hidden = true;
  scrollToLatestMessage();
}

/* Render HTTPS Markdown links as safe clickable elements, preserving plain text. */
function renderAnswerText(container, text) {
  const linkPattern = /\[([^\]]+)\]\((https:\/\/[^\s)]+)\)/g;
  let lastIndex = 0;
  let match;

  while ((match = linkPattern.exec(text)) !== null) {
    container.append(document.createTextNode(text.slice(lastIndex, match.index)));

    const link = document.createElement("a");
    link.href = match[2];
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = match[1];
    container.append(link);
    lastIndex = linkPattern.lastIndex;
  }

  container.append(document.createTextNode(text.slice(lastIndex)));
}

/* Render the answer and any source links returned by the server. */
function addAssistantMessage(answer, sources = [], isError = false) {
  const message = document.createElement("div");
  message.className = "message assistant";

  const label = document.createElement("div");
  label.className = "answer-label";
  label.textContent = "RCB Assistant";

  const body = document.createElement("div");
  body.className = "answer-text" + (isError ? " error" : "");
  renderAnswerText(body, answer);
  message.append(label, body);

  if (sources.length) {
    const sourceList = document.createElement("div");
    sourceList.className = "sources";
    for (const source of sources) {
      const link = document.createElement("a");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = source.title || "RCB source";
      sourceList.append(link);
    }
    message.append(sourceList);
  }

  messages.append(message);
  scrollToLatestMessage();
}

/* Send one question to the Python API and show a short loading indicator. */
async function askQuestion(text) {
  addUserMessage(text);
  sendButton.disabled = true;

  const loading = document.createElement("div");
  loading.className = "typing";
  loading.textContent = "Looking through RCB website information…";
  messages.append(loading);
  scrollToLatestMessage();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text, history: conversationHistory.slice(-8) }),
    });
    const result = await response.json();
    loading.remove();
    conversationHistory.push({ role: "user", content: text });
    if (result.answer) conversationHistory.push({ role: "assistant", content: result.answer });
    if (conversationHistory.length > 8) conversationHistory.splice(0, conversationHistory.length - 8);

    if (!response.ok || result.error) {
      addAssistantMessage(result.error || "Sorry, something went wrong. Please try again.", result.sources, true);
    } else {
      addAssistantMessage(result.answer, result.sources);
    }
  } catch (error) {
    loading.remove();
    addAssistantMessage("Could not reach the chatbot server. Please try again in a moment.", [], true);
  } finally {
    sendButton.disabled = false;
    input.focus();
  }
}

/* Submit by button or Enter; Shift+Enter inserts a new line. */
form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || sendButton.disabled) return;
  input.value = "";
  input.style.height = "auto";
  askQuestion(text);
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 130) + "px";
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

/* Example prompts ask the same API as a manually typed question. */
document.querySelectorAll(".suggestion").forEach((button) => {
  button.addEventListener("click", () => askQuestion(button.textContent.trim()));
});

setWidgetOpen(false);
