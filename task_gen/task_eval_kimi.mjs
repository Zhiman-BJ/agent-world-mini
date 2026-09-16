// Thin bridge to the official SDK. Environment state is accessible only via MCP.
import { readFileSync, writeFileSync, appendFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';

const input = JSON.parse(readFileSync(0, 'utf8'));
const serialize = (value) => {
  const text = JSON.stringify(value);
  return input.api_key ? text.replaceAll(input.api_key, '[REDACTED]') : text;
};
const save = (name, value) => writeFileSync(join(input.log_dir, name), serialize(value));
const append = (name, value) => appendFileSync(join(input.log_dir, name), serialize(value) + '\n');

let harness;
let session;
let outcome = {};
try {
  const { createKimiHarness } = await import(pathToFileURL(input.sdk_path).href);
  // Save the actual model request bodies without credentials/HTTP headers.
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (resource, options) => {
    const url = typeof resource === 'string' ? resource : resource instanceof URL ? resource.href : resource.url;
    if (url.startsWith(input.base_url.replace(/\/$/, '') + '/') && typeof options?.body === 'string') {
      const request = JSON.parse(options.body);
      // Fail closed if an SDK upgrade silently changes profile/tool loading.
      const names = (request.tools ?? []).map((tool) => tool.function?.name);
      if (names.length !== input.tool_count || names.some((name) => !name?.startsWith('mcp__agent_world_eval__'))) {
        throw new Error('Kimi tool allowlist mismatch; refusing model request');
      }
      append('llm_requests.jsonl', { time: new Date().toISOString(), step: 'task_eval.agent', request });
    }
    return originalFetch(resource, options);
  };
  harness = createKimiHarness({
    homeDir: input.home_dir, uiCapabilities: [],
    identity: { productName: 'agent-world-eval', version: '0.1.0', platform: 'agent_world_eval' },
  });
  await harness.setConfig({
    providers: { evaluation: { type: input.provider_type, baseUrl: input.base_url, apiKey: input.api_key } },
    models: { evaluation: { provider: 'evaluation', model: input.model,
      maxContextSize: input.max_context_size, maxOutputSize: input.max_output_size } },
    defaultModel: 'evaluation', loopControl: input.loop_control,
    modelOverrides: { temperature: input.temperature, maxCompletionTokens: input.max_output_size },
    telemetry: false,
    // SDK v0.20's createSession currently ignores agentProfile/agentFiles.
    extraAgentDirs: [dirname(input.profile)],
    tools: { enabled: ['mcp__agent_world_eval__*'], disabled: ['select_tools'] },
  });
  // Register before session creation: the engine snapshots its MCP baseline.
  // The harness home is private and temporary, never the user's Kimi home.
  await harness.addMcpServer(input.mcp);
  session = await harness.createSession({
    workDir: input.work_dir, model: 'evaluation', permission: 'yolo',
  });
  await session.getMcpStartupMetrics();
  const connected = (await session.listMcpServers()).find((server) => server.name === input.mcp.name);
  if (connected?.status !== 'connected') throw new Error(`MCP startup failed: ${connected?.error ?? connected?.status}`);
  let resolveDone;
  const done = new Promise((resolve) => { resolveDone = resolve; });
  const errors = [];
  session.onEvent((event) => {
    append('events.jsonl', event);
    if (event.agentId !== 'main') return;
    if (event.type === 'error') errors.push(event.message);
    if (event.type === 'turn.ended') resolveDone(event);
  });
  await session.prompt(input.prompt);
  const ended = await done;
  const context = await session.getContext();
  save('context.json', context);
  const last = context.history.at(-1);
  const answer = last?.role === 'assistant' && !last.toolCalls?.length
    ? last.content.filter((part) => part.type === 'text').map((part) => part.text).join('') : '';
  outcome = { session_id: session.id, reason: ended.reason, answer, usage: await session.getUsage(),
    error: ended.reason === 'completed' ? undefined : errors.join('\n') || ended.reason };
  if (ended.reason !== 'completed' || !answer.trim()) process.exitCode = 1;
} catch (error) {
  outcome = { ...outcome, error: String(error), reason: 'failed' };
  process.exitCode = 1;
} finally {
  save('result.json', outcome);
  await harness?.close();
}
