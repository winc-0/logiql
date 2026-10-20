<template>
  <div class="card err">
    <h4>执行失败</h4>
    <div>{{ message }}</div>
  </div>
</template>

<script setup>
defineProps({ message: { type: String, required: true } });
</script>
