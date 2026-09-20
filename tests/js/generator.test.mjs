/**
 * Prompt construction for the on-device answer model.
 *
 * The rest of generator.js needs WebGPU or a several-hundred-megabyte GGUF, so it is not
 * covered here. This part is pure, and it is the part that decides whether an answer is
 * grounded in the documentation or invented, which makes it worth pinning down.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { buildMessages } from "../../src/mkdocs_ask/assets/generator.js";

const CONTEXTS = [
  {
    url: "guide/retention/#when-the-disk-fills-up",
    heading: "When the disk fills up",
    breadcrumbs: ["Retention and compaction", "When the disk fills up"],
    text: "Kagura refuses writes at storage.min_free_bytes.",
  },
  {
    url: "troubleshooting/#running-out-of-disk-space",
    heading: "Running out of disk space",
    breadcrumbs: ["Troubleshooting", "Running out of disk space"],
    text: "Compact first, then shorten retention.",
  },
];

const manifest = (ai_answer = {}) => ({ language: "en", ai_answer: { ...ai_answer } });

test("the default instructions demand grounding, admission of ignorance, and citations", () => {
  const [system] = buildMessages(manifest(), "why is the disk full", CONTEXTS);
  assert.equal(system.role, "system");
  assert.match(system.content, /ONLY the provided documents/);
  assert.match(system.content, /do not contain the answer/);
  assert.match(system.content, /\[1\]/, "citations are what make an answer checkable");
  assert.match(system.content, /same language as the question/);
});

test("documents are numbered from one and carry their breadcrumbs", () => {
  const [, user] = buildMessages(manifest(), "why is the disk full", CONTEXTS);
  assert.equal(user.role, "user");
  assert.match(user.content, /\[1\] Retention and compaction > When the disk fills up/);
  assert.match(user.content, /\[2\] Troubleshooting > Running out of disk space/);
  assert.match(user.content, /storage\.min_free_bytes/);
  // The numbering has to line up with the citation links the UI builds from the same order.
  assert.ok(user.content.indexOf("[1]") < user.content.indexOf("[2]"));
});

test("the question is appended after the documents, not mixed into them", () => {
  const [, user] = buildMessages(manifest(), "why is the disk full", CONTEXTS);
  assert.ok(user.content.startsWith("Documents:"));
  assert.ok(user.content.trimEnd().endsWith("Question: why is the disk full"));
});

test("a site can replace the instructions entirely", () => {
  const custom = "Answer in bullet points, citing sources as [n].";
  const [system] = buildMessages(manifest({ system_prompt: custom }), "q", CONTEXTS);
  assert.equal(system.content, custom);
});

test("an empty context set still produces a well-formed exchange", () => {
  const messages = buildMessages(manifest(), "q", []);
  assert.equal(messages.length, 2);
  assert.match(messages[1].content, /^Documents:\n\n\nQuestion: q$/);
});
