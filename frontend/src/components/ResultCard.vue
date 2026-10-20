<template>
  <div class="card">
    <h4>查询结果（{{ rows.length }} 行）</h4>
    <ChartPanel :rows="rows" />
    <div class="tablewrap">
      <table>
        <thead>
          <tr><th v-for="c in cols" :key="c">{{ c }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in shown" :key="i">
            <td v-for="c in cols" :key="c" :class="isNum(r[c]) ? 'num' : ''">
              {{ fmtCell(r[c]) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="rows.length > LIMIT" class="more">
      仅显示前 {{ LIMIT }} 行，共 {{ rows.length }} 行
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import ChartPanel from "./ChartPanel.vue";
import { isNum, fmtCell } from "../utils/format.js";

const props = defineProps({ rows: { type: Array, required: true } });
const LIMIT = 100;

const cols = computed(() => (props.rows.length ? Object.keys(props.rows[0]) : []));
const shown = computed(() => props.rows.slice(0, LIMIT));
</script>
