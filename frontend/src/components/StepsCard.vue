<template>
  <div class="card">
    <h4>执行链路</h4>
    <div class="steps">
      <div v-for="(s, i) in steps" :key="i" class="step" :class="s.status">
        <span class="dot">
          {{ s.status === "error" ? "!" : s.status === "success" ? "✓" : "" }}
        </span>
        <span class="txt">{{ s.text }}</span>
        <span class="t">+{{ s.at.toFixed(1) }}s</span>
      </div>
      <div v-if="!steps.length" class="step run">
        <span class="dot"></span><span class="txt">正在处理…</span>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({ steps: { type: Array, required: true } });
</script>
