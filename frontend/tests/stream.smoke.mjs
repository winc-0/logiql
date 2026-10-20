// SSE 联调冒烟：拿真实后端跑一问，检查消息状态机是否被正确填充。
// 与 chart.test.mjs 不同，这个要联网且会调 LLM（约 1 分钟），
// 需要先启动后端：uvicorn main:app --port 8010 --env-file .env
// 运行：node frontend/tests/stream.smoke.mjs
import { createBotMessage, streamQuery, finish } from "../src/composables/useQueryStream.js";

const API = process.env.ZHISHU_API || "http://127.0.0.1:8010/api/query";
const QUESTION = process.env.ZHISHU_Q || "各品类的销售额排名";

const msg = createBotMessage(QUESTION);
await streamQuery(QUESTION, API, msg);
finish(msg);

const cases = [
  ["步骤数 >= 11（correct_sql 仅在校验失败时出现）", msg.steps.length >= 11, msg.steps.length],
  ["末步已收尾", !msg._last, msg._last?.text],
  ["拿到 SQL", /select/i.test(msg.sql), msg.sql.slice(0, 60)],
  ["拿到结果集", Array.isArray(msg.result) && msg.result.length > 0, msg.result?.length],
  ["无错误", !msg.error, msg.error],
  ["耗时已记录", msg.elapsed > 0, msg.elapsed.toFixed(1)],
  ["阶段文案已规范化", msg.steps.some((s) => s.text === "生成 SQL"), msg.steps.map((s) => s.text).join("/")],
];

let fail = 0;
for (const [name, cond, extra] of cases) {
  console.log((cond ? "  ok  " : "FAIL  ") + name + (cond ? "" : "  → " + extra));
  if (!cond) fail++;
}
console.log(fail ? `\n失败 ${fail} 项` : "\nSSE 状态机通过");
process.exit(fail ? 1 : 0);
