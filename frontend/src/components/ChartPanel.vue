<template>
  <div v-if="plan" class="chartbox">
    <div class="chartbar">
      <div class="seg">
        <button :class="{ on: effType === 'bar' }" @click="type = 'bar'">柱状</button>
        <button :class="{ on: effType === 'line' }" @click="type = 'line'">折线</button>
        <button :class="{ on: effType === 'pie' }" :disabled="!pieOk" @click="type = 'pie'">
          饼图
        </button>
      </div>
      <select v-if="plan.dims.length > 1" v-model="dim" title="维度">
        <option v-for="d in plan.dims" :key="d" :value="d">{{ d }}</option>
      </select>
      <select v-if="plan.measures.length > 1" v-model.number="mi" title="度量">
        <option v-for="(m, i) in plan.measures" :key="m" :value="i">{{ m }}</option>
      </select>
      <span class="chartnote">{{ note }}</span>
    </div>
    <div class="chartdraw" v-html="svg"></div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { chartPlan, buildSeries, renderChart, isTimeCol, PIE_LIMIT } from "../utils/chart.js";

const props = defineProps({ rows: { type: Array, required: true } });

const plan = computed(() => chartPlan(props.rows));
const dim = ref("");
const mi = ref(0);
const type = ref("bar");

// 结果集换了一批（新的提问）就重置选择；时间维度默认折线
watch(
  plan,
  (p) => {
    if (!p) return;
    dim.value = p.dim;
    mi.value = 0;
    type.value = p.time ? "line" : "bar";
  },
  { immediate: true }
);

const isTime = computed(() => (plan.value && dim.value ? isTimeCol(dim.value, props.rows) : false));
const items = computed(() =>
  plan.value ? buildSeries(props.rows, dim.value, plan.value.measures, mi.value, isTime.value) : []
);

// 饼图只在"非负 + 扇区不多"时才有意义
const pieOk = computed(
  () => items.value.length <= PIE_LIMIT && items.value.every((d) => d.v >= 0) && items.value.some((d) => d.v > 0)
);
const effType = computed(() => (type.value === "pie" && !pieOk.value ? "bar" : type.value));

const svg = computed(() => {
  if (!plan.value || !items.value.length) return "";
  return renderChart(effType.value, items.value, dim.value, plan.value.measures[mi.value]);
});

const note = computed(() => {
  const n = items.value.length;
  const agg = n === props.rows.length ? "直接展示" : `${props.rows.length} 行按「${dim.value}」求和为 ${n} 组`;
  return `${agg} · 共 ${props.rows.length} 行`;
});
</script>
