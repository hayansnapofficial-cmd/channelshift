"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { members: [], audit: [], pending: false, denied: false, selection: null };
  const errors = {
    auth_required: "다시 로그인해 주세요.",
    admin_forbidden: "관리자 권한이 필요합니다.",
    member_not_found: "계정을 찾을 수 없습니다. 목록을 새로고침해 주세요.",
    master_protected: "마스터 계정의 이용 상태는 변경할 수 없습니다.",
    invalid_admin_request: "변경할 계정과 사유를 확인해 주세요. 사유는 1~500자로 입력해 주세요.",
    invalid_token: "연결 확인이 만료되었습니다. 페이지를 새로고침해 주세요.",
    invalid_origin: "접속 주소를 확인할 수 없습니다. ChannelShift 주소에서 다시 열어 주세요.",
    auth_storage_unavailable: "회원 정보를 읽거나 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    auth_busy: "다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.",
    rate_limited: "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
    network_error: "서버에 연결할 수 없습니다. 실행 상태를 확인한 뒤 다시 시도해 주세요.",
    invalid_response: "서버 응답을 확인하지 못했습니다. 다시 시도해 주세요.",
    request_failed: "요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  };
  const actionLabels = { member_disabled: "이용 정지", member_enabled: "이용 복원", master_bootstrap: "마스터 계정 생성" };
  const dateFormatter = new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  function message(value, error = false) { $("message").textContent = value; $("message").hidden = !value; $("message").classList.toggle("error", error); }
  function failure(code) { const error = new Error(Object.hasOwn(errors, code) ? errors[code] : errors.request_failed); error.code = code; return error; }
  function disabled(member) { return member.disabled !== null && member.disabled !== undefined; }
  function text(value, fallback = "—") { return typeof value === "string" && value ? value : fallback; }
  function date(value) {
    if (typeof value !== "number" && typeof value !== "string") return "—";
    const result = new Date(typeof value === "number" ? value * 1000 : value);
    return Number.isNaN(result.getTime()) ? "—" : dateFormatter.format(result);
  }
  function node(tag, value, className) {
    const element = document.createElement(tag);
    if (value !== undefined) element.textContent = value;
    if (className) element.className = className;
    return element;
  }
  function controls() {
    document.querySelectorAll("button, input, textarea").forEach((element) => { element.disabled = state.pending || state.denied; });
    $("refresh-overview").hidden = state.denied;
    $("admin-content").setAttribute("aria-busy", String(state.pending));
    $("apply-status").disabled = state.pending || state.denied || !state.selection;
  }
  function clearPrivateContent() {
    state.members = []; state.audit = []; state.selection = null;
    $("admin-content").hidden = true;
    $("member-rows").replaceChildren(); $("audit-rows").replaceChildren();
    $("member-action").hidden = true; $("action-reason").value = "";
    $("action-title").textContent = ""; $("action-description").textContent = "";
    ["count-total", "count-verified", "count-pending", "count-disabled"].forEach((id) => { $(id).textContent = "0"; });
    $("member-count").textContent = "";
  }
  async function api(path, body) {
    const token = document.querySelector('meta[name="channelshift-token"]')?.content;
    if (!token || token === "__CHANNELSHIFT_TOKEN__") throw failure("invalid_token");
    const options = { credentials: "same-origin", cache: "no-store", headers: { "X-ChannelShift-Token": token } };
    if (body !== undefined) { options.method = "POST"; options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
    let response;
    try { response = await fetch(path, options); } catch { throw failure("network_error"); }
    if (response.status === 401) {
      clearPrivateContent(); window.location.replace("/login"); throw failure("auth_required");
    }
    if (response.status === 403) {
      clearPrivateContent(); state.denied = true; $("access-denied").hidden = false; throw failure("admin_forbidden");
    }
    let result;
    try { result = await response.json(); } catch { throw failure("invalid_response"); }
    if (!result || typeof result !== "object" || Array.isArray(result)) throw failure("invalid_response");
    if (!response.ok || result.ok !== true) throw failure(typeof result.error === "string" ? result.error : "request_failed");
    return result;
  }
  function validOverview(result) {
    return Array.isArray(result.members) && Array.isArray(result.audit) && result.members.every((member) =>
      member && typeof member === "object" && !Array.isArray(member) && typeof member.id === "string" && member.id &&
      typeof member.username === "string" && typeof member.email === "string" && ["master", "member"].includes(member.role) &&
      typeof member.email_verified === "boolean" && (member.disabled === null || typeof member.disabled === "number")) &&
      result.audit.every((entry) => entry && typeof entry === "object" && !Array.isArray(entry));
  }
  function chooseMember(member) {
    if (state.pending || state.denied || member.role !== "member") return;
    state.selection = { id: member.id, disabled: !disabled(member) };
    const suspending = state.selection.disabled;
    $("action-title").textContent = `${member.username} · ${suspending ? "이용 정지" : "이용 복원"}`;
    $("action-description").textContent = suspending ? "적용하면 이 계정의 로그인이 제한되고 기존 세션이 종료됩니다." : "적용하면 이용 정지가 해제됩니다. 이메일 인증을 완료한 계정은 다시 로그인할 수 있습니다.";
    $("apply-status").textContent = suspending ? "이용 정지 적용" : "이용 복원 적용";
    $("apply-status").classList.toggle("danger", suspending);
    $("action-reason").value = ""; $("member-action").hidden = false;
    message(""); controls(); $("action-reason").focus();
  }
  function closeAction() {
    state.selection = null; $("member-action").hidden = true; $("action-reason").value = ""; controls();
  }
  function renderMembers() {
    const query = $("member-search").value.trim().toLocaleLowerCase();
    const members = state.members.filter((member) => `${member.username} ${member.email}`.toLocaleLowerCase().includes(query));
    $("member-count").textContent = query ? `${members.length} / 전체 ${state.members.length}개` : `전체 ${members.length}개`;
    $("members-empty").hidden = members.length > 0;
    $("members-empty").textContent = query ? "검색 결과가 없습니다." : "표시할 계정이 없습니다.";
    $("members-table-wrap").hidden = members.length === 0;
    const rows = document.createDocumentFragment();
    members.forEach((member) => {
      const row = node("tr"); const account = node("td", undefined, "account-cell");
      account.append(node("strong", member.username), node("small", member.email));
      const status = node("td"); status.append(node("span", disabled(member) ? "이용 정지" : member.role === "member" && !member.email_verified ? "인증 대기" : "정상", `state-label${disabled(member) ? " disabled" : ""}`));
      const verified = node("td"); verified.append(node("span", member.email_verified ? "완료" : "미인증", `state-label${member.email_verified ? "" : " pending"}`));
      const action = node("td");
      if (member.role === "member") {
        const button = node("button", disabled(member) ? "이용 복원" : "이용 정지", `text-button${disabled(member) ? "" : " suspend"}`);
        button.type = "button"; button.setAttribute("aria-label", `${member.username} ${disabled(member) ? "이용 복원" : "이용 정지"}`);
        button.addEventListener("click", () => chooseMember(member)); action.append(button);
      } else { action.append(node("span", "—", "muted")); }
      row.append(account, node("td", member.role === "master" ? "마스터" : "회원", "role-cell"), verified, status, node("td", date(member.created), "date-cell"), action);
      rows.append(row);
    });
    $("member-rows").replaceChildren(rows); controls();
  }
  function renderOverview() {
    $("count-total").textContent = String(state.members.length);
    $("count-verified").textContent = String(state.members.filter((member) => member.email_verified).length);
    $("count-pending").textContent = String(state.members.filter((member) => !member.email_verified).length);
    $("count-disabled").textContent = String(state.members.filter(disabled).length);
    renderMembers();
    const identities = new Map(state.members.map((member) => [member.id, member.username]));
    const rows = document.createDocumentFragment();
    state.audit.forEach((entry) => {
      const row = node("tr");
      row.append(node("td", date(entry.created), "date-cell"), node("td", entry.actor_id === "local-bootstrap" ? "로컬 설정" : identities.get(entry.actor_id) || text(entry.actor_id)), node("td", Object.hasOwn(actionLabels, entry.action) ? actionLabels[entry.action] : "계정 변경"), node("td", identities.get(entry.target_id) || text(entry.target_id)), node("td", entry.reason === "local_provisioning" ? "초기 마스터 설정" : text(entry.reason)));
      rows.append(row);
    });
    $("audit-rows").replaceChildren(rows); $("audit-empty").hidden = state.audit.length > 0; $("audit-table-wrap").hidden = state.audit.length === 0;
    $("admin-content").hidden = false;
  }
  async function fetchOverview() {
    const result = await api("/api/admin/overview");
    if (!validOverview(result)) throw failure("invalid_response");
    state.members = result.members; state.audit = result.audit;
    renderOverview();
  }
  async function refreshOverview() {
    if (state.pending || state.denied) return;
    state.pending = true; controls(); message("");
    $("loading-status").hidden = false; $("loading-status").textContent = "회원 정보를 불러오는 중입니다.";
    try { await fetchOverview(); closeAction(); }
    catch (error) { message(error.message, true); }
    finally { state.pending = false; $("loading-status").hidden = true; controls(); }
  }
  $("member-status-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (state.pending || state.denied || !state.selection) return;
    const reason = $("action-reason").value.trim();
    if (!reason || Array.from(reason).length > 500) { message(errors.invalid_admin_request, true); $("action-reason").focus(); return; }
    const selected = { ...state.selection };
    state.pending = true; controls(); message("");
    let saved = false;
    try {
      await api("/api/admin/member-status", { user_id: selected.id, disabled: selected.disabled, reason });
      saved = true; closeAction(); await fetchOverview();
      message(selected.disabled ? "계정 이용을 정지했습니다." : "계정 이용을 복원했습니다.");
    } catch (error) {
      if (saved && !state.denied && error.code !== "auth_required") {
        clearPrivateContent(); message("변경을 저장했습니다. 최신 목록을 불러오지 못했으니 새로고침해 주세요.", true);
      } else { message(error.message, true); }
    } finally { state.pending = false; controls(); }
  });
  $("cancel-status").addEventListener("click", closeAction);
  $("refresh-overview").addEventListener("click", refreshOverview);
  $("member-search").addEventListener("input", renderMembers);
  refreshOverview();
})();
