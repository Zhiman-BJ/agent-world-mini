// Thin bridge to the official SDK. Environment state is accessible only via MCP.
import { readFileSync, writeFileSync, appendFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { responseEvents } from './responses_events.mjs';

const input = JSON.parse(readFileSync(0, 'utf8'));
const serialize = (value) => {
  const text = JSON.stringify(value);
  return input.api_key ? text.replaceAll(input.api_key, '[REDACTED]') : text;
};
const save = (name, value) => writeFileSync(join(input.log_dir, name), serialize(value));
const append = (name, value) => appendFileSync(join(input.log_dir, name), serialize(value) + '\n');

async function recordResponse(response, request_id, started) {
  const decoder = new TextDecoder();
  let body = '';
  let error;
  try {
    for await (const chunk of response.body ?? []) body += decoder.decode(chunk, { stream: true });
  } catch (caught) {
    error = String(caught);
  }
  body += decoder.decode();
  append('llm_responses.jsonl', { request_id, status: response.status,
    duration_ms: Date.now() - started, body, error });
}

let harness;
let session;
let outcome = {};
let interrupted = false;
let requestNumber = 0;
const responseLogs = [];
process.on('SIGTERM', () => {
  interrupted = true;
  if (session) void session.cancel().catch((error) => append('errors.jsonl', { error: String(error) }));
});
try {
  const { createKimiHarness } = await import(pathToFileURL(input.sdk_path).href);
  // Save the actual model request bodies without credentials/HTTP headers.
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (resource, options) => {
    const url = typeof resource === 'string' ? resource : resource instanceof URL ? resource.href : resource.url;
    if (url.startsWith(input.base_url.replace(/\/$/, '') + '/') && typeof options?.body === 'string') {
      const request = JSON.parse(options.body);
      // Fail closed if an SDK upgrade silently changes profile/tool loading.
      const names = (request.tools ?? []).map((tool) => tool.type === 'function'
        ? (input.provider_type === 'openai_responses' ? tool.name : tool.function?.name) : undefined);
      // Compaction requests may intentionally omit tools. Any offered tool must
      // still be from our MCP server; a nonempty task tool table must be complete.
      if ((names.length > 0 && names.length !== input.tool_count) || names.some((name) => !name?.startsWith('mcp__agent_world_eval__'))) {
        throw new Error('Kimi tool allowlist mismatch; refusing model request');
      }
      request.temperature = input.temperature;
      const nonstream = input.provider_type === 'openai_responses' && input.responses_stream === false;
      if (nonstream) request.stream = false;
      options = { ...options, body: JSON.stringify(request) };
      const request_id = ++requestNumber;
      const started = Date.now();
      append('llm_requests.jsonl', { request_id, time: new Date().toISOString(), step: 'task_eval.agent', request });
      const messages = request.messages ?? (Array.isArray(request.input) ? request.input : []);
      append('system_prompts.jsonl', { request_id, instructions: request.instructions,
        messages: messages.filter((m) => m.role === 'system' || m.role === 'developer') });
      try {
        const response = await originalFetch(resource, options);
        // Clone preserves streaming to the SDK; retain the raw SSE/JSON answer too.
        responseLogs.push(recordResponse(response.clone(), request_id, started));
        if (nonstream && response.ok) {
          const events = responseEvents(await response.json());
          append('adapted_response_events.jsonl', { request_id, source: 'local_nonstream_adapter', events });
          const body = events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join('');
          return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
        }
        return response;
      } catch (error) {
        append('llm_responses.jsonl', { request_id, duration_ms: Date.now() - started, error: String(error) });
        throw error;
      }
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
    telemetry: false,
    // SDK v0.20's createSession currently ignores agentProfile/agentFiles.
    extraAgentDirs: [dirname(input.profile)],
    tools: { enabled: ['mcp__agent_world_eval__*'], disabled: ['select_tools'] },
  });
  save('effective_config.json', { ...await harness.getConfig(),
    adapter: { temperature: input.temperature, tool_count: input.tool_count } });
  // Register before session creation: the engine snapshots its MCP baseline.
  // The harness home is private and temporary, never the user's Kimi home.
  await harness.addMcpServer(input.mcp);
  session = await harness.createSession({
    workDir: input.work_dir, model: 'evaluation', permission: 'yolo',
  });
  await session.getMcpStartupMetrics();
  const connected = (await session.listMcpServers()).find((server) => server.name === input.mcp.name);
  if (connected?.status !== 'connected') throw new Error(`MCP startup failed: ${connected?.error ?? connected?.status}`);
  if (connected.toolCount !== input.tool_count) throw new Error('MCP tool count mismatch');
  let resolveDone;
  const done = new Promise((resolve) => { resolveDone = resolve; });
  const errors = [];
  session.onEvent((event) => {
    append('events.jsonl', event);
    if (event.agentId !== 'main') return;
    if (event.type === 'error') errors.push(event.message);
    if (event.type === 'turn.ended') resolveDone(event);
  });
  if (interrupted) throw new Error('Cancelled before model request');
  await session.prompt(input.prompt);
  const ended = await done;
  const context = await session.getContext();
  save('context.json', context);
  const finalMessages = [];
  for (let index = context.history.length - 1; index >= 0; index -= 1) {
    const message = context.history[index];
    if (message.role !== 'assistant' || message.toolCalls?.length) break;
    finalMessages.unshift(message);
  }
  const answer = finalMessages.flatMap((message) => message.content)
    .filter((part) => part.type === 'text').map((part) => part.text).join('');
  outcome = { session_id: session.id, reason: ended.reason, answer, usage: await session.getUsage(),
    error: ended.reason === 'completed' ? undefined : errors.join('\n') || ended.reason };
  if (ended.reason === 'completed' && !answer.trim()) {
    outcome.error = 'Empty final answer';
    outcome.error_kind = 'empty_answer';
  }
  if (ended.reason !== 'completed' || !answer.trim()) process.exitCode = 1;
} catch (error) {
  outcome = { ...outcome, error: String(error), reason: 'failed' };
  process.exitCode = 1;
} finally {
  if (session) {
    try {
      save('context.json', await session.getContext());
      outcome.usage = await session.getUsage();
    } catch (error) {
      append('errors.jsonl', { error: String(error) });
    }
  }
  if (interrupted) {
    outcome.reason = 'cancelled';
    outcome.error = 'Execution timeout or SIGTERM';
    process.exitCode = 1;
  }
  await Promise.allSettled(responseLogs);
  save('result.json', outcome);
  await harness?.close();
}
