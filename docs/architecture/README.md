# 구조 그림

[Archify](https://github.com/tt-a1i/archify) 로 코드를 따라가며 그린 그림이다. 노드마다 `파일:줄` 근거(SRC)가 붙어 있다. 기준 커밋 `67fa2a5` — 그 뒤 코드가 바뀌면 줄 번호가 어긋날 수 있다.

| 그림 | 원본(Archify JSON) | 무엇 |
|---|---|---|
| [ops-pipeline.html](ops-pipeline.html) | [ops-pipeline.workflow.json](ops-pipeline.workflow.json) | 크론 → `daily_report.sh` → 관문·짓기·검증·바꿔 끼우기, 감시·배포·웹 |
| [report-build-dataflow.html](report-build-dataflow.html) | [report-build.dataflow.json](report-build.dataflow.json) | DB → 집계 → `payload.json` → 화면·다이제스트·검증기 |

추적하며 찾은 문제: [운영](2026-11-19-problems-ops.md) · [데이터 흐름](2026-11-19-problems-dataflow.md).

다시 그리기: `npx skills add tt-a1i/archify --skill archify --agent claude-code --copy --yes`(버전은 `skills-lock.json`) 뒤 `node .claude/skills/archify/bin/archify.mjs finalize <type> <json> <html> --repo-root . --quality showcase --json`.
