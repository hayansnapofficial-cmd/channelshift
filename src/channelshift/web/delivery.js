"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { status: null, project: null, projects: [], pending: false, advancing: false, timer: null, sequence: 0, interventionRevision: null, answerDrafts: new Map(), consentDrafts: new Map(), connection: null, connectionBusy: false, connectionTimer: null, connectionSequence: 0, referenceDrafts: new Map(), erdDatabaseDrafts: new Map(), mcpConnection: null, mcpToken: "", mcpBusy: false, mcpSequence: 0, mcpDisplayAllowed: false };
  const consentModes = { undecided: "미결정", required: "사용", not_required: "사용 안함" };
  const labels = { RECEIVED: "접수됨", EXTRACTING: "Codex 정리 중", RUNNING: "작업 중", REVIEW_REQUIRED: "검토 필요", REVIEWING: "요구사항 검토 중", COLLECTING_REFERENCE: "참고 자료 수집 중", DESIGNING_ERD: "ERD 초안 생성 중", NEEDS_ATTENTION: "문제 확인 필요" };
  const reasons = { missing_client_info: "고객 정보 부족", contradictory_requirements: "요구사항 모순", model_error: "모델 오류", nonstandard_request: "표준 밖 요청", access_approval: "권한·승인 확인", quality_issue: "품질 문제", other: "기타" };
  const errors = {
    member_provider_not_linked: "내 Codex 연결에서 본인 계정으로 로그인하세요.",
    login_required: "로그인이 만료되었습니다. 입력 내용을 보관한 뒤 내 계정에서 다시 로그인하세요.",
    invalid_token: "연결 확인이 만료되었습니다. 입력 내용을 따로 보관한 뒤 페이지를 새로고침하세요.",
    invalid_origin: "연결 주소를 확인할 수 없습니다. 로컬 ChannelShift 주소에서 다시 열어 주세요.",
    invalid_request: "입력 항목을 확인하고 다시 시도해 주세요.",
    invalid_project: "프로젝트 이름과 고객 원문을 확인해 주세요.",
    invalid_delivery_project: "프로젝트 이름과 고객 원문을 확인해 주세요.",
    invalid_delivery_input: "필수 입력과 길이를 확인하세요. 원문은 12,000자, 답변과 개입 기록의 각 항목은 2,000자까지 저장할 수 있습니다.",
    delivery_project_not_found: "프로젝트를 찾지 못했습니다. 목록을 새로고침하세요.",
    delivery_storage_limit: "로컬 기록 저장 한도에 도달했습니다. 담당자에게 보관 정책 확인을 요청하세요.",
    delivery_busy: "다른 작업이 진행 중입니다. 작업 결과를 기다린 뒤 다시 시도하세요.",
    delivery_recovery_required: "이전 작업의 종료 확인이 필요합니다. 중복 실행하지 말고 담당자에게 실행 상태 점검을 요청하세요.",
    delivery_candidate_required: "Codex로 요구사항을 먼저 정리해 주세요.",
    delivery_review_required: "저장한 답변과 요구사항을 검수한 뒤 ERD 초안을 만들 수 있습니다.",
    delivery_client_requirements_required: "ERD 초안에 반영할 고객 요구사항이 필요합니다. 검수 내용을 확인하세요.",
    delivery_erd_required: "먼저 요구사항 검수에서 ERD 초안을 만들어 주세요.",
    delivery_erd_stale: "요구사항이 변경되었습니다. 검수 내용을 확인하고 ERD 초안을 다시 만들어 주세요.",
    invalid_schema: "ERD 구조를 확인하지 못했습니다. 작업 이력을 확인한 뒤 다시 시도하세요.",
    invalid_erd_input: "ERD 생성에 필요한 검수 내용이 올바르지 않습니다. 요구사항과 답변을 다시 확인하세요.",
    codex_invalid_erd_output: "ERD 결과 형식이나 요구사항 연결 근거를 확인하지 못했습니다. 기존 초안은 유지했습니다. 작업 이력을 확인하세요.",
    delivery_answers_required: "필수 질문에 답변을 입력한 뒤 저장하고 요구사항 검수를 진행하세요.",
    delivery_revision_conflict: "검토 대상이 변경되었습니다. 메모는 보존했습니다. 최신 결과를 확인한 뒤 다시 저장하세요.",
    delivery_input_limit: "원문과 저장된 답변을 합친 정리 입력이 12,000자를 넘습니다. 답변 길이나 접수 범위를 조정해 주세요. 내용은 잘리지 않았습니다.",
    delivery_job_failed: "작업을 마치지 못했습니다. 담당자에게 연결과 작업 이력 확인을 요청하세요.",
    invalid_intervention: "개입 단계·이유·상황 내용을 확인해 주세요.",
    invalid_source: "고객 요구사항 원문을 입력해 주세요.",
    source_required: "고객 요구사항 원문부터 접수해 주세요.",
    project_not_found: "프로젝트를 찾지 못했습니다. 목록을 새로고침하세요.",
    not_found: "요청한 항목을 찾지 못했습니다. 페이지를 새로고침하세요.",
    codex_unavailable: "이 컴퓨터에서 Codex를 찾지 못했습니다. Codex 설치와 실행 상태를 확인하세요.",
    codex_not_authenticated: "본인 계정으로 Codex 로그인을 완료한 뒤 연결을 다시 확인하세요.",
    codex_login_required: "본인 계정으로 Codex 로그인을 완료한 뒤 연결을 다시 확인하세요.",
    codex_authentication_required: "본인 계정으로 Codex 로그인을 완료한 뒤 연결을 다시 확인하세요.",
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
    jev_not_configured: "요구사항 검토를 위한 서버 연결이 필요합니다. 담당자에게 연결 설정을 요청하세요.",
    jev_key_missing: "요구사항 검토를 위한 서버 연결이 필요합니다. 담당자에게 연결 설정을 요청하세요.",
    jev_auth_failed: "요구사항 검토 연결을 확인하지 못했습니다. 담당자에게 서버 연결 점검을 요청하세요.",
    jev_rate_limited: "요구사항 검토 이용 한도에 도달했습니다. 잠시 후 다시 요청하세요.",
    jev_unavailable: "요구사항 검토 서비스에 연결할 수 없습니다. 잠시 후 다시 요청하세요.",
    candidate_required: "내 Codex로 요구사항을 먼저 정리한 뒤 검토를 요청하세요.",
    service_not_configured: "서버 연결이 필요합니다. 담당자에게 서비스 연결 설정을 요청하세요.",
    service_rate_limited: "서비스 이용 한도에 도달했습니다. 잠시 후 다시 요청하세요.",
    service_busy: "서비스에서 다른 작업을 처리 중입니다. 잠시 후 다시 요청하세요.",
    service_unavailable: "서비스에 연결하지 못했습니다. 잠시 후 다시 요청하세요.",
    service_invalid_input: "입력 항목을 확인하고 다시 요청하세요.",
    rate_limited: "서비스 이용 한도에 도달했습니다. 잠시 후 다시 요청하세요.",
    unavailable: "서비스에 연결하지 못했습니다. 잠시 후 다시 요청하세요.",
    invalid_input: "입력 항목을 확인하고 다시 요청하세요.",
    reference_url_invalid: "수집할 수 있는 공개 페이지의 주소를 입력하세요.",
    reference_unavailable: "페이지를 가져오지 못했습니다. 주소와 공개 여부를 확인하세요.",
    reference_redirect_refused: "페이지 이동을 확인할 수 없어 가져오기를 중단했습니다. 최종 공개 페이지의 주소를 입력하세요.",
    reference_too_large: "페이지가 수집 한도를 초과했습니다. 범위가 작은 페이지를 선택하세요.",
    reference_empty: "페이지에서 참고할 내용을 찾지 못했습니다. 다른 공개 페이지를 선택하세요.",
    codex_connection_pending: "Codex 연결이 진행 중입니다. 공식 로그인 화면에서 인증을 완료하세요.",
    codex_connection_expired: "로그인 코드가 만료되었습니다. 내 Codex 연결을 다시 시작하세요.",
    codex_connection_not_found: "진행 중인 연결을 찾지 못했습니다. 내 Codex 연결을 다시 시작하세요.",
    codex_connection_failed: "Codex 연결을 완료하지 못했습니다. 잠시 후 다시 연결하세요.",
    project_busy: "이 프로젝트의 작업이 진행 중입니다. 결과를 기다린 뒤 다시 시도하세요.",
    busy: "작업이 진행 중입니다. 결과를 기다린 뒤 다시 시도하세요.",
    payload_too_large: "입력 내용이 너무 큽니다. 담당자와 접수 범위를 확인해 주세요.",
    operation_failed: "작업을 마치지 못했습니다. 연결 상태와 작업 이력을 확인한 뒤 다시 시도하세요.",
  };
  const running = (project) => Boolean(project && ["EXTRACTING", "REVIEWING", "RUNNING", "COLLECTING_REFERENCE", "DESIGNING_ERD"].includes(project.state));
  const text = (value) => typeof value === "string" ? value : "";
  const list = (value) => Array.isArray(value) ? value : [];
  function node(tag, className, value) { const el = document.createElement(tag); if (className) el.className = className; if (value !== undefined) el.textContent = String(value); return el; }
  function message(value, error = false) { $("message").textContent = value; $("message").classList.toggle("error", error); $("message").hidden = !value; }
  function date(value) { const time = new Date(value); return Number.isNaN(time.getTime()) ? "" : time.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }); }
  function safeError(code) { return errors[code] || "작업을 마치지 못했습니다. 연결 상태와 작업 이력을 확인해 주세요."; }
  function publicUrl(value) {
    try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
  }
  function officialLoginUrl(value) {
    try { const url = new URL(value); return url.protocol === "https:" && ["auth.openai.com", "chatgpt.com"].includes(url.hostname) && !url.username && !url.password && !url.port ? url.href : null; } catch { return null; }
  }
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
  function connectionMessage(value, error = false) {
    const target = $("codex-connection-message"); target.textContent = value; target.hidden = !value; target.classList.toggle("answer-conflict", error);
  }
  function renderConnection() {
    const codex = state.status?.codex || {};
    const member = state.status?.member_mode === true;
    const pending = state.connection?.state === "pending";
    const pendingElsewhere = codex.state === "pending" && !pending;
    const connected = codex.can_execute === true;
    const codexBusy = codex.state === "busy";
    $("codex-status").textContent = !state.status ? "연결 상태 확인 필요" : codexBusy ? "작업 중" : pending || pendingElsewhere ? "로그인 대기 중" : connected ? "연결됨" : codex.state === "expired" ? "로그인 만료" : codex.available === false ? "연결 준비 필요" : member ? "연결 필요" : "Codex 앱 로그인 필요";
    $("codex-status").title = connected || pending || codexBusy ? "" : safeError(codex.reason);
    $("codex-help").textContent = pendingElsewhere ? "이전에 시작한 연결이 있습니다. 연결을 초기화한 뒤 다시 시작할 수 있습니다." : member ? "회원 본인의 계정을 연결합니다. 요구사항 정리에는 내 계정 한도를 사용합니다." : "이 컴퓨터의 Codex 앱에서 본인 계정으로 로그인한 뒤 새로고침하세요.";
    $("codex-connection-actions").hidden = !member;
    $("connect-codex").hidden = connected || pending || pendingElsewhere || codexBusy;
    $("connect-codex").disabled = state.connectionBusy || codex.available === false;
    $("connect-codex").textContent = state.connectionBusy ? "연결 확인 중…" : "내 Codex 연결";
    $("disconnect-codex").hidden = (!connected && !pendingElsewhere) || pending || codexBusy;
    $("disconnect-codex").textContent = pendingElsewhere ? "연결 초기화" : "연결 해제";
    $("disconnect-codex").disabled = state.connectionBusy || state.pending || state.advancing || running(state.project);
    $("cancel-codex-login").disabled = state.connectionBusy;
    $("codex-login").hidden = !member || !pending;
    $("codex-login-code").textContent = pending ? text(state.connection.user_code) : "";
    $("codex-login-code").hidden = !$("codex-login-code").textContent;
    const loginUrl = pending ? officialLoginUrl(state.connection.verification_url) : null;
    if (loginUrl) $("codex-login-link").href = loginUrl; else $("codex-login-link").removeAttribute("href");
    $("codex-login-link").hidden = !loginUrl;
    $("codex-login-instruction").textContent = loginUrl ? "공식 로그인 화면에서 아래 코드를 입력하세요." : "공식 로그인 정보를 준비하고 있습니다.";
    $("codex-login-help").textContent = loginUrl ? "로그인을 마치면 연결 상태가 자동으로 갱신됩니다. 코드는 다른 사람에게 공유하지 마세요." : pending && state.connection.verification_url ? "공식 로그인 주소를 확인하지 못했습니다. 연결을 취소한 뒤 다시 시도하세요." : "잠시 기다리면 로그인 주소와 코드가 표시됩니다.";
  }
  function stopConnectionPoll() { clearTimeout(state.connectionTimer); state.connectionTimer = null; }
  function scheduleConnectionPoll() {
    stopConnectionPoll();
    if (document.hidden || state.connection?.state !== "pending" || !state.connection.connection_id) return;
    state.connectionTimer = setTimeout(pollConnection, 3000);
  }
  async function refreshCodexConnection() {
    if (state.status?.member_mode !== true) return;
    const result = await api("/api/connections/codex"); if (state.status?.member_mode === true) state.status.codex = result.codex || {};
  }
  async function acceptConnection(result) {
    const connection = result.connection;
    if (!connection || typeof connection.state !== "string") throw new Error("연결 응답을 확인하지 못했습니다. 연결 상태를 새로고침하세요.");
    state.connection = connection;
    if (connection.state === "pending") {
      if (!text(connection.connection_id)) throw new Error("연결 응답을 확인하지 못했습니다. 연결 상태를 새로고침하세요.");
      connectionMessage("");
    } else {
      stopConnectionPoll();
      state.connection = null;
      await refreshCodexConnection();
      const notices = { connected: "내 Codex가 연결되었습니다.", cancelled: "연결을 취소했습니다.", disconnected: "연결을 취소했습니다.", expired: errors.codex_connection_expired, failed: safeError(connection.reason || "codex_connection_failed") };
      connectionMessage(notices[connection.state] || "연결 상태를 확인하세요.", ["failed", "expired"].includes(connection.state));
    }
    renderConnection(); controls(); scheduleConnectionPoll();
  }
  async function pollConnection() {
    if (document.hidden || state.connection?.state !== "pending" || !state.connection.connection_id) return;
    const sequence = state.connectionSequence;
    const connectionId = state.connection.connection_id;
    try {
      const result = await api("/api/connections/codex/poll", { connection_id: connectionId });
      if (sequence !== state.connectionSequence || document.hidden) return;
      await acceptConnection(result);
    } catch (error) {
      if (sequence !== state.connectionSequence) return;
      connectionMessage(error.message, true);
      if (["login_required", "invalid_token", "codex_connection_expired", "codex_connection_not_found"].includes(error.code)) { state.connection = null; renderConnection(); return; }
    } finally { if (sequence === state.connectionSequence) scheduleConnectionPoll(); }
  }
  async function connectionAction(action) {
    if (state.connectionBusy || state.status?.member_mode !== true) return;
    const connectionId = state.connection?.connection_id;
    if (action === "cancel" && !connectionId) return;
    stopConnectionPoll(); ++state.connectionSequence; state.connectionBusy = true; connectionMessage(""); renderConnection(); controls();
    try {
      const result = await api(`/api/connections/codex/${action}`, action === "cancel" ? { connection_id: connectionId } : {});
      if (action === "disconnect") { state.connection = null; state.status.codex = result.codex || {}; connectionMessage("내 Codex 연결을 해제했습니다."); }
      else await acceptConnection(result);
    } catch (error) { connectionMessage(error.message, true); }
    finally { state.connectionBusy = false; renderConnection(); controls(); scheduleConnectionPoll(); }
  }
  function mcpMessage(value, error = false) {
    const target = $("mcp-message"); target.textContent = value; target.hidden = !value; target.classList.toggle("answer-conflict", error);
  }
  function clearMcpToken() {
    state.mcpToken = ""; state.mcpDisplayAllowed = false; $("mcp-token").value = ""; $("mcp-token").type = "password";
    $("mcp-token-panel").hidden = true; $("reveal-mcp-token").textContent = "키 보기"; $("reveal-mcp-token").setAttribute("aria-pressed", "false");
  }
  function renderMcpConnection() {
    const member = state.status?.member_mode === true;
    $("mcp-connection-panel").hidden = !member;
    if (!member) clearMcpToken();
    const connected = state.mcpConnection?.connected === true;
    const expiry = state.mcpConnection?.expires_at;
    $("mcp-status").textContent = state.mcpConnection ? connected ? "연결 키 발급됨" : "연결 안 됨" : "연결 상태 확인 필요";
    $("mcp-status").title = connected && typeof expiry === "number" ? `${date(expiry * 1000)} 만료` : "";
    $("create-mcp-token").disabled = state.mcpBusy;
    $("create-mcp-token").textContent = state.mcpBusy ? "처리 중…" : "연결 키 발급";
    $("revoke-mcp-token").hidden = !connected;
    $("revoke-mcp-token").disabled = state.mcpBusy;
    $("mcp-token").value = state.mcpToken;
    $("mcp-token-panel").hidden = !state.mcpToken;
    ["reveal-mcp-token", "copy-mcp-token", "save-mcp-token"].forEach((id) => { $(id).disabled = state.mcpBusy || !state.mcpToken; });
    $("mcp-config-example").textContent = `CHANNELSHIFT_SERVICE_URL=${window.location.origin}\nCHANNELSHIFT_SERVICE_TOKEN_FILE=저장한/channelshift-service-token.txt의/전체/경로`;
  }
  async function refreshMcpConnection() {
    if (state.status?.member_mode !== true || state.mcpBusy) return;
    const sequence = ++state.mcpSequence; state.mcpBusy = true; clearMcpToken(); mcpMessage(""); renderMcpConnection();
    try {
      const result = await api("/api/connections/mcp");
      if (sequence === state.mcpSequence) state.mcpConnection = result.connection || null;
    } catch (error) { if (sequence === state.mcpSequence) { state.mcpConnection = null; mcpMessage(error.message, true); } }
    finally { if (sequence === state.mcpSequence) { state.mcpBusy = false; renderMcpConnection(); } }
  }
  async function mcpAction(action) {
    if (state.mcpBusy || state.status?.member_mode !== true) return;
    const sequence = ++state.mcpSequence; state.mcpBusy = true; clearMcpToken(); mcpMessage(""); renderMcpConnection();
    state.mcpDisplayAllowed = !document.hidden && $("mcp-connection-panel").open;
    try {
      const result = await api(`/api/connections/mcp/${action}`, {});
      if (sequence !== state.mcpSequence) return;
      if (action === "create") {
        const token = text(result.connection?.token);
        if (!token || token.length > 4096 || /\s/.test(token)) throw new Error("연결 키 응답을 확인하지 못했습니다. 연결 상태를 새로고침하세요.");
        state.mcpConnection = { connected: true, expires_at: result.connection.expires_at };
        if (state.mcpDisplayAllowed && !document.hidden && $("mcp-connection-panel").open) {
          state.mcpToken = token; mcpMessage("연결 키를 발급했습니다. 키 파일을 저장해 내 AI 도구에 연결하세요.");
        } else mcpMessage("연결 키를 발급했지만 화면이 닫혀 표시를 지웠습니다. 키가 필요하면 다시 발급하세요.");
      } else {
        state.mcpConnection = result.connection || { connected: false, expires_at: null }; mcpMessage("MCP 연결을 해제했습니다.");
      }
    } catch (error) { if (sequence === state.mcpSequence) mcpMessage(error.message, true); }
    finally { if (sequence === state.mcpSequence) { state.mcpBusy = false; renderMcpConnection(); } }
  }
  function answerKey(project, saved) { return `${project.id}:${project.candidate_revision}:${saved.question_digest}`; }
  function nextStepProblem(project) {
    if (!project?.candidate) return "요구사항 초안을 먼저 정리하세요.";
    if (running(project)) return "진행 중인 작업이 끝나면 요구사항을 검수할 수 있습니다.";
    if ([...state.answerDrafts.values()].some((draft) => draft.projectId === project.id && draft.candidateRevision !== project.candidate_revision)) return "답변 이력에서 이전 초안의 미저장 답변을 확인하고 사본을 정리하세요.";
    let missing = 0;
    for (const saved of list(project.question_answers)) {
      const draft = state.answerDrafts.get(answerKey(project, saved));
      if (draft && draft.expectedRevision !== saved.answer_revision) return "변경된 질문의 현재 답변을 확인한 뒤 다시 진행하세요. 미저장 입력은 보존했습니다.";
      const value = draft ? draft.answer : saved.answer;
      if (Array.from(value).length > 2000) return "답변은 질문마다 2,000자까지 저장할 수 있습니다.";
      if (saved.blocking && !value.trim()) missing += 1;
    }
    return missing ? `필수 질문 ${missing}개에 답변을 입력하세요.` : "";
  }
  function controls() {
    const pending = state.pending || state.advancing;
    const busy = pending || running(state.project);
    $("create-project").disabled = pending;
    $("extract").disabled = busy || state.connectionBusy || !state.project || state.status?.codex?.can_execute !== true;
    $("review-jev").disabled = busy || !state.project?.candidate || state.status?.jev_configured !== true;
    $("collect-reference").disabled = busy || !state.project || state.status?.services?.reference_collect?.available !== true;
    $("collect-reference").textContent = state.project?.state === "COLLECTING_REFERENCE" ? "가져오는 중…" : "자료 가져오기";
    $("reference-url").disabled = busy;
    $("save-intervention").disabled = pending || !state.project;
    $("save-consent").disabled = pending || !state.project;
    $("consent-rebase").disabled = pending;
    ["consent-mode", "consent-operator", "consent-reason"].forEach((id) => { $(id).disabled = pending; });
    $("new-project").disabled = pending;
    document.querySelectorAll(".answer-save, .answer-rebase, .answer-discard").forEach((button) => { button.disabled = busy; });
    document.querySelectorAll(".answer-form textarea").forEach((input) => { input.disabled = busy; });
    const nextProblem = nextStepProblem(state.project);
    $("next-step").disabled = busy || Boolean(nextProblem) || state.project?.workflow_stage === "requirements_review";
    $("next-step").textContent = state.advancing ? "저장하고 이동 중…" : "저장하고 요구사항 검수";
    $("next-step-help").textContent = state.advancing ? "답변을 저장하고 검수 화면을 준비하고 있습니다." : nextProblem || "작성한 답변을 모두 저장하고 요구사항 검수로 이동합니다.";
    $("back-to-intake").disabled = busy || state.project?.workflow_stage !== "requirements_review";
    $("create-erd").disabled = busy || state.connectionBusy || !state.project?.requirements_review || state.project.workflow_stage !== "requirements_review" || state.status?.codex?.can_execute !== true;
    $("erd-database").disabled = busy;
    $("create-erd").textContent = state.project?.state === "DESIGNING_ERD" ? "초안 만드는 중…" : state.project?.erd_draft ? "ERD 초안 다시 만들기" : "ERD 초안 만들기";
    $("erd-help").textContent = state.project?.state === "DESIGNING_ERD" ? "내 Codex가 저장된 검수 내용을 바탕으로 초안을 만들고 있습니다." : state.status?.codex?.state === "busy" ? "내 Codex의 작업이 끝나면 초안을 만들 수 있습니다." : state.status?.codex?.can_execute !== true ? "내 Codex를 연결한 뒤 초안을 만들 수 있습니다." : state.project?.erd_draft ? "다시 만들면 현재 편집 초안이 새 결과로 바뀝니다." : "버튼을 누르면 초안 생성을 시작합니다.";
    $("extract").textContent = state.project?.state === "EXTRACTING" ? "정리 중…" : "Codex로 정리";
    $("review-jev").textContent = state.project?.state === "REVIEWING" ? "검토 중…" : "요구사항 검토";
    renderConnection();
    renderMcpConnection();
  }
  async function refreshStatus() {
    $("refresh-status").disabled = true;
    try {
      state.status = await api("/api/delivery/status");
      if (state.status.member_mode === true) await refreshCodexConnection();
      if (state.status.member_mode === true) await refreshMcpConnection();
      if (Array.isArray(state.status.stages) && state.status.stages.length) {
        const selectedStage = $("stage-id").value;
        $("stage-id").replaceChildren(...state.status.stages.map((stage) => {
          const option = node("option", null, text(stage.title).replace(/^[GS]\d[A-Z]?\s+/, "")); option.value = stage.id; return option;
        }));
        if (state.status.stages.some((stage) => stage.id === selectedStage)) $("stage-id").value = selectedStage;
        if (state.project) renderHistory(state.project);
      }
      renderConnection();
      $("jev-status").textContent = state.status.jev_configured === true ? "사용 가능" : "서버 연결 필요";
      $("references-status").textContent = state.status.services?.reference_collect?.available === true ? "사용 가능" : "서버 연결 필요";
    } catch (error) { state.status = null; $("codex-status").textContent = "확인 실패"; $("jev-status").textContent = "확인 실패"; $("references-status").textContent = "확인 실패"; message(error.message, true); }
    finally { $("refresh-status").disabled = false; controls(); }
  }
  function renderList() {
    const target = $("project-list"); target.replaceChildren();
    $("project-list-status").textContent = state.projects.length ? `${state.projects.length}개` : "프로젝트 없음";
    state.projects.forEach((project) => {
      const item = node("button", "project-item"); item.type = "button"; item.setAttribute("aria-current", String(project.id === state.project?.id));
      item.append(node("strong", null, text(project.name) || "이름 없는 프로젝트"), node("small", null, [labels[project.state] || "상태 확인 필요", date(project.created_at)].filter(Boolean).join(" · ")));
      item.addEventListener("click", () => { if (!state.pending && !state.advancing) openProject(project.id); }); target.append(item);
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
    target.append(node("p", "muted small", project.jev.status === "completed" || project.jev.status === "COMPLETE" ? "요구사항 검토 결과" : "요구사항 검토 기록"));
    const judgments = { supported: "입력 근거에서 확인됨", unsupported: "입력 근거를 찾지 못함", contradicted: "입력 근거와 모순될 수 있음", unclear: "추가 확인 필요" };
    list(project.jev.items).forEach((item) => { const row = node("article", "result-item"); row.append(node("strong", null, text(item.requirement_id) || "요구사항 확인"), node("p", null, judgments[item.judgment] || "판정 내용 확인 필요")); if (typeof item.confidence === "number" && Number.isFinite(item.confidence)) row.append(node("p", "muted small", `신뢰도 ${item.confidence}`)); target.append(row); });
    if (!list(project.jev.items).length) target.append(node("p", "empty-result", "결과 없음"));
  }
  function renderReferences(project) {
    const references = list(project.references); const target = $("reference-results"); target.replaceChildren();
    $("reference-count").textContent = `${references.length}개`;
    $("reference-url").value = state.referenceDrafts.get(project.id) || "";
    references.slice().reverse().forEach((item) => {
      const row = node("article", "result-item reference-item");
      row.append(node("h3", null, text(item.title) || "수집한 참고 자료"));
      const url = publicUrl(item.url);
      if (url) { const link = node("a", "reference-link", url); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; row.append(link); }
      row.append(node("p", "muted small", [date(item.collected_at), "참고용 · 요구사항 확정 전"].filter(Boolean).join(" · ")));
      if (text(item.text)) { const details = node("details", "reference-text"); details.append(node("summary", null, "수집 내용 보기"), node("p", null, text(item.text))); row.append(details); }
      target.append(row);
    });
    if (!references.length) target.append(node("p", "empty-result", "저장된 참고 자료가 없습니다."));
  }
  function renderQuestions(project) {
    const target = $("questions"); target.replaceChildren();
    const summary = project.answer_summary || {};
    $("answer-summary").textContent = `답변 ${summary.answered || 0}/${summary.total || 0} · 필수 미답변 ${summary.blocking_unanswered || 0}`;
    const questions = list(project.candidate?.questions);
    if (!questions.length) target.append(node("p", "empty-result", "없음"));
    questions.forEach((question, index) => {
      const saved = list(project.question_answers).find((item) => item.question_id === question.id);
      if (!saved) return;
      const key = answerKey(project, saved);
      const draft = state.answerDrafts.get(key);
      const conflict = Boolean(draft && draft.expectedRevision !== saved.answer_revision);
      const row = node("article", "result-item answer-item");
      row.append(node("strong", null, `${question.id} · ${saved.answered ? "답변 저장됨" : question.blocking ? "필수 · 미답변" : "미답변"}`), node("p", null, text(question.text)));
      const form = node("form", "answer-form");
      const input = node("textarea"); input.id = `question-answer-${index}`; input.rows = 3;
      input.value = draft ? draft.answer : saved.answer; input.dataset.questionId = question.id;
      const label = node("label", null, "답변"); label.htmlFor = input.id;
      const length = node("small", "muted", `${Array.from(input.value).length}/2,000자`);
      input.addEventListener("input", () => {
        const prior = state.answerDrafts.get(key);
        state.answerDrafts.set(key, { projectId: project.id, candidateRevision: project.candidate_revision,
          questionText: question.text, questionId: question.id, answer: input.value,
          expectedRevision: prior?.expectedRevision || saved.answer_revision });
        length.textContent = `${Array.from(input.value).length}/2,000자 · 미저장`;
        controls();
      });
      const save = node("button", "button secondary answer-save", saved.answered ? "답변 수정 저장" : "답변 저장"); save.type = "submit";
      form.append(label, input, length);
      if (conflict) {
        form.append(node("p", "answer-conflict", "다른 탭에서 답변이 변경되었습니다. 입력한 답변은 보존했습니다."),
          node("p", "saved-answer", `현재 저장된 답변: ${saved.answer || "(비어 있음)"}`));
        const rebase = node("button", "text-button answer-rebase", "현재 답변 확인함"); rebase.type = "button";
        rebase.addEventListener("click", () => { state.answerDrafts.get(key).expectedRevision = saved.answer_revision; renderQuestions(state.project); controls(); });
        form.append(rebase);
      }
      form.append(save);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (state.pending || state.advancing || running(state.project)) return;
        await saveAnswer(project, saved, input.value);
      });
      row.append(form); target.append(row);
    });
    const history = $("answer-history"); history.replaceChildren();
    list(project.answer_history).slice().reverse().forEach((answer) => {
      const row = node("article", "history-item");
      row.append(node("strong", null, `${answer.question_id} · ${answer.candidate_revision === project.candidate_revision ? "현재 후보" : "이전 후보"}`),
        node("small", null, date(answer.created_at)), node("p", null, answer.question_text), node("p", null, answer.answer || "답변 비움"));
      history.append(row);
    });
    for (const [key, draft] of state.answerDrafts) {
      if (draft.projectId === project.id && draft.candidateRevision !== project.candidate_revision) {
        const row = node("article", "history-item answer-conflict");
        row.append(node("strong", null, `${draft.questionId} · 이전 후보의 미저장 답변`), node("p", null, draft.questionText));
        const preserved = node("textarea"); preserved.readOnly = true; preserved.value = draft.answer;
        preserved.setAttribute("aria-label", `${draft.questionId} 이전 후보의 미저장 답변`); row.append(preserved);
        const discard = node("button", "text-button answer-discard", "이전 답변 사본 버리기"); discard.type = "button";
        discard.addEventListener("click", () => { if (state.pending || state.advancing) return; state.answerDrafts.delete(key); renderQuestions(state.project); controls(); });
        row.append(discard); history.append(row);
      }
    }
    if (!history.children.length) history.append(node("p", "empty-result", "기록 없음"));
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
      const operationNames = { jev: "요구사항 검토 시작", extract: "Codex 정리 시작", codex: "Codex 정리 시작", reference: "참고 자료 수집 시작", reference_collect: "참고 자료 수집 시작" };
      const eventNames = { source_registered: "고객 원문 접수", job_started: operationNames[item.payload?.operation] || "작업 시작", candidate_recorded: "요구사항 초안 저장", advice_recorded: "요구사항 검토 의견 저장", reference_recorded: "참고 자료 저장", reference_failed: "참고 자료 수집 실패 · 확인 필요", job_failed: "작업 실패 · 확인 필요", human_intervention: "사람 개입 기록", question_answered: "질문 답변 저장", consent_policy_recorded: "동의 화면 설정 저장", requirements_review_requested: "요구사항 검수로 이동", intake_reopened: "접수로 돌아감" };
      const erdEventNames = { erd_started: "ERD 초안 생성 시작", erd_recorded: "ERD 초안 저장", erd_edited: "ERD 편집 저장", erd_failed: "ERD 초안 생성 실패 · 확인 필요" };
      row.append(node("p", null, eventNames[kind] || erdEventNames[kind] || "작업 상태 기록"), node("small", null, date(item.created_at || item.at)));
      const code = item.payload?.code || item.error; if (code) row.append(node("p", null, safeError(code))); events.append(row);
    });
    if (!list(project.events).length) events.append(node("p", "empty-result", "기록 없음"));
  }
  function renderConsent(project) {
    const saved = project.consent_policy || { mode: "undecided", operator_label: "", reason: "" };
    const draft = state.consentDrafts.get(project.id);
    const form = draft || saved;
    $("consent-current").textContent = consentModes[saved.mode] || "미결정";
    $("consent-mode").value = form.mode;
    $("consent-operator").value = form.operator_label;
    $("consent-reason").value = form.reason;
    $("consent-conflict").hidden = !draft || draft.expectedRevision === project.consent_revision;
    $("consent-latest").textContent = `${consentModes[saved.mode]} · ${saved.operator_label || "선택자 없음"}\n${saved.reason}`;
    const history = $("consent-history"); history.replaceChildren();
    list(project.consent_history).slice().reverse().forEach((item) => {
      const row = node("article", "history-item");
      row.append(node("strong", null, `${consentModes[item.mode]} · ${item.operator_label}`),
        node("small", null, date(item.created_at)), node("p", null, item.reason));
      history.append(row);
    });
    if (!history.children.length) history.append(node("p", "empty-result", "기록 없음"));
  }
  function renderRequirementsReview(project) {
    const snapshot = project.requirements_review;
    if (!snapshot) return;
    $("review-saved-at").textContent = `${date(snapshot.created_at)} 저장한 내용`;
    const fromClient = (item) => ["client", "customer", "client_original"].includes(item.origin);
    const requirements = list(snapshot.candidate?.requirements);
    resultList("review-client-requirements", requirements.filter(fromClient), "client");
    resultList("review-internal-requirements", requirements.filter((item) => !fromClient(item)), "internal");
    resultList("review-out-of-scope", list(snapshot.candidate?.out_of_scope), "scope");
    const target = $("review-answers"); target.replaceChildren();
    list(snapshot.answers).forEach((answer) => {
      const row = node("article", "result-item");
      row.append(node("strong", null, answer.question_id), node("p", null, answer.question_text),
        node("p", "saved-answer", answer.answer || "선택 질문 · 답변 없음"));
      target.append(row);
    });
    if (!target.children.length) target.append(node("p", "empty-result", "추가 질문 없음"));
    const draft = project.erd_draft; const schema = draft?.result?.schema;
    const database = state.erdDatabaseDrafts.get(project.id) || draft?.database || schema?.database || "postgresql";
    $("erd-database").value = ["postgresql", "mysql", "sqlite"].includes(database) ? database : "postgresql";
    $("erd-draft-status").textContent = project.state === "DESIGNING_ERD" ? "생성 중" : schema ? `테이블 ${list(schema.entities).length}개 · 관계 ${list(schema.relations).length}개` : "생성 전";
    $("erd-stale").hidden = !draft || project.erd_current === true;
    $("erd-no-data").hidden = !schema || list(schema.entities).length !== 0;
    $("erd-design-details").hidden = !draft;
    $("erd-trace-status").textContent = draft?.traceability_current === false ? "수동 편집 전 생성 근거입니다. 현재 테이블·필드와의 연결을 다시 확인하세요." : "초안 생성 당시의 요구사항 연결 근거입니다.";
    const evidence = $("erd-design-evidence"); evidence.replaceChildren();
    list(draft?.result?.traceability).forEach((item) => { const row = node("article", "result-item"); row.append(node("strong", null, text(item.entity)), node("p", null, list(item.requirement_ids).filter((id) => typeof id === "string").join(" · "))); evidence.append(row); });
    list(draft?.result?.unmapped_requirements).forEach((item) => { const row = node("article", "result-item"); row.append(node("strong", null, `${text(item.requirement_id)} · DB 미반영`), node("p", null, text(item.reason))); evidence.append(row); });
    list(draft?.result?.notes).forEach((note) => { const row = node("article", "result-item"); row.append(node("p", null, text(note))); evidence.append(row); });
    if (draft && !evidence.children.length) evidence.append(node("p", "empty-result", "추가 설계 근거 없음"));
    $("open-erd").hidden = !schema || project.erd_current !== true || running(project);
    if (schema && project.erd_current === true) $("open-erd").href = `/?delivery=${encodeURIComponent(project.id)}`; else $("open-erd").removeAttribute("href");
  }
  function projectUrl(id) {
    const url = new URL(window.location.href); if (id) url.searchParams.set("project", id); else url.searchParams.delete("project");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
  function renderProject() {
    const project = state.project; $("intake-panel").hidden = Boolean(project); $("project-panel").hidden = !project; renderList(); controls();
    if (!project) return;
    const inReview = project.workflow_stage === "requirements_review";
    $("selected-name").textContent = text(project.name); $("project-state").textContent = inReview ? "요구사항 검수" : labels[project.state] || "상태 확인 필요";
    $("source-text").textContent = text(project.source?.text);
    $("candidate-panel").hidden = !project.candidate || inReview;
    $("intake-extraction").hidden = inReview;
    $("requirements-review-panel").hidden = !inReview;
    if (project.candidate) { const requirements = list(project.candidate.requirements); const fromClient = (item) => ["client", "customer", "client_original"].includes(item.origin); resultList("client-requirements", requirements.filter(fromClient), "client"); resultList("internal-requirements", requirements.filter((item) => !fromClient(item)), "internal"); renderQuestions(project); resultList("out-of-scope", list(project.candidate.out_of_scope), "scope"); renderJev(project); }
    renderHistory(project);
    renderReferences(project);
    renderConsent(project);
    if (inReview) renderRequirementsReview(project);
    controls();
  }
  function schedulePoll() {
    clearTimeout(state.timer); if (!running(state.project)) return;
    const id = state.project.id; const sequence = state.sequence;
    state.timer = setTimeout(async () => { try { const result = await api(`/api/delivery/projects/${encodeURIComponent(id)}`); if (sequence !== state.sequence || state.project?.id !== id) return; state.project = result.project; renderProject(); if (!running(state.project)) { message(labels[state.project.state] || "상태 변경됨"); await refreshProjects(); } schedulePoll(); } catch (error) { if (sequence === state.sequence) message(error.message, true); } }, 2500);
  }
  async function openProject(id) {
    if (state.pending || state.advancing) return; state.pending = true; controls();
    clearTimeout(state.timer); const sequence = ++state.sequence; message("");
    try { const result = await api(`/api/delivery/projects/${encodeURIComponent(id)}`); if (sequence !== state.sequence) return; state.project = result.project; projectUrl(state.project.id); state.interventionRevision = null; $("intervention-form").reset(); renderProject(); schedulePoll(); } catch (error) { if (sequence === state.sequence) message(error.message, true); }
    finally { state.pending = false; controls(); }
  }
  async function mutation(path, body, success, onSuccess = null) {
    if (state.pending) return false; state.pending = true; controls(); message("");
    try { const result = await api(path, body); if (!result.project || typeof result.project.id !== "string") throw new Error("프로젝트 응답을 읽지 못했습니다. 목록을 새로고침해 저장 여부를 확인하세요."); state.project = result.project; projectUrl(state.project.id); if (onSuccess) onSuccess(); ++state.sequence; renderProject(); schedulePoll(); message(success); await refreshProjects(); return true; }
    catch (error) {
      let detail = error.message;
      if (error.status === 409 && path === "/api/delivery/answer") detail = "질문이나 저장된 답변이 변경되었습니다. 작성 중인 답변은 보존했습니다. 최신 내용을 확인해 주세요.";
      if (error.status === 409 && path === "/api/delivery/consent") detail = "저장된 설정이 변경되었습니다. 작성 중인 내용은 보존했습니다. 최신 설정을 확인해 주세요.";
      if (error.status === 409 && path === "/api/delivery/references") detail = "프로젝트가 변경되어 자료 수집을 시작하지 않았습니다. 입력한 주소는 보존했습니다. 최신 내용을 확인한 뒤 다시 요청하세요.";
      if (error.status === 409 && ["/api/delivery/next", "/api/delivery/back"].includes(path)) detail = "요구사항·답변 또는 진행 단계가 변경되어 이동하지 않았습니다. 최신 내용을 확인하고 다시 진행하세요. 미저장 입력은 보존했습니다.";
      message(detail, true);
      if (error.status === 409 && state.project) { const id = state.project.id; try { const result = await api(`/api/delivery/projects/${encodeURIComponent(id)}`); state.project = result.project; if (path === "/api/delivery/intervention" && error.code === "delivery_revision_conflict") state.interventionRevision = state.project.intervention_revision; renderProject(); schedulePoll(); } catch {} }
      return false;
    }
    finally { state.pending = false; controls(); }
  }
  async function saveAnswer(project, saved, answer, bindContext = false) {
    const key = answerKey(project, saved);
    const draft = state.answerDrafts.get(key);
    if (Array.from(answer).length > 2000) { message("답변은 2,000자까지 저장할 수 있습니다. 내용을 줄여 주세요.", true); return false; }
    if (draft && draft.expectedRevision !== saved.answer_revision) { message("현재 저장된 답변을 확인한 뒤 다시 저장하세요. 입력한 답변은 보존했습니다.", true); return false; }
    const body = { project_id: project.id, candidate_revision: project.candidate_revision,
      question_id: saved.question_id, question_digest: saved.question_digest, answer,
      expected_revision: draft?.expectedRevision || saved.answer_revision };
    if (bindContext) body.expected_context_revision = project.intervention_revision;
    return mutation("/api/delivery/answer", body, "답변 저장됨 · 다시 정리할 때 반영",
      () => state.answerDrafts.delete(key));
  }
  async function saveAndReview() {
    if (state.pending || state.advancing || !state.project) return;
    const problem = nextStepProblem(state.project);
    if (problem) { message(problem, true); return; }
    const projectId = state.project.id; const candidateRevision = state.project.candidate_revision;
    const questionDigests = list(state.project.question_answers).map((saved) => saved.question_digest);
    state.advancing = true; controls();
    try {
      for (const digest of questionDigests) {
        if (state.project.id !== projectId || state.project.candidate_revision !== candidateRevision) {
          message("저장 중 요구사항 초안이 변경되어 이동하지 않았습니다. 보존된 답변과 최신 질문을 확인하세요.", true); return;
        }
        const saved = state.project.question_answers.find((item) => item.question_digest === digest);
        const draft = state.answerDrafts.get(answerKey(state.project, saved));
        if (draft && !await saveAnswer(state.project, saved, draft.answer, true)) return;
      }
      if (state.project.id !== projectId || state.project.candidate_revision !== candidateRevision) {
        message("요구사항 초안이 변경되어 이동하지 않았습니다. 최신 내용을 확인하세요.", true); return;
      }
      const remaining = nextStepProblem(state.project);
      if (remaining) { message(remaining, true); return; }
      if (await mutation("/api/delivery/next", { project_id: projectId, expected_revision: state.project.intervention_revision }, "답변을 저장하고 요구사항 검수로 이동했습니다.")) {
        $("requirements-review-title").focus();
        $("requirements-review-panel").scrollIntoView({ block: "start" });
      }
    } finally { state.advancing = false; controls(); }
  }
  $("next-step").addEventListener("click", saveAndReview);
  $("back-to-intake").addEventListener("click", async () => {
    if (state.pending || state.advancing || !state.project) return;
    if (await mutation("/api/delivery/back", { project_id: state.project.id, expected_revision: state.project.intervention_revision }, "접수로 돌아왔습니다. 요구사항과 답변을 수정할 수 있습니다.")) {
      $("candidate-panel").scrollIntoView({ block: "start" });
      $("next-step").focus();
    }
  });
  $("intake-form").addEventListener("submit", async (event) => { event.preventDefault(); const name = $("project-name").value.trim(); const request = $("client-request").value; if (!name || !request.trim()) { message("프로젝트명과 원문을 입력하세요.", true); return; } if (Array.from(request).length > 12000) { message("원문은 12,000자까지 저장할 수 있습니다. 내용을 줄여 주세요.", true); return; } if (await mutation("/api/delivery/projects", { name, client_request: request }, "원문 저장됨")) $("intake-form").reset(); });
  $("extract").addEventListener("click", () => { if (state.project && !$("extract").disabled) mutation("/api/delivery/extract", { project_id: state.project.id }, "Codex 정리 요청됨"); });
  $("review-jev").addEventListener("click", () => { if (state.project && !$("review-jev").disabled) mutation("/api/delivery/jev", { project_id: state.project.id }, "요구사항 검토 요청됨"); });
  $("create-erd").addEventListener("click", () => {
    if (!state.project || $("create-erd").disabled) return;
    mutation("/api/delivery/erd", { project_id: state.project.id, database: $("erd-database").value, expected_revision: state.project.intervention_revision }, "ERD 초안 생성을 요청했습니다.");
  });
  $("erd-database").addEventListener("change", () => { if (state.project) state.erdDatabaseDrafts.set(state.project.id, $("erd-database").value); });
  $("connect-codex").addEventListener("click", () => connectionAction("start"));
  $("cancel-codex-login").addEventListener("click", () => connectionAction("cancel"));
  $("disconnect-codex").addEventListener("click", () => connectionAction("disconnect"));
  $("create-mcp-token").addEventListener("click", () => mcpAction("create"));
  $("revoke-mcp-token").addEventListener("click", () => mcpAction("revoke"));
  $("mcp-connection-panel").addEventListener("toggle", () => { if (!$("mcp-connection-panel").open) clearMcpToken(); });
  $("reveal-mcp-token").addEventListener("click", () => {
    if (!state.mcpToken || state.mcpBusy) return;
    const visible = $("mcp-token").type === "password"; $("mcp-token").type = visible ? "text" : "password";
    $("reveal-mcp-token").textContent = visible ? "키 숨기기" : "키 보기"; $("reveal-mcp-token").setAttribute("aria-pressed", String(visible));
  });
  $("copy-mcp-token").addEventListener("click", async () => {
    if (!state.mcpToken || state.mcpBusy) return;
    try { await navigator.clipboard.writeText(state.mcpToken); mcpMessage("연결 키를 복사했습니다."); }
    catch { mcpMessage("복사하지 못했습니다. 키 파일 저장을 사용하세요.", true); }
  });
  $("save-mcp-token").addEventListener("click", () => {
    if (!state.mcpToken || state.mcpBusy) return;
    const url = URL.createObjectURL(new Blob([state.mcpToken], { type: "text/plain;charset=utf-8" }));
    const link = node("a"); link.href = url; link.download = "channelshift-service-token.txt"; link.hidden = true;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    mcpMessage("키 파일 저장을 요청했습니다. 저장한 파일의 전체 경로를 MCP 설정에 넣으세요.");
  });
  $("reference-url").addEventListener("input", () => { if (state.project) state.referenceDrafts.set(state.project.id, $("reference-url").value); });
  $("reference-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (!state.project || $("collect-reference").disabled) return;
    const projectId = state.project.id; const url = $("reference-url").value.trim();
    state.referenceDrafts.set(projectId, url);
    if (!publicUrl(url) || url.length > 2000) { message(errors.reference_url_invalid, true); return; }
    await mutation("/api/delivery/references", { project_id: projectId, url, expected_revision: state.project.intervention_revision }, "참고 자료 수집을 요청했습니다.", () => state.referenceDrafts.delete(projectId));
  });
  function rememberConsentDraft() {
    if (!state.project) return;
    const prior = state.consentDrafts.get(state.project.id);
    state.consentDrafts.set(state.project.id, { mode: $("consent-mode").value, operator_label: $("consent-operator").value,
      reason: $("consent-reason").value, expectedRevision: prior?.expectedRevision || state.project.consent_revision });
  }
  $("consent-form").addEventListener("input", rememberConsentDraft);
  $("consent-form").addEventListener("change", rememberConsentDraft);
  $("consent-rebase").addEventListener("click", () => {
    if (!state.project || state.pending) return;
    const draft = state.consentDrafts.get(state.project.id);
    if (draft) draft.expectedRevision = state.project.consent_revision;
    renderConsent(state.project);
  });
  $("consent-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (!state.project || state.pending) return;
    rememberConsentDraft(); const id = state.project.id; const draft = state.consentDrafts.get(id);
    if (!draft.operator_label.trim() || !draft.reason.trim() || Array.from(draft.operator_label).length > 100 || Array.from(draft.reason).length > 2000) {
      message("선택자 표기(100자 이내)와 선택 이유(2,000자 이내)를 입력하세요.", true); return;
    }
    if (draft.expectedRevision !== state.project.consent_revision) { message("현재 저장된 설정을 확인한 뒤 다시 저장하세요. 입력 내용은 보존했습니다.", true); return; }
    await mutation("/api/delivery/consent", { project_id: id, mode: draft.mode, operator_label: draft.operator_label,
      reason: draft.reason, expected_revision: draft.expectedRevision }, "동의 화면 설정 저장됨", () => state.consentDrafts.delete(id));
  });
  $("intervention-form").addEventListener("input", () => { if (!state.interventionRevision) state.interventionRevision = state.project?.intervention_revision || null; });
  $("intervention-form").addEventListener("submit", async (event) => { event.preventDefault(); if (!state.project) return; const note = $("intervention-note").value.trim(); if (!note) { message("상황과 확인 내용을 입력하세요.", true); return; } const body = { project_id: state.project.id, expected_revision: state.interventionRevision || state.project.intervention_revision, stage_id: $("stage-id").value, reason: $("intervention-reason").value, note, decision: $("intervention-decision").value.trim(), outcome: $("intervention-outcome").value.trim() }; if (await mutation("/api/delivery/intervention", body, "개입 기록 저장됨")) { state.interventionRevision = null; $("intervention-form").reset(); } });
  $("new-project").addEventListener("click", () => { if (state.pending || state.advancing) return; clearTimeout(state.timer); ++state.sequence; state.project = null; projectUrl(null); state.interventionRevision = null; $("intervention-form").reset(); message(""); renderProject(); $("client-request").focus(); });
  $("refresh-projects").addEventListener("click", refreshProjects); $("refresh-status").addEventListener("click", refreshStatus);
  document.addEventListener("visibilitychange", () => { if (document.hidden) { stopConnectionPoll(); clearMcpToken(); } else scheduleConnectionPoll(); });
  window.addEventListener("pagehide", () => { clearTimeout(state.timer); stopConnectionPoll(); ++state.connectionSequence; ++state.mcpSequence; state.mcpBusy = false; clearMcpToken(); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) refreshMcpConnection(); });
  renderProject();
  const selectedProject = new URLSearchParams(window.location.search).get("project");
  Promise.allSettled([refreshStatus(), refreshProjects(), selectedProject && /^[a-f0-9]{32}$/.test(selectedProject) ? openProject(selectedProject) : Promise.resolve()]);
})();
