// ГЛАВНЫЙ тест этапа 1: воронку можно собрать операциями редактора с нуля, и она совпадает
// с импортированной по смыслу (без учёта раскладки).
//
// Режим 1 (npm test): образцы из src/model/__fixtures__ (webinar, leadmagnet, повтор для не пришедших, автовебинар).
// Режим 2 (из pytest, tests/test_recreate_via_ops.py): RECREATE_IN – папка с графами,
// RECREATE_OUT – куда положить воссозданные графы; pytest выгружает их в yaml и сверяет
// с оригиналом через load_funnel движка.
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { canonical, meaningDiff } from "./canonical";
import { recreate } from "./recreate";
import type { Funnel } from "./types";

const FIXTURES = join(__dirname, "__fixtures__");
const IN = process.env.RECREATE_IN;
const OUT = process.env.RECREATE_OUT;

function counterIds(): () => string {
  let n = 0;
  // uuid4-вид: у новых шагов версия 4 – в отличие от импортированных uuid5
  return () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`;
}

function load(dir: string, file: string): Funnel {
  return JSON.parse(readFileSync(join(dir, file), "utf-8")) as Funnel;
}

const dir = IN ?? FIXTURES;
const files = readdirSync(dir).filter((f) => f.endsWith(".json") && !f.startsWith("default_"));

describe("воссоздать воронку операциями редактора", () => {
  it("образцы найдены", () => {
    expect(files.length).toBeGreaterThan(0);
  });

  for (const file of files) {
    it(file, () => {
      const src = load(dir, file);
      const { graph, ops } = recreate(src, counterIds());
      const diff = meaningDiff(src, graph);
      expect(diff, diff.join("\n")).toEqual([]);
      expect(JSON.stringify(canonical(graph))).toBe(JSON.stringify(canonical(src)));
      expect(ops).toBeGreaterThanOrEqual(src.steps.length + src.edges.length);
      // новые шаги – действительно новые id (не скопированы из импорта)
      expect(graph.steps.every((s) => s.id[14] === "4")).toBe(true);
      if (OUT) {
        mkdirSync(OUT, { recursive: true });
        // source переносим, чтобы бэкенд знал, с каким оригиналом сверять
        writeFileSync(join(OUT, file), JSON.stringify({ ...graph, source: src.source }, null, 1), "utf-8");
      }
    });
  }
});
