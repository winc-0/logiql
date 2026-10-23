<template>
  <div class="chat-page">
    <header>
      <div class="brand"><b>仓配自助问数</b><span>企业级 NL2SQL 智能问数</span></div>
      <div class="spacer"></div>
      <span class="hint">{{ apiHint }}</span>
    </header>

    <main ref="streamEl">
      <div class="wrap">
        <template v-for="m in messages" :key="m.id">
          <div v-if="m.role === 'user'" class="msg-user">
            <div class="bubble">{{ m.text }}</div>
          </div>

          <div v-else class="msg-bot">
            <StepsCard :steps="m.steps" />
            <SqlCard v-if="m.sql" :sql="m.sql" />
            <ResultCard v-if="m.result" :rows="m.result" />
            <ErrorCard v-if="m.error" :message="m.error" />
            <div class="status">
              <span v-if="m.running" class="spin"></span>
              <span>{{ m.running ? "正在处理…" : `完成 · 耗时 ${m.elapsed.toFixed(1)}s` }}</span>
            </div>
          </div>
        </template>
      </div>
    </main>

    <footer>
      <div class="bar">
        <div class="examples">
          <span v-for="t in EXAMPLES" :key="t" class="chip" @click="useExample(t)">{{ t }}</span>
        </div>
        <div class="inputrow">
          <textarea
            v-model="question"
            rows="1"
            placeholder="用自然语言问一句话，比如：2025年各承运商的妥投及时率分别是多少？"
            @keydown.enter.exact.prevent="submit"
            @input="autoGrow"
          ></textarea>
          <button v-if="!busy" class="send" :disabled="!question.trim()" @click="submit">发送</button>
          <button v-else class="send stop" @click="abort">停止</button>
        </div>
        <div class="tip">Enter 发送 · Shift+Enter 换行</div>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { nextTick, ref } from "vue";
import StepsCard from "./components/StepsCard.vue";
import SqlCard from "./components/SqlCard.vue";
import ResultCard from "./components/ResultCard.vue";
import ErrorCard from "./components/ErrorCard.vue";
import { EXAMPLES } from "./utils/stages.js";
import {
  createBotMessage,
  streamQuery,
  finish,
  applyError,
} from "./composables/useQueryStream.js";

// 开发态由 vite 的 /api 代理转发到后端；生产态 build 产物由后端同端口托管，
// 两条路都是同源 /api/query，不需要在前端再拼地址
const API_URL = "/api/query";
const apiHint = import.meta.env.DEV ? "dev · 代理到后端 :8010" : "同源 /api/query";

const question = ref("");
const busy = ref(false);
const messages = ref([]);
const streamEl = ref(null);
let seq = 0;
let controller = null;

async function toEnd() {
  await nextTick();
  const el = streamEl.value;
  if (el) el.scrollTop = el.scrollHeight;
}

function autoGrow(e) {
  const t = e.target;
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 160) + "px";
}

function useExample(t) {
  question.value = t;
}

function abort() {
  controller?.abort();
}

async function submit() {
  const text = question.value.trim();
  if (!text || busy.value) return;

  busy.value = true;
  question.value = "";
  messages.value.push({ id: ++seq, role: "user", text });
  const bot = createBotMessage(text);
  bot.id = ++seq;
  messages.value.push(bot);
  await toEnd();

  controller = new AbortController();
  try {
    await streamQuery(text, API_URL, bot, controller.signal);
  } catch (e) {
    if (e?.name !== "AbortError") {
      applyError(bot, String(e?.message || e));
    }
  } finally {
    finish(bot);
    // 被中断时把没跑完的那一步标成 error，避免一直转圈
    if (bot._last) bot._last.status = "error";
    controller = null;
    busy.value = false;
    await toEnd();
  }
}
</script>
