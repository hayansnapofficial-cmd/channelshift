"use strict";

// Registered dependency preview only. It does not change artifacts or Gate state.
(() => {
  const kinds = { api: "API", backend: "백엔드", screen: "화면", test: "테스트" };
  const reasonText = {
    graph_missing: "연결 정보 없음",
    schema_revision_mismatch: "기준 버전 다름",
    field_node_missing: "필드 연결 없음",
    coverage_incomplete: "일부 연결 미확인",
    registered_graph: "등록된 연결 기준",
  };

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }

  function create(root, { api, getSchema }) {
    let graph = null;
    let selection = null;
    let session = null;
    let sequence = 0;
    let projectGeneration = 0;
    let graphRevision = 0;
    const heading = node("div", "impact-heading");
    const title = node("h2", null, "변경 영향");
    title.id = "impact-heading";
    const status = node("span", "impact-status", "미확인");
    status.id = "impact-status";
    heading.append(title, status);
    const target = node("p", "impact-target", "선택한 필드 없음");
    target.id = "impact-target";
    const result = node("div", "impact-result");
    result.setAttribute("aria-live", "polite");
    result.setAttribute("aria-atomic", "true");
    const counts = node("dl", "impact-counts");
    counts.id = "impact-counts";
    const message = node("p", "impact-message");
    message.id = "impact-message";
    result.append(counts, message);
    const items = node("ul", "impact-items");
    items.id = "impact-items";
    items.setAttribute("aria-label", "등록된 영향 항목과 연결 경로");
    const actions = node("div", "impact-actions");
    const importButton = node("button", "button secondary", "연결 JSON 열기");
    importButton.type = "button";
    importButton.id = "impact-import-trigger";
    const clearButton = node("button", "text-button", "연결 해제");
    clearButton.type = "button";
    clearButton.id = "impact-clear";
    clearButton.disabled = true;
    const file = node("input");
    file.type = "file";
    file.id = "impact-import-file";
    file.accept = ".json,application/json";
    file.hidden = true;
    const fileName = node("p", "impact-file", "최대 128 KiB");
    fileName.id = "impact-file-name";
    actions.append(importButton, clearButton, file);
    root.append(heading, target, result, items, actions, fileName);

    function render(payload, text) {
      const state = payload?.status || "UNKNOWN";
      root.dataset.status = state;
      root.setAttribute("aria-busy", "false");
      status.textContent = state === "LINKED" ? "연결됨" : state === "PARTIAL" ? "일부 미확인" : "미확인";
      counts.replaceChildren();
      for (const [kind, label] of Object.entries(kinds)) {
        const count = payload?.counts?.[kind];
        const observed = payload?.observed_counts?.[kind];
        const cell = node("div", "impact-count");
        const known = Number.isInteger(count) && count >= 0;
        cell.append(node("dt", null, label), node("dd", known ? "" : "impact-unknown", known ? `${count}개` : "미확인"));
        if (!known && Number.isInteger(observed) && observed > 0) {
          cell.append(node("small", null, `등록 ${observed}개 확인`));
        }
        counts.append(cell);
      }
      message.textContent = text || reasonText[payload?.reason] || reasonText.graph_missing;
      items.replaceChildren();
      const linked = Array.isArray(payload?.items) ? payload.items : [...(payload?.direct || []), ...(payload?.indirect || [])];
      for (const item of linked) {
        if (!item || !Object.hasOwn(kinds, item.kind)) continue;
        const entry = node("li");
        const path = Array.isArray(item.path) ? item.path : [];
        const distance = Number.isInteger(item.distance) ? item.distance : Math.max(1, path.length - 1);
        entry.append(node("span", "impact-kind", `${kinds[item.kind]} · ${distance > 1 ? "간접" : "직접"} 연결`),
          node("strong", null, typeof item.label === "string" ? item.label : item.id),
          node("small", "impact-recheck", "재검토 대상"));
        if (path.length) entry.append(node("code", "impact-path", path.join(" → ")));
        items.append(entry);
      }
      items.hidden = !items.childElementCount;
    }

    async function select(schema, tableId, fieldId, { editing = false, fieldKey = null, tableKey = null } = {}) {
      const sameField = session && fieldKey && session.fieldKey === fieldKey && session.graph === graph;
      if (!sameField) session = { schema, tableId, fieldId, fieldKey, graph, edited: false };
      session.edited = session.edited || editing;
      selection = { tableId, fieldId, fieldKey, tableKey };
      target.textContent = `${session.tableId}.${session.fieldId}${session.edited ? " · 편집 전 기준" : ""}`;
      const request = ++sequence;
      if (!graph) { render(null); return; }
      if (!sameField) {
        render(null, "연결 확인 중");
        status.textContent = "확인 중";
      }
      root.setAttribute("aria-busy", "true");
      try {
        const response = await api("/api/impact", { schema: session.schema, table_id: session.tableId, field_id: session.fieldId, graph });
        if (request !== sequence) return;
        render(response.impact);
      } catch {
        if (request !== sequence) return;
        render(null, "연결 확인 실패");
      }
    }

    function clear({ project = false } = {}) {
      sequence += 1;
      graphRevision += 1;
      if (project) { projectGeneration += 1; selection = null; }
      session = null;
      graph = null;
      clearButton.disabled = true;
      file.value = "";
      fileName.textContent = "최대 128 KiB";
      if (!selection) target.textContent = "선택한 필드 없음";
      render(null);
    }

    function setGraph(value, label = "연결 정보", { expectedRevision } = {}) {
      if (expectedRevision !== undefined && expectedRevision !== graphRevision) return false;
      if (value !== null && (typeof value !== "object" || Array.isArray(value))) return false;
      sequence += 1;
      graphRevision += 1;
      session = null;
      graph = value;
      clearButton.disabled = !graph;
      fileName.textContent = String(label).slice(0, 160);
      if (graph && selection) {
        void select(getSchema(), selection.tableKey?.name || selection.tableId,
          selection.fieldKey?.name || selection.fieldId, { fieldKey: selection.fieldKey, tableKey: selection.tableKey });
      } else render(null, graph ? "필드 선택 필요" : label);
      return true;
    }

    importButton.addEventListener("click", () => file.click());
    clearButton.addEventListener("click", () => clear());
    file.addEventListener("change", async () => {
      const selectedFile = file.files[0];
      file.value = "";
      if (!selectedFile) return;
      const generation = projectGeneration;
      clear();
      const request = sequence;
      if (selectedFile.size > 128 * 1024) {
        render(null, "파일 한도 초과 · 최대 128 KiB");
        return;
      }
      let parsed;
      try {
        parsed = JSON.parse(await selectedFile.text());
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      } catch {
        if (generation === projectGeneration && request === sequence) render(null, "JSON 형식 오류");
        return;
      }
      if (generation !== projectGeneration || request !== sequence) return;
      setGraph(parsed, selectedFile.name.slice(0, 100));
    });

    render(null);
    return Object.freeze({
      select,
      setGraph,
      getRevision: () => graphRevision,
      reset: () => clear({ project: true }),
      schemaChanged: (message = "") => {
        sequence += 1;
        graphRevision += 1;
        session = null;
        render(null, message || (graph ? "구조 변경 · 연결 재확인 필요" : reasonText.graph_missing));
      },
    });
  }

  window.ChannelShiftImpact = Object.freeze({ create });
})();
