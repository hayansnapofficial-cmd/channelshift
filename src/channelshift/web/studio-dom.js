export const $ = (id) => document.getElementById(id);
export const list = (value) => Array.isArray(value) ? value : [];
export const text = (value) => typeof value === "string" ? value : "";
export function node(tag, className, value) { const el = document.createElement(tag); if (className) el.className = className; if (value !== undefined) el.textContent = String(value); return el; }
export function arrow() { const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("viewBox", "0 0 20 20"); svg.setAttribute("aria-hidden", "true"); const path = document.createElementNS(svg.namespaceURI, "path"); path.setAttribute("d", "M3 10h13m-5-5 5 5-5 5"); svg.append(path); return svg; }
export function button(label, callback, primary = false) { const el = node("button", primary ? "button primary" : "button secondary", label); el.type = "button"; if (primary) el.append(arrow()); el.addEventListener("click", callback); return el; }
export function notice(value, error = false) { $("notice").textContent = value; $("notice").hidden = !value; $("notice").classList.toggle("error", error); }
const errors = {
  pipeline_revision_conflict: "다른 작업으로 내용이 바뀌었습니다. 입력은 보존했습니다. 최신 내용을 확인한 뒤 다시 저장하세요.",
  pipeline_stage_locked: "앞 단계의 결과를 확인하고 승인한 뒤 진행하세요.",
  pipeline_busy: "프로젝트 작업이 진행 중입니다. 결과를 기다려 주세요.",
  pipeline_review_note_required: "검토한 내용과 확인 결과를 짧게 적어 주세요.",
  pipeline_analysis_required: "저장한 답변으로 Codex가 요구사항을 다시 정리한 뒤 확정할 수 있습니다.",
  pipeline_artifact_required: "먼저 이 단계의 초안을 만들어 주세요.",
  pipeline_artifact_stale: "앞 단계가 바뀌어 결과를 다시 만들어야 합니다.",
  pipeline_database_check_failed: "데이터베이스 검증을 통과하지 못했습니다. 설계를 확인하세요.",
  pipeline_job_failed: "초안을 만들지 못했습니다. 연결 상태와 앞 단계 결과를 확인하세요.",
  pipeline_recovery_required: "이전 작업의 종료 확인이 필요합니다. 담당자에게 실행 상태 점검을 요청하세요.",
  invalid_pipeline_input: "작업에 필요한 입력 형식과 길이를 확인하세요.",
  invalid_pipeline_artifact: "생성된 파일의 구조를 확인하지 못했습니다. 앞 단계 결과를 검토하세요.",
  codex_invalid_pipeline_output: "Codex가 반환한 파일 형식을 확인하지 못했습니다. 기존 결과는 유지했습니다.",
  login_required: "로그인이 만료되었습니다. 입력 내용을 보관한 뒤 다시 로그인하세요.",
  invalid_token: "연결 확인이 만료되었습니다. 입력 내용을 보관한 뒤 새로고침하세요.",
  invalid_request: "입력 항목을 확인하고 다시 시도하세요.",
  invalid_project: "프로젝트 이름과 만들고 싶은 내용을 입력하세요.",
  invalid_delivery_project: "프로젝트 이름과 만들고 싶은 내용을 확인하세요.",
  invalid_delivery_input: "입력 형식과 길이를 확인하세요. 원문은 12,000자, 답변은 각각 2,000자까지 저장할 수 있습니다.",
  invalid_studio_input: "입력 항목을 확인하고 다시 시도하세요.",
  invalid_site_obligations: "운영·정책의 사업자등록번호, 연락처와 입력 길이를 확인하세요.",
  site_obligations_incomplete: "운영·정책 7개 항목을 모두 입력한 뒤 납품 파일을 준비하세요.",
  delivery_revision_conflict: "다른 작업으로 내용이 바뀌었습니다. 입력은 보존했습니다. 최신 내용을 확인한 뒤 다시 저장하세요.",
  studio_revision_conflict: "다른 작업으로 내용이 바뀌었습니다. 입력은 보존했습니다. 최신 내용을 확인한 뒤 다시 저장하세요.",
  delivery_answers_required: "확인이 필요한 질문에 답변한 뒤 다시 정리하세요.",
  delivery_candidate_required: "내 Codex로 요구사항을 먼저 정리하세요.",
  delivery_review_required: "요구사항을 확인하고 확정한 뒤 계속하세요.",
  delivery_client_requirements_required: "설계에 반영할 요구사항이 필요합니다. 정리한 내용을 확인하세요.",
  delivery_erd_stale: "요구사항이 변경되었습니다. 현재 요구사항에 맞춰 ERD를 다시 만들어 주세요.",
  delivery_erd_required: "먼저 ERD 초안을 만들어 주세요.",
  invalid_schema: "ERD 구조를 확인하지 못했습니다. 편집 내용을 확인하세요.",
  codex_invalid_erd_output: "Codex의 ERD 결과 형식을 확인하지 못했습니다. 기존 초안은 유지했습니다.",
  delivery_busy: "이 프로젝트에서 작업이 진행 중입니다. 결과를 기다린 뒤 다시 시도하세요.",
  studio_busy: "이 프로젝트에서 작업이 진행 중입니다. 결과를 기다린 뒤 다시 시도하세요.",
  busy: "작업이 진행 중입니다. 잠시 후 다시 시도하세요.",
  project_busy: "프로젝트 작업이 진행 중입니다. 결과를 기다려 주세요.",
  delivery_recovery_required: "이전 작업의 종료 확인이 필요합니다. 담당자에게 실행 상태 점검을 요청하세요.",
  codex_not_authenticated: "내 계정에서 Codex를 연결한 뒤 계속하세요.",
  codex_authentication_required: "내 계정에서 Codex를 연결한 뒤 계속하세요.",
  codex_login_required: "내 계정에서 Codex를 연결한 뒤 계속하세요.",
  member_provider_not_linked: "내 계정에서 본인의 Codex를 연결하세요.",
  codex_unavailable: "Codex 실행 환경을 확인할 수 없습니다. 담당자에게 연결 확인을 요청하세요.",
  codex_timeout: "Codex 작업 시간이 초과되었습니다. 연결 상태와 요청 범위를 확인하세요.",
  codex_invalid_output: "Codex 결과 형식을 확인하지 못했습니다. 내용을 확인한 뒤 다시 요청하세요.",
  codex_execution_failed: "Codex 작업을 완료하지 못했습니다. 연결 상태를 확인하세요.",
  codex_rate_limited: "Codex 이용 한도에 도달했습니다. 한도 초기화 후 다시 요청하세요.",
  codex_connection_expired: "로그인 코드가 만료되었습니다. 다시 연결하세요.",
  codex_connection_failed: "Codex 연결을 완료하지 못했습니다. 잠시 후 다시 시도하세요.",
  codex_connection_pending: "공식 로그인 화면에서 진행 중인 연결을 완료하세요.",
  codex_connection_not_found: "진행 중인 연결을 찾지 못했습니다. 다시 연결하세요.",
  delivery_project_not_found: "프로젝트를 찾지 못했습니다. 목록을 새로고침하세요.",
  not_found: "요청한 항목을 찾지 못했습니다.",
  service_not_configured: "서버 연결이 필요합니다. 담당자에게 연결 확인을 요청하세요.",
  payload_too_large: "입력 내용이 너무 큽니다. 요청 범위를 줄여 주세요.",
};
export function safeError(code) { return errors[code] || "작업을 마치지 못했습니다. 입력 내용과 연결 상태를 확인한 뒤 다시 시도하세요."; }
export async function api(path, body, binary = false) {
  const options = { credentials: "same-origin", cache: "no-store", headers: { "X-ChannelShift-Token": document.querySelector('meta[name="channelshift-token"]').content } };
  if (body !== undefined) { options.method = "POST"; options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  let response;
  try { response = await fetch(path, options); } catch { throw new Error("서버에 연결할 수 없습니다. ChannelShift 실행 상태를 확인하세요."); }
  if (binary && response.ok) return response.blob();
  let result;
  try { result = await response.json(); } catch { throw new Error("서버 응답을 읽지 못했습니다. 연결 상태를 확인하세요."); }
  if (!response.ok || result.ok !== true) { const error = new Error(safeError(result.error)); error.code = result.error; error.status = response.status; throw error; }
  return result;
}
export function download(blob, filename) { const url = URL.createObjectURL(blob); const link = node("a"); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000); }
