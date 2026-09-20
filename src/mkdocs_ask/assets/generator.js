/**
 * mkdocs-ask: Phase 2 answer generation with an on-device small language model.
 *
 * Two backends, both loaded lazily and only after the user asks for an AI answer:
 *   - WebGPU: WebLLM (@mlc-ai/web-llm) with a prebuilt MLC model id.
 *   - WASM:   wllama (@wllama/wllama) with a GGUF file (`ai_answer.wasm_model_url`).
 *
 * EXPERIMENTAL: the retrieval half of the plugin does not depend on this module.
 */

import { resolveRuntimeUrl } from "./urls.js";

const DEFAULT_SYSTEM = [
  "You are the assistant for this documentation site.",
  "Answer using ONLY the provided documents.",
  "If the documents do not contain the answer, say so plainly.",
  "Cite every document you used by its number, like [1].",
  "Reply in the same language as the question.",
].join(" ");

export async function createGenerator(manifest, onStatus = () => {}, baseUrl = self.location.href) {
  const cfg = manifest.ai_answer;
  const rt = manifest.runtime;
  const hasWebGPU = typeof navigator !== "undefined" && !!navigator.gpu;
  const useWebGPU = cfg.backend === "webgpu" || (cfg.backend === "auto" && hasWebGPU);
  const backend = useWebGPU
    ? await createWebLLM(cfg, rt, onStatus, baseUrl)
    : await createWllama(cfg, rt, onStatus, baseUrl);

  return {
    backend: backend.name,
    async *generate(query, contexts) {
      const messages = buildMessages(manifest, query, contexts);
      yield* backend.stream(messages);
    },
  };
}

export function buildMessages(manifest, query, contexts) {
  const system = manifest.ai_answer.system_prompt || DEFAULT_SYSTEM;
  const docs = contexts
    .map((c, i) => `[${i + 1}] ${c.breadcrumbs.join(" > ")}\n${c.text}`)
    .join("\n\n");
  return [
    { role: "system", content: system },
    { role: "user", content: `Documents:\n${docs}\n\nQuestion: ${query}` },
  ];
}

async function createWebLLM(cfg, rt, onStatus, baseUrl) {
  onStatus("Loading WebLLM...", 0);
  const webllm = await import(/* @vite-ignore */ resolveRuntimeUrl(rt.webllm_url, baseUrl));
  const engine = await webllm.CreateMLCEngine(cfg.model, {
    initProgressCallback: (p) => onStatus(p.text, p.progress),
  });
  onStatus(`Model ready (WebGPU: ${cfg.model})`, 1);
  return {
    name: "webllm",
    async *stream(messages) {
      const chunks = await engine.chat.completions.create({
        messages,
        stream: true,
        temperature: cfg.temperature,
        max_tokens: cfg.max_tokens,
      });
      for await (const chunk of chunks) {
        const delta = chunk.choices?.[0]?.delta?.content;
        if (delta) yield delta;
      }
    },
  };
}

async function createWllama(cfg, rt, onStatus, baseUrl) {
  if (!cfg.wasm_model_url) {
    throw new Error(
      "WebGPU is not available and ai_answer.wasm_model_url (a GGUF file) is not configured."
    );
  }
  onStatus("Loading wllama...", 0);
  const wllamaUrl = resolveRuntimeUrl(rt.wllama_url, baseUrl);
  const mod = await import(/* @vite-ignore */ wllamaUrl);
  const base = wllamaUrl.replace(/index\.js$/, "");
  const wllama = new mod.Wllama({
    "single-thread/wllama.wasm": `${base}single-thread/wllama.wasm`,
    "multi-thread/wllama.wasm": `${base}multi-thread/wllama.wasm`,
  });
  await wllama.loadModelFromUrl(cfg.wasm_model_url, {
    progressCallback: ({ loaded, total }) =>
      onStatus(`Downloading model (${Math.round((100 * loaded) / total)}%)`, loaded / total),
  });
  onStatus("Model ready (WASM)", 1);
  return {
    name: "wllama",
    async *stream(messages) {
      // wllama reports progress through a callback; this turns it into an async iterator.
      // A failure is kept and rethrown once the queue is drained, rather than lost.
      const queue = [];
      let done = false;
      let failure = null;
      let notify = null;
      const wait = () => new Promise((r) => (notify = r));
      wllama
        .createChatCompletion(messages, {
          nPredict: cfg.max_tokens,
          sampling: { temp: cfg.temperature },
          onNewToken: (_token, _piece, text) => {
            queue.push(text);
            notify?.();
          },
        })
        .catch((err) => {
          failure = err;
        })
        .finally(() => {
          done = true;
          notify?.();
        });
      let emitted = "";
      while (!done || queue.length) {
        if (!queue.length) await wait();
        while (queue.length) {
          const full = queue.shift();
          if (full.length > emitted.length) {
            yield full.slice(emitted.length);
            emitted = full;
          }
        }
      }
      if (failure) throw failure;
    },
  };
}
