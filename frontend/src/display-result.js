// Discriminated UI result contract. Missing/unknown kinds stay on legacy Markdown.
const KINDS = new Set(["train_schedule", "train_schedule_batch", "emu_routing", "empty", "error"]);
export const DISPLAY_SCHEMA_VERSION = 1;

export function normalizeDisplayResults(value) {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => item && typeof item === "object"
    && KINDS.has(item.kind) && typeof item.status === "string"
    && (item.schema_version == null || item.schema_version === DISPLAY_SCHEMA_VERSION));
}
