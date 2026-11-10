/* One-line RCB site embed: <script src="https://CHATBOT-HOST/widget.js" defer></script> */
(() => {
  if (window.__rcbAssistantLoaded) return;
  window.__rcbAssistantLoaded = true;

  const script = document.currentScript;
  const appUrl = new URL("/", script.src);
  appUrl.searchParams.set("embed", "1");

  const frame = document.createElement("iframe");
  frame.title = "RCB Website Assistant";
  frame.src = appUrl.href;
  frame.loading = "lazy";
  frame.setAttribute("allow", "clipboard-write");
  Object.assign(frame.style, {
    position: "fixed",
    right: "0",
    bottom: "0",
    width: "88px",
    height: "88px",
    border: "0",
    background: "transparent",
    zIndex: "2147483000",
    colorScheme: "light",
  });

  window.addEventListener("message", (event) => {
    if (event.source !== frame.contentWindow || event.data?.type !== "rcb-chat-widget-state") return;
    if (event.data.open) {
      frame.style.width = "min(564px, 100vw)";
      frame.style.height = "min(772px, 100dvh)";
    } else {
      frame.style.width = "88px";
      frame.style.height = "88px";
    }
  });

  (document.body || document.documentElement).append(frame);
})();
