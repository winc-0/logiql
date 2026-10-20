<template>
  <div class="card">
    <h4>生成的 SQL</h4>
    <pre class="sqlbox">{{ sql }}</pre>
    <button class="copybtn" @click="copy">{{ copied ? "已复制" : "复制" }}</button>
  </div>
</template>

<script setup>
import { ref } from "vue";

const props = defineProps({ sql: { type: String, required: true } });
const copied = ref(false);

async function copy() {
  try {
    await navigator.clipboard.writeText(props.sql);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1600);
  } catch {
    // 非安全上下文（http 且非 localhost）下剪贴板不可用，静默降级
  }
}
</script>
