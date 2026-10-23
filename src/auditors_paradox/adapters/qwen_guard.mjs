// Check the actual request before Qwen Code can send it, and keep credentials out of evidence.
import fs from 'node:fs';
import { AsyncLocalStorage } from 'node:async_hooks';
import net from 'node:net';
import { isDeepStrictEqual } from 'node:util';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';

const contract = JSON.parse(fs.readFileSync(process.env.AP_QWEN_CONTRACT, 'utf8'));
const output = process.env.AP_QWEN_RESULT;
const token = process.env.OPENAI_API_KEY;
const scope = new AsyncLocalStorage();
const state = { status: 'starting', network_requests: 0, input_tokens: null, output_tokens: null };
const nativeFetch = globalThis.fetch;
const nativeConnect = net.Socket.prototype.connect;

// Make the pinned client's startup-context setting cover its Git and date reminders.
if (process.env.AP_QWEN_CORE) {
  registerHooks({ load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    if (!url.startsWith('file:') || fileURLToPath(url).toLowerCase() !== fileURLToPath(process.env.AP_QWEN_CORE).toLowerCase()) return result;
    let source = String(result.source);
    const replacements = [
      ['getCachedGitStatus(){if(this.config.getExecutionEnvironment?.()',
       'getCachedGitStatus(){if(this.config.getSkipStartupContext())return null;if(this.config.getExecutionEnvironment?.()'],
      ['if(messageType==="userQuery"){const today=formatDateForContext();',
       'if(messageType==="userQuery"&&!this.config.getSkipStartupContext()){const today=formatDateForContext();'],
    ];
    for (const [before, after] of replacements) {
      if (source.split(before).length !== 2) block('context_patch_version_mismatch');
      source = source.replace(before, after);
    }
    state.context_compatibility = 'skip-startup-context-v1';
    save();
    return { ...result, source };
  }});
}

function save() {
  const text = JSON.stringify(state);
  fs.writeFileSync(output + '.tmp', token ? text.split(token).join('[REDACTED]') : text);
  fs.renameSync(output + '.tmp', output);
}

function block(kind, details = {}) {
  Object.assign(state, { status: 'blocked', error_kind: kind, ...details });
  save();
  process.exit(86);
}

function durationSeconds(value) {
  if (!value) return 0;
  if (/^\d+(\.\d+)?$/.test(value)) return Number(value);
  const parts = [...value.matchAll(/(\d+(?:\.\d+)?)(ms|s|m|h)/g)];
  if (!parts.length || parts.map(p => p[0]).join('') !== value) return 0;
  return parts.reduce((n, p) => n + Number(p[1]) * ({ms: .001, s: 1, m: 60, h: 3600}[p[2]]), 0);
}

function recordHeaders(response) {
  const allowed = ['retry-after', 'retry-after-ms', 'x-ratelimit-limit-requests',
    'x-ratelimit-limit-tokens', 'x-ratelimit-remaining-requests', 'x-ratelimit-remaining-tokens',
    'x-ratelimit-reset-requests', 'x-ratelimit-reset-tokens', 'x-ratelimit-remaining-project-tokens',
    'x-ratelimit-reset-project-tokens'];
  const headers = Object.fromEntries(allowed.filter(k => response.headers.has(k))
    .map(k => [k, response.headers.get(k).slice(0, 120)]));
  const retry = headers['retry-after'];
  const hint = durationSeconds(retry) || (retry && !/^\d/.test(retry)
    ? Math.max(0, (Date.parse(retry) - Date.now()) / 1000) : 0);
  const delays = [hint, Number(headers['retry-after-ms'] ?? 0) / 1000];
  for (const kind of ['requests', 'tokens', 'project-tokens']) {
    if (headers[`x-ratelimit-remaining-${kind}`] === '0') {
      delays.push(durationSeconds(headers[`x-ratelimit-reset-${kind}`]));
    }
  }
  Object.assign(state, { http_status: response.status, headers_received_at_ms: Date.now(),
    rate_limit_headers: headers, retry_after_seconds: Math.max(0, ...delays.filter(v => Number.isFinite(v) && v >= 0)) });
  save();
}

// Only a checked inference request may open a network connection in this process.
net.Socket.prototype.connect = function (...args) {
  const options = Array.isArray(args[0]) ? args[0][0] : args[0];
  if (!scope.getStore()) block('unexpected_network_connection', { rejected_host: options?.host ?? options?.hostname ?? 'unknown', rejected_port: options?.port ?? null, ipc: Boolean(options?.path) });
  return nativeConnect.apply(this, args);
};

