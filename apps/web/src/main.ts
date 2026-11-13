/** Demo host page: stands in for someone else's app that embeds the widget. */
import * as UniversalName from "./embed";

declare global {
  interface Window {
    UniversalName: typeof UniversalName;
  }
}
window.UniversalName = UniversalName;

// ?name=peni pre-fills the pay card; ?start=names opens on the owner's cards.
const query = new URLSearchParams(location.search);
const name = query.get("name") ?? undefined;
const start = query.get("start") === "names" ? "names" : "pay";
document.querySelector("#open-pay")?.addEventListener("click", () => UniversalName.open({ name }));
document.querySelector("#open-names")?.addEventListener("click", () => UniversalName.open({ start: "names" }));
UniversalName.open({ name, start });
