import { $, api, text, safeError, download } from "/studio-dom.js";

export function createAccount(onChange, hasUnsaved) {
  let codex = null, login = null, loginTimer = null, connectionBusy = false, loginSequence = 0, member = true;
  let mcp = null, mcpToken = "", mcpBusy = false, mcpDisplay = false;
  const isOpen = () => $("account-dialog").open;
  const officialUrl = (value) => { try { const url = new URL(value); return url.protocol === "https:" && ["auth.openai.com","chatgpt.com"].includes(url.hostname) && !url.username && !url.password && !url.port ? url.href : null; } catch { return null; } };
  function clearToken() { mcpDisplay = false; mcpToken = ""; $("mcp-token").value = ""; $("mcp-token").type = "password"; $("mcp-secret").hidden = true; $("reveal-mcp").textContent = "키 보기"; $("reveal-mcp").setAttribute("aria-pressed","false"); }
  function stopPoll() { clearTimeout(loginTimer); loginTimer = null; }
  function schedulePoll() { stopPoll(); if (!document.hidden && isOpen() && login?.state === "pending" && login.connection_id) loginTimer = setTimeout(poll,3000); }
  function render() {
    const pending = login?.state === "pending", otherPending = !pending && codex?.state === "pending", connected = codex?.can_execute === true, working = codex?.state === "busy";
    $("codex-status").textContent = working ? "작업 중" : pending || otherPending ? "로그인 대기 중" : connected ? "연결됨" : codex ? "연결 필요" : "확인 중";
    $("account-dialog").querySelector(".connection-section > p").textContent = member ? "내 계정으로 연결하고, 요청한 작업에 내 이용 한도를 사용합니다." : "로컬 Codex 로그인을 사용합니다. 회원별 연결은 회원 서버에서 사용할 수 있습니다.";
    $("connect-codex").hidden = !member || connected || pending || otherPending || working;
    $("connect-codex").disabled = connectionBusy || codex?.available === false;
    $("disconnect-codex").hidden = !member || (!connected && !otherPending) || pending || working;
    $("disconnect-codex").textContent = otherPending ? "연결 초기화" : "연결 해제";
    $("disconnect-codex").disabled = connectionBusy;
    $("cancel-codex").disabled = connectionBusy;
    $("refresh-codex").disabled = connectionBusy;
    $("codex-login").hidden = !pending;
    $("codex-login-code").textContent = pending ? text(login.user_code) : "";
    const url = pending ? officialUrl(login.verification_url) : null;
    $("codex-login-link").hidden = !url;
    if (url) $("codex-login-link").href=url; else $("codex-login-link").removeAttribute("href");
    $("mcp-panel").hidden = !member;
    $("mcp-panel").querySelector("p").textContent = "내 AI 도구에서 요구사항 검토와 참고 자료 수집을 사용합니다. 요청 내용과 공개 URL은 서버를 거쳐 외부 처리 서비스로 전송됩니다. 새 키를 발급하면 이전 키는 사용할 수 없습니다.";
    $("logout").hidden = !member;
    $("mcp-status").textContent = mcp?.connected ? "연결 키 발급됨" : "연결 안 됨";
    $("create-mcp").disabled = mcpBusy; $("revoke-mcp").disabled = mcpBusy; $("revoke-mcp").hidden = !mcp?.connected;
    $("mcp-secret").hidden = !mcpToken; $("mcp-token").value=mcpToken;
    $("mcp-config").textContent = `CHANNELSHIFT_SERVICE_URL=${location.origin}\nCHANNELSHIFT_SERVICE_TOKEN_FILE=저장한/channelshift-service-token.txt의/전체/경로`;
    onChange(codex);
  }
  async function refresh() {
    try { const result = await api(member?"/api/connections/codex":"/api/delivery/status"); codex=result.codex || {}; render(); }
    catch(error) { $("account-message").textContent=error.message; }
  }
  async function accept(result) {
    login=result.connection || null;
    if (!login?.state) throw new Error("연결 응답을 확인하지 못했습니다.");
    if (login.state !== "pending") {
      const terminal=login; login=null; stopPoll(); await refresh();
      $("account-message").textContent = terminal.state === "connected" ? "내 Codex가 연결되었습니다." : ["cancelled","disconnected"].includes(terminal.state) ? "연결을 취소했습니다." : safeError(terminal.reason || `codex_connection_${terminal.state}`);
    }
    render(); schedulePoll();
  }
  async function poll() {
    if (!isOpen() || document.hidden || login?.state !== "pending" || !login.connection_id) return;
    const sequence=loginSequence;
    try { const result=await api("/api/connections/codex/poll",{connection_id:login.connection_id}); if (sequence===loginSequence && isOpen() && !document.hidden) await accept(result); }
    catch(error) { if (sequence===loginSequence) { $("account-message").textContent=error.message; if (["login_required","invalid_token","codex_connection_not_found","codex_connection_expired"].includes(error.code)) login=null; } }
    finally { if (sequence===loginSequence) schedulePoll(); }
  }
  async function connect(action) {
    if (connectionBusy || (action === "cancel" && !login?.connection_id)) return;
    stopPoll(); ++loginSequence; connectionBusy=true; $("account-message").textContent=""; render();
    try { const result=await api(`/api/connections/codex/${action}`,action === "cancel" ? {connection_id:login.connection_id} : {}); if (action === "disconnect") {login=null;codex=result.codex;$("account-message").textContent="내 Codex 연결을 해제했습니다.";} else await accept(result); }
    catch(error) {$("account-message").textContent=error.message;}
    finally {connectionBusy=false;render();schedulePoll();}
  }
  async function mcpAction(action) {
    if(mcpBusy)return; mcpBusy=true; clearToken(); mcpDisplay=!document.hidden && isOpen() && $("mcp-panel").open; render(); $("mcp-message").textContent="";
    try { const result=await api(`/api/connections/mcp/${action}`,{}); if(action === "create") {const token=text(result.connection?.token);if(!token || token.length>4096 || /\s/.test(token))throw new Error("연결 키 응답을 확인하지 못했습니다.");mcp={connected:true,expires_at:result.connection.expires_at};if(mcpDisplay && isOpen() && $("mcp-panel").open && !document.hidden){mcpToken=token;$("mcp-message").textContent="연결 키를 발급했습니다.";} }else{mcp=result.connection;$("mcp-message").textContent="MCP 연결을 해제했습니다.";} }
    catch(error){$("mcp-message").textContent=error.message;}
    finally{mcpBusy=false;render();}
  }
  $("account-open").addEventListener("click",async()=>{ $("account-dialog").showModal(); await refresh(); if(member){try{mcp=(await api("/api/connections/mcp")).connection;}catch(error){$("mcp-message").textContent=error.message;}render();}schedulePoll(); });
  $("account-close").addEventListener("click",()=>$("account-dialog").close());
  $("account-dialog").addEventListener("close",()=>{clearToken();stopPoll();});
  $("connect-codex").addEventListener("click",()=>connect("start"));
  $("disconnect-codex").addEventListener("click",()=>connect("disconnect"));
  $("cancel-codex").addEventListener("click",()=>connect("cancel"));
  $("refresh-codex").addEventListener("click",refresh);
  $("create-mcp").addEventListener("click",()=>mcpAction("create"));
  $("revoke-mcp").addEventListener("click",()=>mcpAction("revoke"));
  $("mcp-panel").addEventListener("toggle",()=>{if(!$("mcp-panel").open)clearToken();});
  $("reveal-mcp").addEventListener("click",()=>{const show=$("mcp-token").type === "password";$("mcp-token").type=show?"text":"password";$("reveal-mcp").textContent=show?"키 숨기기":"키 보기";$("reveal-mcp").setAttribute("aria-pressed",String(show));});
  $("copy-mcp").addEventListener("click",async()=>{if(!mcpToken)return;try{await navigator.clipboard.writeText(mcpToken);$("mcp-message").textContent="연결 키를 복사했습니다.";}catch{$("mcp-message").textContent="복사하지 못했습니다. 키 파일 저장을 이용하세요.";}});
  $("save-mcp").addEventListener("click",()=>{if(mcpToken)download(new Blob([mcpToken],{type:"text/plain;charset=utf-8"}),"channelshift-service-token.txt");});
  $("logout").addEventListener("click",async()=>{if(hasUnsaved() && !confirm("저장하지 않은 내용이 있습니다. 로그아웃할까요?"))return;stopPoll();++loginSequence;clearToken();try{await api("/api/auth/logout",{});location.assign("/login");}catch(error){$("account-message").textContent=error.message;}});
  document.addEventListener("visibilitychange",()=>{if(document.hidden){clearToken();stopPoll();}else schedulePoll();});
  window.addEventListener("pagehide",()=>{clearToken();stopPoll();});
  return {refresh,setStatus(status){member=status?.member_mode === true;codex=status?.codex || null;render();},open(){$("account-open").click();}};
}