function authorize(request, rawBody) {
  if (process.env.AP_QWEN_CORE && state.context_compatibility !== 'skip-startup-context-v1') {
    block('context_patch_not_applied');
  }
  if (request.url !== contract.endpoint || request.method !== 'POST') {
    const url = new URL(request.url);
    block('unexpected_endpoint', { rejected_endpoint: url.origin + url.pathname });
  }
  if (state.network_requests !== 0) block('internal_request_blocked');
  let body;
  try { body = JSON.parse(rawBody); }
  catch { block('invalid_request_body'); }
  if (body.model !== contract.model) block('unexpected_model');
  if (['previous_response_id', 'conversation', 'conversation_id', 'thread_id', 'session_id', 'prompt']
      .some(key => body[key] != null)) block('unexpected_remote_context');
  if (contract.reasoning_effort && body.reasoning?.effort !== contract.reasoning_effort) block('unexpected_reasoning_effort');
  if (body.tools?.length || body.functions?.length || body.tool_choice || body.function_call) {
    block('unexpected_tools', { tool_names: body.tools?.map(t => t.function?.name), tool_choice: body.tool_choice ?? null });
  }
  const responses = contract.protocol === 'responses';
  if (body[responses ? 'max_output_tokens' : contract.token_parameter] !== contract.max_tokens ||
      ('max_tokens' in body && 'max_completion_tokens' in body)) block('unexpected_output_limit');
  const sourceMessages = responses ? [
    ...(body.instructions ? [{role:'system',content:body.instructions}] : []),
    ...(Array.isArray(body.input) ? body.input : [])] : body.messages;
  const messages = sourceMessages?.map(message => ({...Object.fromEntries(Object.entries(message).filter(([key,value])=> !(responses && key==='type' && value==='message'))),
    content: Array.isArray(message.content) && message.content.length === 1 &&
      ['text','input_text','output_text'].includes(message.content[0].type) ? message.content[0].text : message.content }));
  if (!isDeepStrictEqual(messages, contract.messages)) {
    block('prompt_contract_mismatch', { actual_messages: sourceMessages, actual_request: body });
  }
  if (contract.temperature == null ? body.temperature != null : body.temperature !== contract.temperature) {
    block('unexpected_temperature');
  }
  Object.assign(state, { status: 'dispatched', network_requests: 1, wire_request: body, dispatched_at_ms: Date.now() });
  save();
}

