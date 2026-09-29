"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { status: null, pending: false, view: "login", waitingEmail: "", verificationToken: null, linkRevision: 0 };
  const panels = ["login", "register", "waiting", "verify", "resend", "account"];
  const errors = {
    auth_invalid_input: "아이디·이메일·비밀번호 형식과 길이를 확인해 주세요.",
    invalid_member_input: "아이디·이메일·비밀번호 형식과 길이를 확인해 주세요.",
    invalid_password: "비밀번호는 8~128자로 입력해 주세요.",
    auth_invalid_credentials: "아이디와 비밀번호 또는 이메일 인증 상태를 확인해 주세요.",
    invalid_credentials: "아이디와 비밀번호 또는 이메일 인증 상태를 확인해 주세요.",
    auth_rate_limited: "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
    rate_limited: "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
    auth_token_invalid: "인증 링크가 만료되었거나 사용할 수 없습니다. 새 인증 메일을 요청해 주세요.",
    invalid_verification_token: "인증 링크가 만료되었거나 사용할 수 없습니다. 새 인증 메일을 요청해 주세요.",
    email_not_configured: "메일 발송 설정이 없어 인증 메일을 보낼 수 없습니다. 담당자에게 설정을 요청해 주세요.",
    email_delivery_failed: "인증 메일을 보내지 못했습니다. 잠시 후 다시 요청해 주세요.",
    invalid_token: "연결 확인이 만료되었습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.",
    invalid_origin: "연결 주소를 확인할 수 없습니다. ChannelShift 회원 주소에서 다시 열어 주세요.",
    invalid_host: "ChannelShift 회원 서버 주소를 확인해 주세요.",
    auth_required: "다시 로그인해 주세요.",
    auth_busy: "인증 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.",
    auth_unavailable: "회원 인증을 사용할 수 없습니다. 담당자에게 연결 상태 확인을 요청해 주세요.",
    auth_storage_unavailable: "회원 정보를 저장하거나 읽지 못했습니다. 담당자에게 저장소 확인을 요청해 주세요.",
    unsafe_auth_storage: "회원 저장소 설정을 확인해야 합니다. 담당자에게 문의해 주세요.",
    registration_unavailable: "회원가입을 완료할 수 없습니다. 입력 내용을 확인하거나 담당자에게 문의해 주세요.",
    invalid_smtp_configuration: "메일 발송 설정을 확인해야 합니다. 담당자에게 문의해 주세요.",
    network_error: "서버에 연결할 수 없습니다. 실행 상태를 확인한 뒤 다시 시도해 주세요.",
    invalid_response: "서버 응답을 확인하지 못했습니다. 연결 상태를 다시 확인해 주세요.",
    request_failed: "요청을 완료하지 못했습니다. 입력 내용을 확인하고 다시 시도해 주세요.",
  };
  function message(value, error = false) { $("message").textContent = value; $("message").hidden = !value; $("message").classList.toggle("error", error); }
  function failure(code) { const error = new Error(Object.hasOwn(errors, code) ? errors[code] : errors.request_failed); error.code = code; return error; }
  async function api(path, body) {
    const token = document.querySelector('meta[name="channelshift-token"]')?.content;
    if (!token || token === "__CHANNELSHIFT_TOKEN__") throw failure("invalid_token");
    const options = { credentials: "same-origin", cache: "no-store", headers: { "X-ChannelShift-Token": token } };
    if (body !== undefined) { options.method = "POST"; options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
    let response;
    try { response = await fetch(path, options); } catch { throw failure("network_error"); }
    let result;
    try { result = await response.json(); } catch { throw failure("invalid_response"); }
    if (!result || typeof result !== "object" || Array.isArray(result)) throw failure("invalid_response");
    if (!response.ok || result.ok !== true) throw failure(typeof result.error === "string" ? result.error : "request_failed");
    return result;
  }
  function controls() {
    const ready = Boolean(state.status);
    document.querySelectorAll("input, button").forEach((element) => { element.disabled = state.pending; });
    $("login-submit").disabled = state.pending || !ready;
    $("register-submit").disabled = state.pending || !ready || !state.status.email_configured;
    $("resend-submit").disabled = state.pending || !ready || !state.status.email_configured;
    $("waiting-resend").disabled = state.pending || !ready || !state.status.email_configured;
    $("verify-submit").disabled = state.pending || !ready || !state.verificationToken;
    $("logout").disabled = state.pending || !ready || !state.status.user;
    $("account-admin").hidden = state.status?.user?.role !== "master";
    $("connection-state").textContent = ready ? "회원 서버 연결됨" : "연결 확인 필요";
    $("email-warning").hidden = !ready || state.status.email_configured;
    $("email-warning").textContent = "메일 발송 설정이 없어 회원가입과 인증 메일 재발송을 사용할 수 없습니다.";
  }
  function show(view, clear = true) {
    state.view = view;
    panels.forEach((name) => { $(`${name}-panel`).hidden = name !== view; });
    $("auth-tabs").hidden = ["verify", "account"].includes(view);
    ["login", "register"].forEach((name) => { if (name === view) $(`show-${name}`).setAttribute("aria-current", "page"); else $(`show-${name}`).removeAttribute("aria-current"); });
    if (clear) message("");
    controls();
  }
  function showAccount() {
    $("account-username").textContent = state.status.user.username;
    $("account-email").textContent = state.status.user.email;
    show("account");
  }
  async function refreshStatus() {
    if (state.pending) return;
    state.pending = true; controls();
    try {
      const status = await api("/api/auth/status");
      if (typeof status.email_configured !== "boolean" || !(status.user === null ||
          status.user && typeof status.user === "object" && typeof status.user.username === "string" && typeof status.user.email === "string")) throw failure("invalid_response");
      state.status = status;
      if (status.user && !state.verificationToken) showAccount();
      else if (state.view === "account") show("login");
    } catch (error) { state.status = null; message(error.message, true); }
    finally { state.pending = false; controls(); }
  }
  function validUsername(value) { return /^[A-Za-z0-9_]{4,32}$/.test(value); }
  function validPassword(value) { const count = Array.from(value.normalize("NFC")).length; return count >= 8 && count <= 128; }
  function validLoginPassword(value) { const count = Array.from(value).length; return count >= 1 && count <= 128; }
  function checkNewPassword(value, confirm) {
    if (!validPassword(value)) { message("비밀번호는 8~128자로 입력해 주세요.", true); return false; }
    if (value !== confirm) { message("비밀번호 확인이 일치하지 않습니다.", true); return false; }
    return true;
  }
  async function submit(path, body, success) {
    if (state.pending || !state.status) return;
    const linkRevision = state.linkRevision;
    state.pending = true; controls(); message("");
    try { const result = await api(path, body); if (linkRevision === state.linkRevision) await success(result); }
    catch (error) { if (error.code === "email_not_configured") state.status.email_configured = false; if (linkRevision === state.linkRevision) message(error.message, true); }
    finally { state.pending = false; controls(); }
  }
  function showWaiting(email) { state.waitingEmail = email; $("waiting-email").textContent = email; $("resend-email").value = email; show("waiting"); }
  $("show-login").addEventListener("click", () => show("login"));
  $("show-register").addEventListener("click", () => show("register"));
  ["show-resend", "verify-resend"].forEach((id) => $(id).addEventListener("click", () => show("resend")));
  ["waiting-login", "resend-login"].forEach((id) => $(id).addEventListener("click", () => show("login")));
  $("waiting-resend").addEventListener("click", () => { $("resend-email").value = state.waitingEmail; show("resend"); });
  $("refresh-status").addEventListener("click", refreshStatus);
  $("login-form").addEventListener("submit", (event) => {
    event.preventDefault(); const username = $("login-username").value.trim(); const password = $("login-password").value;
    if (!validUsername(username) || !validLoginPassword(password)) { message("아이디는 영문·숫자·밑줄 4~32자로, 비밀번호는 1~128자로 입력해 주세요.", true); return; }
    submit("/api/auth/login", { username, password }, () => { $("login-password").value = ""; window.location.assign("/delivery"); });
  });
  $("register-form").addEventListener("submit", (event) => {
    event.preventDefault(); if (!state.status?.email_configured) { message(errors.email_not_configured, true); return; }
    const username = $("register-username").value.trim(); const email = $("register-email").value.trim(); const password = $("register-password").value;
    if (!validUsername(username)) { message("아이디는 영문·숫자·밑줄(_) 4~32자로 입력해 주세요.", true); return; }
    if (!checkNewPassword(password, $("register-password-confirm").value)) return;
    submit("/api/auth/register", { username, email, password }, () => { $("register-password").value = ""; $("register-password-confirm").value = ""; $("login-username").value = username; showWaiting(email); });
  });
  $("resend-form").addEventListener("submit", (event) => {
    event.preventDefault(); if (!state.status?.email_configured) { message(errors.email_not_configured, true); return; }
    const email = $("resend-email").value.trim();
    submit("/api/auth/resend", { email }, () => showWaiting(email));
  });
  $("verify-form").addEventListener("submit", (event) => {
    event.preventDefault(); if (!state.verificationToken) { message(errors.invalid_verification_token, true); return; }
    const password = $("verify-password").value;
    if (!checkNewPassword(password, $("verify-password-confirm").value)) return;
    submit("/api/auth/verify", { token: state.verificationToken, password }, () => {
      state.verificationToken = null; $("verify-form").reset(); show("login");
      message("이메일 인증을 완료했습니다. 설정한 비밀번호로 로그인하세요."); $("login-username").focus();
    });
  });
  $("logout").addEventListener("click", () => submit("/api/auth/logout", {}, () => {
    state.status.user = null; document.querySelectorAll('input[type="password"]').forEach((input) => { input.value = ""; });
    show("login"); message("로그아웃했습니다.");
  }));

  // The email link token is kept only in this closure, never in rendered text or storage.
  function captureVerificationLink() {
    if (!window.location.hash) return;
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const tokens = fragment.getAll("verify");
    state.linkRevision += 1; state.verificationToken = null;
    try { window.history.replaceState(null, "", window.location.pathname); }
    catch { show("resend"); message("인증 주소를 정리하지 못했습니다. 로그인 페이지를 다시 열어 인증 메일을 요청해 주세요.", true); return; }
    if (tokens.length === 1 && tokens[0].length > 0 && tokens[0].length <= 1024) { state.verificationToken = tokens[0]; show("verify"); }
    else { show("resend"); message(errors.invalid_verification_token, true); }
  }
  window.addEventListener("hashchange", captureVerificationLink);
  captureVerificationLink();
  refreshStatus();
})();
