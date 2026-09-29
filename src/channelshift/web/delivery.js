"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { status: null, project: null, projects: [], pending: false, timer: null, sequence: 0, interventionRevision: null };
  const labels = { RECEIVED: "접수됨", EXTRACTING: "Codex 정리 중", RUNNING: "작업 중", REVIEW_REQUIRED: "검토 필요", REVIEWING: "Jev 검토 중", NEEDS_ATTENTION: "문제 확인 필요" };
  const reasons = { missing_client_info: "고객 정보 부족", contradictory_requirements: "요구사항 모순", model_error: "모델 오류", nonstandard_request: "표준 밖 요청", access_approval: "권한·승인 확인", quality_issue: "품질 문제", other: "기타" };
  const errors = {
    invalid_token: "연결 확인이 만료되었습니다. 입력 내용을 따로 보관한 뒤 페이지를 새로고침하세요.",
    invalid_origin: "연결 주소를 확인할 수 없습니다. 로컬 ChannelShift 주소에서 다시 열어 주세요.",
    invalid_request: "입력 항목을 확인하고 다시 시도해 주세요.",
    invalid_project: "프로젝트 이름과 고객 원문을 확인해 주세요.",
    invalid_delivery_project: "프로젝트 이름과 고객 원문을 확인해 주세요.",
    invalid_delivery_input: "필수 입력과 길이를 확인하세요. 원문은 12,000자, 개입 기록의 각 항목은 2,000자까지 저장할 수 있습니다.",
    delivery_project_not_found: "프로젝트를 찾지 못했습니다. 목록을 새로고침하세요.",
    delivery_storage_limit: "로컬 기록 저장 한도에 도달했습니다. 담당자에게 보관 정책 확인을 요청하세요.",
    delivery_busy: "다른 작업이 진행 중입니다. 작업 결과를 기다린 뒤 다시 시도하세요.",
    delivery_recovery_required: "이전 작업의 종료 확인이 필요합니다. 중복 실행하지 말고 담당자에게 실행 상태 점검을 요청하세요.",
    delivery_candidate_required: "내 Codex로 요구사항을 먼저 정리한 뒤 Jev 검토를 요청하세요.",
    delivery_revision_conflict: "검토 대상이 변경되었습니다. 메모는 보존했습니다. 최신 결과를 확인한 뒤 다시 저장하세요.",
    delivery_job_failed: "작업을 마치지 못했습니다. 담당자에게 연결과 작업 이력 확인을 요청하세요.",
    invalid_intervention: "개입 단계·이유·상황 내용을 확인해 주세요.",
    invalid_source: "고객 요구사항 원문을 입력해 주세요.",
    source_required: "고객 요구사항 원문부터 접수해 주세요.",
    project_not_found: "프로젝트를 찾지 못했습니다. 목록을 새로고침하세요.",
    not_found: "요청한 항목을 찾지 못했습니다. 페이지를 새로고침하세요.",
    codex_unavailable: "이 컴퓨터에서 Codex를 찾지 못했습니다. Codex 설치와 실행 상태를 확인하세요.",
    codex_not_authenticated: "기존 Codex 앱에서 본인 계정으로 공식 로그인을 완료한 뒤 연결을 다시 확인하세요.",
    codex_login_required: "기존 Codex 앱에서 본인 계정으로 공식 로그인을 완료한 뒤 연결을 다시 확인하세요.",
    codex_authentication_required: "기존 Codex 앱에서 본인 계정으로 공식 로그인을 완료한 뒤 연결을 다시 확인하세요.",
    codex_authentication_unverified: "Codex 인증 방식을 확인하지 못했습니다. 기존 Codex 앱에서 본인 구독 로그인을 확인하세요.",
    codex_unsafe_provider_environment: "구독 외 연결 설정이 감지되었습니다. 담당자에게 실행 환경 확인을 요청하세요.",
    codex_unsupported_cli: "현재 Codex 버전은 이 실행 방식과 호환되지 않습니다. 담당자에게 버전 확인을 요청하세요.",
    codex_timeout: "Codex 정리 시간이 초과되었습니다. 연결 상태를 확인하고 원문의 범위를 검토하세요.",
    codex_stop_failed: "이전 Codex 실행의 종료를 확인하지 못했습니다. 담당자가 확인하기 전 다시 실행하지 마세요.",
    codex_invalid_output: "Codex 결과가 요구사항 형식에 맞지 않습니다. 원문과 작업 이력을 확인한 뒤 다시 정리하세요.",
    codex_execution_failed: "Codex 작업에 실패했습니다. 연결 상태를 확인한 뒤 다시 시도하세요.",
    subscription_required: "본인 구독으로 연결된 Codex 계정이 필요합니다. Codex 앱의 로그인 방식을 확인하세요.",
    codex_subscription_required: "본인 구독으로 연결된 Codex 계정이 필요합니다. Codex 앱의 로그인 방식을 확인하세요.",
    codex_rate_limited: "Codex 이용 한도에 도달했습니다. 한도 초기화 후 연결을 확인하고 다시 요청하세요.",
    jev_not_configured: "이 컴퓨터의 Jev 연결 설정이 필요합니다. 담당자에게 TypeSafe 연결 설정을 요청하세요.",
    jev_key_missing: "이 컴퓨터의 Jev 연결 설정이 필요합니다. 담당자에게 TypeSafe 연결 설정을 요청하세요.",
    jev_auth_failed: "Jev 인증을 확인하지 못했습니다. 담당자에게 이 컴퓨터의 TypeSafe 연결 점검을 요청하세요.",
    jev_rate_limited: "Jev 이용 한도에 도달했습니다. TypeSafe 이용 상태를 확인한 뒤 다시 요청하세요.",
    jev_unavailable: "Jev에 연결할 수 없습니다. 잠시 후 다시 요청하세요.",
    candidate_required: "내 Codex로 요구사항을 먼저 정리한 뒤 Jev 검토를 요청하세요.",
    project_busy: "이 프로젝트의 작업이 진행 중입니다. 결과를 기다린 뒤 다시 시도하세요.",
    busy: "작업이 진행 중입니다. 결과를 기다린 뒤 다시 시도하세요.",
    payload_too_large: "입력 내용이 너무 큽니다. 담당자와 접수 범위를 확인해 주세요.",
    operation_failed: "작업을 마치지 못했습니다. 연결 상태와 작업 이력을 확인한 뒤 다시 시도하세요.",
  };
  const running = (project) => Boolean(project && ["EXTRACTING", "REVIEWING", "RUNNING"].includes(project.state));
  const text = (value) => typeof value === "string" ? value : "";
  const list = (value) => Array.isArray(value) ? value : [];
  function node(tag, className, value) { const el = document.createElement(tag); if (className) el.className = className; if (value !== undefined) el.textContent = String(value); return el; }
  function message(value, error = false) { $("message").textContent = value; $("message").classList.toggle("error", error); $("message").hidden = !value; }
  function date(value) { const time = new Date(value); return Number.isNaN(time.getTime()) ? "" : time.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }); }
  function safeError(code) { return errors[code] || "작업을 마치지 못했습니다. 연결 상태와 작업 이력을 확인해 주세요."; }
  async function api(path, body) {
    const options = { headers: { "X-ChannelShift-Token": document.querySelector('meta[name="channelshift-token"]').content }, credentials: "same-origin", cache: "no-store" };
    if (body !== undefined) { options.method = "POST"; options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
    let response;
    try { response = await fetch(path, options); } catch { throw new Error("로컬 서버에 연결할 수 없습니다. ChannelShift 실행 상태를 확인한 뒤 새로고침하세요."); }
    let result;
    try { result = await response.json(); } catch { throw new Error("응답을 읽지 못했습니다. 서버 상태를 확인한 뒤 다시 시도하세요."); }
    if (!response.ok || result.ok !== true) { const error = new Error(safeError(result.error)); error.status = response.status; error.code = result.error; throw error; }
    return result;
  }
  function controls() {
    const busy = state.pending || running(state.project);
    $("create-project").disabled = state.pending;
    $("extract").disabled = busy || !state.project || state.status?.codex?.can_execute !== true;
    $("review-jev").disabled = busy || !state.project?.candidate || state.status?.jev_configured !== true;
    $("save-intervention").disabled = state.pending || !state.project;
    $("new-project").disabled = state.pending;
    $("extract").textContent = state.project?.state === "EXTRACTING" ? "정리 중…" : "Codex로 정리";
    $("review-jev").textContent = state.project?.state === "REVIEWING" ? "검토 중…" : "Jev 검토";
  }
  async function refreshStatus() {
    $("refresh-status").disabled = true;
    try {
      state.status = await api("/api/delivery/status");
      if (Array.isArray(state.status.stages) && state.status.stages.length) {
        const selectedStage = $("stage-id").value;
        $("stage-id").replaceChildren(...state.status.stages.map((stage) => {
          const option = node("option", null, text(stage.title).replace(/^[GS]\d[A-Z]?\s+/, "")); option.value = stage.id; return option;
        }));
        if (state.status.stages.some((stage) => stage.id === selectedStage)) $("stage-id").value = selectedStage;
        if (state.project) renderHistory(state.project);
      }
      const codex = state.status.codex || {};
      $("codex-status").textContent = codex.can_execute === true ? "연결됨" : codex.available === true && codex.authenticated !== true ? "Codex 앱 로그인 필요" : "연결 확인 필요";
      $("codex-status").title = codex.can_execute === true ? "" : safeError(codex.reason);
      $("jev-status").textContent = state.status.jev_configured === true ? "연결됨" : "설정 필요";
    } catch (error) { state.status = null; $("codex-status").textContent = "확인 실패"; $("jev-status").textContent = "확인 실패"; message(error.message, true); }
    finally { $("refresh-status").disabled = false; controls(); }
  }
  function renderList() {
    const target = $("project-list"); target.replaceChildren();
    $("project-list-status").textContent = state.projects.length ? `${state.projects.length}개` : "프로젝트 없음";
    state.projects.forEach((project) => {
      const item = node("button", "project-item"); item.type = "button"; item.setAttribute("aria-current", String(project.id === state.project?.id));
      item.append(node("strong", null, text(project.name) || "이름 없는 프로젝트"), node("small", null, [labels[project.state] || "상태 확인 필요", date(project.created_at)].filter(Boolean).join(" · ")));
      item.addEventListener("click", () => { if (!state.pending) openProject(project.id); }); target.append(item);
    });
  }
  async function refreshProjects() { $("refresh-projects").disabled = true; try { const result = await api("/api/delivery/projects"); state.projects = list(result.items); renderList(); } catch (error) { $("project-list-status").textContent = error.message; } finally { $("refresh-projects").disabled = false; } }
  function resultList(id, items, kind) {
    const target = $(id); target.replaceChildren();
    if (!items.length) { target.append(node("p", "empty-result", "없음")); return; }
    items.forEach((item) => {
      const row = node("article", "result-item");
      const label = [text(item.id), kind === "question" && item.blocking ? "먼저 확인 필요" : ""].filter(Boolean).join(" · ");
      if (label) row.append(node("strong", null, label));
      row.append(node("p", null, text(item.text)));
      if (item.quote) row.append(node("blockquote", null, text(item.quote)));
      if (kind === "internal" && !["internal", "internal_proposal"].includes(item.origin)) row.append(node("p", "muted small", "출처 확인 필요"));
      target.append(row);
    });
  }
  function renderJev(project) {
    const target = $("jev-results"); target.replaceChildren();
    if (!project.jev) return;
    target.append(node("p", "muted small", project.jev.status === "completed" || project.jev.status === "COMPLETE" ? "Jev 대조 결과" : "Jev 검토 기록"));
    const judgments = { supported: "원문에서 근거를 찾음", unsupported: "원문 근거를 찾지 못함", contradicted: "원문과 모순될 수 있음", unclear: "추가 확인 필요" };
    list(project.jev.items).forEach((item) => { const row = node("article", "result-item"); row.append(node("strong", null, text(item.requirement_id) || "요구사항 확인"), node("p", null, judgments[item.judgment] || "판정 내용 확인 필요")); if (typeof item.confidence === "number" && Number.isFinite(item.confidence)) row.append(node("p", "muted small", `신뢰도 ${item.confidence}`)); target.append(row); });
    if (!list(project.jev.items).length) target.append(node("p", "empty-result", "결과 없음"));
  }
  function renderHistory(project) {
    const target = $("interventions"); target.replaceChildren();
    list(project.interventions).slice().reverse().forEach((item) => {
      const row = node("article", "history-item"); const option = Array.from($("stage-id").options).find((value) => value.value === item.stage_id);
      row.append(node("h3", null, `${option?.textContent || text(item.stage_id)} · ${reasons[item.reason] || "개입 기록"}`), node("small", null, date(item.created_at)), node("p", null, text(item.note)));
      const detail = node("dl"); [["결정·다음 행동", item.decision], ["결과·남은 문제", item.outcome]].forEach(([label, value]) => { if (value) detail.append(node("dt", null, label), node("dd", null, text(value))); }); row.append(detail); target.append(row);
    });
    if (!list(project.interventions).length) target.append(node("p", "empty-result", "기록 없음"));
    const events = $("events"); events.replaceChildren();
    list(project.events).slice().reverse().forEach((item) => {
      const row = node("article", "history-item"); const kind = text(item.type) || text(item.event) || text(item.kind);
      const eventNames = { source_registered: "고객 원문 접수", job_started: item.payload?.operation === "jev" ? "Jev 검토 시작" : "Codex 정리 시작", candidate_recorded: "요구사항 초안 저장", advice_recorded: "Jev 대조 의견 저장", job_failed: "작업 실패 · 확인 필요", human_intervention: "사람 개입 기록" };
      row.append(node("p", null, eventNames[kind] || "작업 상태 기록"), node("small", null, date(item.created_at || item.at)));
      const code = item.payload?.code || item.error; if (code) row.append(node("p", null, safeError(code))); events.append(row);
    });
    if (!list(project.events).length) events.append(node("p", "empty-result", "기록 없음"));
  }
  function renderProject() {
    const project = state.project; $("intake-panel").hidden = Boolean(project); $("project-panel").hidden = !project; renderList(); controls();
    if (!project) return;
    $("selected-name").textContent = text(project.name); $("project-state").textContent = labels[project.state] || "상태 확인 필요";
    $("source-text").textContent = text(project.source?.text);
    $("candidate-panel").hidden = !project.candidate;
    if (project.candidate) { const requirements = list(project.candidate.requirements); const fromClient = (item) => ["client", "customer", "client_original"].includes(item.origin); resultList("client-requirements", requirements.filter(fromClient), "client"); resultList("internal-requirements", requirements.filter((item) => !fromClient(item)), "internal"); resultList("questions", list(project.candidate.questions), "question"); resultList("out-of-scope", list(project.candidate.out_of_scope), "scope"); renderJev(project); }
    renderHistory(project);
  }
  function schedulePoll() {
    clearTimeout(state.timer); if (!running(state.project)) return;
    const id = state.project.id; const sequence = state.sequence;
    state.timer = setTimeout(async () => { try { const result = await api(`/api/delivery/projects/${encodeURIComponent(id)}`); if (sequence !== state.sequence || state.project?.id !== id) return; state.project = result.project; renderProject(); if (!running(state.project)) { message(labels[state.project.state] || "상태 변경됨"); await refreshProjects(); } schedulePoll(); } catch (error) { if (sequence === state.sequence) message(error.message, true); } }, 2500);
  }
  async function openProject(id) {
    clearTimeout(state.timer); const sequence = ++state.sequence; message("");
    try { const result = await api(`/api/delivery/projects/${encodeURIComponent(id)}`); if (sequence !== state.sequence) return; state.project = result.project; state.interventionRevision = null; $("intervention-form").reset(); renderProject(); schedulePoll(); } catch (error) { if (sequence === state.sequence) message(error.message, true); }
  }
  async function mutation(path, body, success) {
    if (state.pending) return false; state.pending = true; controls(); message("");
    try { const result = await api(path, body); if (!result.project || typeof result.project.id !== "string") throw new Error("프로젝트 응답을 읽지 못했습니다. 목록을 새로고침해 저장 여부를 확인하세요."); state.project = result.project; ++state.sequence; renderProject(); schedulePoll(); message(success); await refreshProjects(); return true; }
    catch (error) { message(error.message, true); if (error.status === 409 && state.project) { const id = state.project.id; try { const result = await api(`/api/delivery/projects/${encodeURIComponent(id)}`); state.project = result.project; if (error.code === "delivery_revision_conflict") state.interventionRevision = state.project.intervention_revision; renderProject(); schedulePoll(); } catch {} } return false; }
    finally { state.pending = false; controls(); }
  }
  $("intake-form").addEventListener("submit", async (event) => { event.preventDefault(); const name = $("project-name").value.trim(); const request = $("client-request").value; if (!name || !request.trim()) { message("프로젝트명과 원문을 입력하세요.", true); return; } if (await mutation("/api/delivery/projects", { name, client_request: request }, "원문 저장됨")) $("intake-form").reset(); });
  $("extract").addEventListener("click", () => { if (state.project && !$("extract").disabled) mutation("/api/delivery/extract", { project_id: state.project.id }, "Codex 정리 요청됨"); });
  $("review-jev").addEventListener("click", () => { if (state.project && !$("review-jev").disabled) mutation("/api/delivery/jev", { project_id: state.project.id }, "Jev 검토 요청됨"); });
  $("intervention-form").addEventListener("input", () => { if (!state.interventionRevision) state.interventionRevision = state.project?.intervention_revision || null; });
  $("intervention-form").addEventListener("submit", async (event) => { event.preventDefault(); if (!state.project) return; const note = $("intervention-note").value.trim(); if (!note) { message("상황과 확인 내용을 입력하세요.", true); return; } const body = { project_id: state.project.id, expected_revision: state.interventionRevision || state.project.intervention_revision, stage_id: $("stage-id").value, reason: $("intervention-reason").value, note, decision: $("intervention-decision").value.trim(), outcome: $("intervention-outcome").value.trim() }; if (await mutation("/api/delivery/intervention", body, "개입 기록 저장됨")) { state.interventionRevision = null; $("intervention-form").reset(); } });
  $("new-project").addEventListener("click", () => { if (state.pending) return; clearTimeout(state.timer); ++state.sequence; state.project = null; state.interventionRevision = null; $("intervention-form").reset(); message(""); renderProject(); $("client-request").focus(); });
  $("refresh-projects").addEventListener("click", refreshProjects); $("refresh-status").addEventListener("click", refreshStatus);
  window.addEventListener("pagehide", () => clearTimeout(state.timer));
  renderProject(); Promise.allSettled([refreshStatus(), refreshProjects()]);
})();