async function examine(response) {
  try {
    recordHeaders(response);
    if (response.status >= 300 && response.status < 400) block('redirect_blocked');
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    let lastProgressSave = 0;
    const timingDecoder = new TextDecoder();
    let timingTail = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 2 * 1024 * 1024) block('response_too_large');
      chunks.push(Buffer.from(value));
      state.response_bytes = size;
      state.last_response_chunk_at_ms = Date.now();
      timingTail += timingDecoder.decode(value, { stream: true });
      const timingLines = timingTail.split(/\r?\n/);
      timingTail = timingLines.pop();
      for (const line of timingLines) {
        if (!line.startsWith('data:') || state.first_content_at_ms) continue;
        try {
          const item = JSON.parse(line.slice(5));
          const content = item.type === 'response.output_text.delta' ? item.delta : item.choices?.[0]?.delta?.content;
          if (typeof content === 'string' && content.length) {
            state.first_content_at_ms = Date.now();
            state.first_content_timing = 'first_complete_nonempty_content_event';
          }
        } catch { /* An incomplete or non-JSON event is not a content timestamp. */ }
      }
      if (state.last_response_chunk_at_ms - lastProgressSave >= 1000) {
        save();
        lastProgressSave = state.last_response_chunk_at_ms;
      }
    }
    const raw = Buffer.concat(chunks).toString('utf8');
    if (!response.ok) {
      let detail;
      try { const parsed = JSON.parse(raw); detail = parsed.error ?? parsed; } catch { detail = {}; }
      const message = String(detail.message ?? '').toLowerCase();
      const kind = message.includes('unauthorized client detected') ? 'client_not_authorized'
        : [401, 403].includes(response.status) ? 'authentication_or_permission'
        : response.status === 429 ? 'rate_limit_or_quota' : 'provider_http_error';
      block(kind, { provider_error: {
        code: typeof detail.code === 'string' ? detail.code.slice(0, 160) : null,
        type: typeof detail.type === 'string' ? detail.type.slice(0, 160) : null,
        message: String(detail.message ?? raw).slice(0, 1200),
      }});
    }
    const rows = response.headers.get('content-type')?.includes('text/event-stream') || raw.trimStart().startsWith('data:')
      ? raw.split(/\r?\n/).filter(line => line.startsWith('data:') && line.slice(5).trim() !== '[DONE]')
        .map(line => JSON.parse(line.slice(5)))
      : [JSON.parse(raw)];
    state.response_events = rows;
    for (const row of rows) {
      const detail = row.type === 'error' ? (row.error ?? row) : row.response?.error;
      if (detail) {
        const code = detail.code ?? detail.type;
        const kind = code === 'rate_limit_exceeded' ? 'provider_rate_limit'
          : code === 'forbidden' ? 'provider_resource_blocked' : 'provider_stream_error';
        block(kind, { provider_error: { code, message: String(detail.message ?? '').slice(0,1200) },
          retry_after_seconds: Math.max(state.retry_after_seconds || 0, 30) });
      }
    }
    let text = '';
    let finish = null;
    for (const row of rows) {
      if (contract.protocol === 'responses') {
        const result = row.response ?? row;
        if (result.model) state.reported_model = result.model;
        if (result.id) state.request_id = result.id;
        if (result.usage) {
          state.input_tokens = result.usage.input_tokens ?? null;
          state.output_tokens = result.usage.output_tokens ?? null;
          state.provider_usage = result.usage;
        }
        if (row.type === 'response.output_text.delta') text += row.delta ?? '';
        if (result.output?.some(item => !['message','reasoning'].includes(item.type))) block('tool_response_blocked');
        if (['response.completed','response.incomplete','response.failed','response.cancelled'].includes(row.type) || (!row.type && result.status)) {
          finish = result.status === 'completed' ? 'stop' : result.status;
          text = result.output?.filter(item=>item.type==='message').flatMap(item=>item.content ?? [])
            .filter(part=>part.type==='output_text').map(part=>part.text).join('') || text;
        }
        continue;
      }
      if (row.model) state.reported_model = row.model;
      if (row.id) state.request_id = row.id;
      if (row.usage) {
        state.input_tokens = row.usage.prompt_tokens ?? null;
        state.output_tokens = row.usage.completion_tokens ?? null;
        state.provider_usage = row.usage;
      }
      for (const choice of row.choices ?? []) {
        if (choice.index !== 0) block('multiple_choices');
        const message = choice.delta ?? choice.message ?? {};
        if (message.tool_calls?.length || message.function_call) block('tool_response_blocked');
        text += message.content ?? '';
        if (choice.finish_reason) finish = choice.finish_reason;
      }
    }
    state.response = text;
    state.finish_reason = finish;
    if (![state.input_tokens, state.output_tokens].every(n => Number.isInteger(n) && n >= 0)) {
      block('usage_missing');
    }
    if (!state.reported_model) block('reported_model_missing');
    if (state.reported_model !== contract.model) block('reported_model_mismatch');
    if (finish !== 'stop') block('incomplete_response');
    state.status = 'complete';
    save();
    return Buffer.concat(chunks);
  } catch {
    block('transport_or_response_error');
  }
};

globalThis.fetch = async function (input, init) {
  const request = new Request(input, init);
  authorize(request, await request.clone().text());
  try {
    const response = await scope.run(true, () => nativeFetch(request, { redirect: 'manual' }));
    await examine(response.clone());
    return response;
  } catch { block('transport_or_response_error'); }
};

// Wrap the pinned client's actual Undici transport without changing its request headers.
if (process.env.AP_QWEN_RUNTIME) {
  const { ToolNames } = await import(process.env.AP_QWEN_TOOLS);
  process.argv.push('--exclude-tools', Object.values(ToolNames).join(','));
  const runtime = await import(process.env.AP_QWEN_RUNTIME);
  const undici = await runtime.loadUndici();
  const actualFetch = undici.fetch;
  undici.fetch = async (input, init) => {
    const request = new Request(input, init);
    authorize(request, await request.clone().text());
    try {
      const response = await scope.run(true, () => actualFetch(input, { ...init, redirect: 'manual' }));
      await examine(response.clone());
      return response;
    } catch { block('transport_or_response_error'); }
  };
}
save();
