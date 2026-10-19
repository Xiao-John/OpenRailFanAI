// Boolean settings use sliders, with native button keyboard behavior.
export function switchControl(checked, label, disabled = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.setAttribute("role", "switch");
  button.setAttribute("aria-label", label);
  button.disabled = disabled;
  Object.assign(button.style, {width: "52px", minWidth: "52px", height: "44px", minHeight: "44px",
    flex: "0 0 52px", padding: "0", border: "0", background: "transparent",
    display: "inline-flex", alignItems: "center", justifyContent: "center", touchAction: "pan-y",
    cursor: disabled ? "default" : "pointer", opacity: disabled ? "0.5" : "1"});
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("width", "52"); svg.setAttribute("height", "30");
  svg.setAttribute("viewBox", "0 0 52 30"); svg.setAttribute("aria-hidden", "true");
  const track = document.createElementNS(ns, "rect");
  for (const [k,v] of Object.entries({width: "52", height: "30", rx: "15"})) track.setAttribute(k,v);
  const thumb = document.createElementNS(ns, "circle");
  for (const [k,v] of Object.entries({cy: "15", r: "11", fill: "white"})) thumb.setAttribute(k,v);
  thumb.style.transition = "cx 160ms ease";
  svg.append(track, thumb); button.appendChild(svg);
  let value = !!checked, start = null, dragged = false;
  function draw() {
    button.setAttribute("aria-checked", String(value));
    track.setAttribute("fill", value ? "var(--blue, #2563eb)" : "var(--muted, #64748b)");
    thumb.setAttribute("cx", value ? "37" : "15");
  }
  Object.defineProperty(button, "checked", {get: () => value, set: next => {value = !!next; draw();}});
  function change(next) {
    if (button.disabled || next === value) return;
    button.checked = next;
    button.dispatchEvent(new Event("change", {bubbles: true}));
  }
  button.addEventListener("click", () => {
    if (dragged) {dragged = false; return;}
    change(!value);
  });
  button.addEventListener("pointerdown", event => {
    if (button.disabled || event.button !== 0) return;
    start = event.clientX; dragged = false;
    button.setPointerCapture(event.pointerId);
  });
  button.addEventListener("pointerup", event => {
    if (start != null && Math.abs(event.clientX-start) >= 6) {
      dragged = true; change(event.clientX > start);
    }
    start = null;
  });
  button.addEventListener("pointercancel", () => {start = null; dragged = false;});
  draw();
  return button;
}
