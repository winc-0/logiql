import { reactive } from "vue";
import { stageLabel } from "../utils/stages.js";

// 一条助手消息的状态容器：进度步骤 + 按需追加的 SQL / 结果 / 错误。
// 流式回调直接改这个对象，Vue 负责渲染，不再手工拼 DOM
export function createBotMessage(question) {
  return reactive({
    role: "assistant",
    question,
    steps: [],        // { text, status: running|success|error, at }
    sql: "",
    result: null,     // 数组即结果集
    error: "",
    elapsed: 0,
    running: true,
    t0: performance.now(),
    _last: null,      // 当前处于 running 的步骤
  });
}

function closeRunning(msg, status = "success") {
  if (msg._last) {
    msg._last.status = status;
    msg._last = null;
  }
}

export function applyStage(msg, stage) {
  closeRunning(msg);
  const step = reactive({
    text: stageLabel(stage),
    status: "running",
    at: (performance.now() - msg.t0) / 1000,
  });
  msg.steps.push(step);
  msg._last = step;
}

export function applySql(msg, sql) {
  msg.sql = sql;
}

export function applyResult(msg, rows) {
  closeRunning(msg);
  msg.result = rows;
}

export function applyError(msg, text) {
  closeRunning(msg, "error");
  msg.error = text;
  msg.running = false;
}

export function finish(msg) {
  closeRunning(msg);
  msg.running = false;
  msg.elapsed = (performance.now() - msg.t0) / 1000;
}

// SSE 解析。后端 yield 的是 "data: {json}  \n\n"，
// EventSource 不支持 POST，所以用 fetch 读流自己切分
export async function streamQuery(question, apiUrl, msg, signal) {
  const resp = await fetch(apiUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: question }),
    signal,
  });

  if (!resp.ok) {
    applyError(msg, `HTTP ${resp.status} ${resp.statusText}（检查后端是否已启动）`);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let gotResult = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // 事件之间以空行分隔，最后一段可能不完整，留到下一轮
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = raw.trim();
      if (!line.startsWith("data:")) continue;
      let payload;
      try {
        payload = JSON.parse(line.slice(5).trim());
      } catch {
        continue;   // 半包或心跳，忽略
      }
      if (payload.stage) applyStage(msg, payload.stage);
      if (payload.sql) applySql(msg, payload.sql);
      if (Array.isArray(payload.result)) {
        gotResult = true;
        applyResult(msg, payload.result);
      }
      if (payload.error) {
        applyError(msg, payload.error);
        return;
      }
    }
  }

  if (!gotResult && !msg.error) {
    applyError(msg, "链路未返回结果集（可能中途异常中断）");
  }
}
