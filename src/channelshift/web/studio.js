import { $, api, list, text, node, button, notice, safeError, download } from "/studio-dom.js";
import { createAccount } from "/studio-account.js";
import { createPolicyEditor } from "/studio-policy.js";
import { createFeatureGuidance } from "/studio-guidance.js";

const groups = [
  {id:"requirements",label:"요구사항",stages:["requirements"]},
  {id:"wireframe",label:"화면 설계",stages:["wireframe"]},
  {id:"data",label:"데이터",stages:["erd","api","database"]},
  {id:"backend",label:"백엔드",stages:["backend"]},
  {id:"frontend",label:"프론트",stages:["frontend"]},
  {id:"delivery",label:"검수·납품",stages:["delivery"]},
];
const stageCopy = {
  wireframe:{title:"화면 설계",description:"확정한 요구사항을 화면과 사용자 흐름으로 정리합니다.",create:"화면 설계 만들기",ai:true},
  erd:{title:"데이터 구조 설계",description:"화면에 필요한 데이터를 정의하고 관계를 확인합니다.",create:"ERD 초안 만들기",ai:true},
  api:{title:"API 계약 정리",description:"화면과 데이터 사이에서 주고받을 요청과 응답을 정리합니다.",create:"API 계약 만들기",ai:true},
  database:{title:"데이터베이스 준비",description:"설계한 구조로 SQLite 생성문과 검증 결과를 준비합니다.",create:"DB 생성문 검증하기",ai:false},
  backend:{title:"백엔드 구현",description:"승인한 API 계약과 데이터 구조를 바탕으로 서버 코드를 준비합니다.",create:"백엔드 초안 만들기",ai:true},
  frontend:{title:"프론트 구현",description:"승인한 화면과 API에 맞춰 사용자 화면의 코드를 준비합니다.",create:"프론트 초안 만들기",ai:true},
  delivery:{title:"검수·납품",description:"단계별 결과와 운영·정책을 모아 납품 파일을 준비합니다.",create:"납품 검수 자료 만들기",ai:false},
};
const state = {view:null,projects:[],stage:"requirements",busy:false,sequence:0,timer:null,codex:null,services:null,answers:new Map(),notes:new Map(),fileDrafts:new Map(),additionalRequests:new Map(),referenceDrafts:new Map(),newDirty:false,renderedRevision:null};
const currentProject = () => state.view?.project;
const projectWorking = () => ["EXTRACTING","REVIEWING","COLLECTING_REFERENCE","DESIGNING_ERD","ADVISING","RUNNING"].includes(currentProject()?.state) || state.view?.pipeline?.job?.state === "running" || state.view?.pipeline?.advice_job?.state === "running";
const working = () => state.busy || projectWorking();
const answerKey = (project,saved) => `${project.id}:${project.candidate_revision}:${saved.question_digest}`;
const hasUnsaved = () => state.newDirty || state.answers.size>0 || policy.hasUnsaved() || guidance.hasUnsaved() || state.fileDrafts.size>0 || state.additionalRequests.size>0 || state.referenceDrafts.size>0 || [...state.notes.values()].some((value)=>value.trim());
const account = createAccount((codex)=>{state.codex=codex;updateControls();},hasUnsaved);
const policy = createPolicyEditor(async(payload,revision)=>action("save_obligations",payload,{revision,quiet:true}),updateControls);
const guidance = createFeatureGuidance({onChange:updateControls,onRender:render,onSave:()=>saveFeatureChoices(),onAnalyze:()=>submitAnswers("analyze"),onUnsure:()=>submitAnswers("analyze")});

function currentStages() {return list(state.view?.pipeline?.stages);}
function nextStage() {return currentStages().find((stage)=>stage.state!=="approved")?.id || "delivery";}
function setStage(id) {state.stage=id;render();}
function canExecute() {return state.codex?.can_execute===true;}
function actionRow(control,ai=false) {const row=node("div","action-row");row.append(control);if(ai){row.append(node("span","field-help",canExecute()?"내 Codex 연결로 정리합니다.":"내 계정에서 Codex를 연결하세요."));}return row;}
function markAction(control,ai=false){control.dataset.mutating="true";if(ai)control.dataset.ai="true";return control;}
function updateControls(){
  document.querySelectorAll("[data-mutating]").forEach((control)=>{control.disabled=working() || control.dataset.allowed==="false" || (control.dataset.ai==="true" && !canExecute()) || (control.dataset.cleanArtifact && state.fileDrafts.has(control.dataset.cleanArtifact)) || (control.dataset.cleanPolicies==="true" && policy.hasCurrentUnsaved(currentProject()?.id)) || (state.stage==="requirements" && state.additionalRequests.has(currentProject()?.id) && (control.dataset.ai==="true" || control.id==="confirm-requirements")) || (control.id==="confirm-requirements" && (guidance.hasCurrentUnsaved(state.view) || guidance.unresolved(state.view).length>0)) || (control.dataset.featureSave==="true" && guidance.hasConflict(state.view)) || (control.dataset.cleanFeatures==="true" && guidance.hasCurrentUnsaved(state.view));});
  const fileNotice=$("artifact-unsaved-message");if(fileNotice)fileNotice.hidden=!state.fileDrafts.has(fileNotice.dataset.artifact);
  const policyNotice=$("delivery-unsaved-message");if(policyNotice)policyNotice.hidden=!policy.hasCurrentUnsaved(currentProject()?.id);
  const featureNotice=$("feature-draft-status");if(featureNotice)featureNotice.textContent=guidance.hasCurrentUnsaved(state.view)?"저장하지 않은 선택이 있습니다.":"저장 후 다시 정리해야 요구사항에 반영됩니다. 확정은 별도로 진행합니다.";
  document.querySelectorAll("[data-feature-recommendation]").forEach((item)=>{item.hidden=guidance.hasCurrentUnsaved(state.view) || hasCurrentDrafts() || state.additionalRequests.has(currentProject()?.id);});
  document.querySelectorAll("[data-clean-intake]").forEach((control)=>{control.disabled ||= guidance.hasCurrentUnsaved(state.view) || hasCurrentDrafts() || state.additionalRequests.has(currentProject()?.id);});
  $("create-project").disabled=state.busy;
  $("new-project").disabled=state.busy;
}
function renderSteps(){
  const target=$("pipeline-steps");target.replaceChildren();
  for(const group of groups){
    const item=node("li");const control=node("button",null,group.label);control.type="button";
    const stages=currentStages().filter((stage)=>group.stages.includes(stage.id));
    const available=group.id==="requirements" || stages.some((stage)=>stage.state!=="locked");
    control.disabled=!state.view || !available;
    if(group.stages.includes(state.stage))control.setAttribute("aria-current","step");
    control.addEventListener("click",()=>{const stage=stages.find((stage)=>stage.state!=="approved" && stage.state!=="locked") || stages.find((stage)=>stage.state!=="locked");if(stage)setStage(stage.id);});
    item.append(control);target.append(item);
  }
}
function renderProjects(){
  const target=$("project-list");target.replaceChildren();
  $("projects-status").textContent=state.projects.length?"":"아직 프로젝트가 없습니다.";
  $("projects-status").hidden=state.projects.length>0;
  for(const project of state.projects){const control=node("button","project-item",project.name);control.type="button";control.setAttribute("aria-current",String(currentProject()?.id===project.id));control.addEventListener("click",()=>selectProject(project.id));target.append(control);}
}
function renderRequirementsList(target,project){
  const rows=list(project.candidate?.requirements);
  if(!rows.length)return;
  const section=node("details","compact-details");section.open=!list(project.candidate?.questions).length || state.view.pipeline.confirmed;
  section.append(node("summary",null,"정리한 요구사항"));
  const ul=node("ul","requirements-list");
  for(const item of rows){const li=node("li");li.append(node("strong",null,`${text(item.id)} ${text(item.title) || text(item.text)}`.trim()));if(item.description)li.append(node("p",null,item.description));if(item.origin && item.origin!=="client")li.append(node("p","field-help","제안 · 원문과 구분해 확인하세요."));ul.append(li);}
  section.append(ul);
  const scope=list(project.candidate?.out_of_scope);
  if(scope.length){section.append(node("h3",null,"이번 범위에서 제외"));const ul=node("ul","plain-list");scope.forEach((row)=>ul.append(node("li",null,typeof row==="string"?row:text(row.text)||text(row.title)||text(row.reason))));section.append(ul);}
  target.append(section);
}
function displayDate(value){const date=new Date(value);return Number.isNaN(date.getTime())?"":date.toLocaleString("ko-KR",{dateStyle:"short",timeStyle:"short"});}
function renderAnswerHistory(){
  const answers=list(currentProject()?.answer_history);if(!answers.length)return;
  const section=node("details","compact-details");section.append(node("summary",null,"이전에 저장한 답변"));
  for(const answer of [...answers].reverse()){
    const row=node("article","artifact-row");row.append(node("h3",null,text(answer.question_text)||text(answer.question_id)),node("p",null,text(answer.answer)||"(빈 답변)"),node("p","field-help",displayDate(answer.created_at)));section.append(row);
  }
  $("requirements-work").append(section);
}
function renderAdditionalRequest(){
  const project=currentProject();
  const source=$("source-additions");source.replaceChildren();
  for(const item of list(project?.source_additions)){const row=node("section","source-addition");row.append(node("h3",null,"추가 요청"),node("p",null,text(item.text)),node("small","field-help",displayDate(item.created_at)));source.append(row);}
  if(!project || state.view.pipeline.confirmed || projectWorking())return;
  const section=node("details","compact-details");section.append(node("summary",null,"요구사항 추가·변경"));
  const form=node("form","flow-form"),label=node("label",null,"추가하거나 바꾸고 싶은 내용");label.htmlFor="additional-request";
  const input=node("textarea");input.id=label.htmlFor;input.rows=3;input.maxLength=4000;input.required=true;input.placeholder="새로운 요청이나 바뀐 조건을 적어 주세요. 처음 요청도 함께 보관합니다.";input.value=state.additionalRequests.get(project.id)||"";
  input.addEventListener("input",()=>{if(input.value)state.additionalRequests.set(project.id,input.value);else state.additionalRequests.delete(project.id);updateControls();});
  const save=markAction(button("추가 요청 저장",()=>{}));save.type="submit";form.append(label,input,actionRow(save),node("p","field-help","저장한 뒤 Codex로 다시 정리하면 명세에 반영됩니다."));
  form.addEventListener("submit",async(event)=>{event.preventDefault();if(working()||!input.value.trim())return;await action("add_request",{text:input.value},{beforeRender:()=>state.additionalRequests.delete(project.id),message:"추가 요청을 저장했습니다. 현재 요청을 반영해 다시 정리하세요."});});
  section.append(form);$("requirements-work").append(section);
}
function renderHistory(){
  $("history-panel").hidden=!currentProject();const target=$("pipeline-history");target.replaceChildren();
  const labels={created:"프로젝트 등록",analyze:"요구사항 정리 요청",save_answers:"답변 저장",save_features:"기능 선택 저장",features_saved:"기능 선택 저장",feature_decisions_saved:"기능 선택 저장",collect_reference:"참고 사이트 조사",review:"요구사항 점검",workflow_advice_started:"내용 점검 시작",workflow_advice_recorded:"내용 점검 의견",requirements_confirmed:"요구사항 확정",requirements_reopened:"요구사항 수정 재개",obligations_saved:"운영·정책 저장",generation_started:"초안 생성 시작",artifact_generated:"초안 생성 완료",generation_failed:"초안 생성 실패",artifact_reviewed:"검토·승인",artifact_edited:"파일 수정",erd_edited:"구조 수정",bundle_downloaded:"납품 파일 준비"};
  for(const item of list(state.view?.pipeline?.history)){
    const row=node("article","artifact-row"),stage=item.stage==="requirements"?"요구사항":stageCopy[item.stage]?.title;
    row.append(node("h3",null,`${stage?`${stage} · `:""}${labels[item.kind]||"작업 기록"}`));
    if(item.note)row.append(node("p",null,text(item.note)));
    row.append(node("p","field-help",displayDate(item.at)));target.append(row);
  }
  if(!target.childElementCount)target.append(node("p","field-help","아직 작업 기록이 없습니다."));
}
function answerPayload(){
  const project=currentProject();
  return list(project?.question_answers).map((saved)=>({question_id:saved.question_id,question_digest:saved.question_digest,answer:state.answers.get(answerKey(project,saved))?.answer ?? saved.answer ?? ""}));
}
function hasCurrentDrafts(){const project=currentProject();return Boolean(project && list(project.question_answers).some((saved)=>state.answers.has(answerKey(project,saved))));}
function needsAnalysis(project){return list(project.question_answers).some((saved)=>saved.answer?.trim() && !list(project.candidate_input?.answer_refs).some((ref)=>ref.question_digest===saved.question_digest && ref.answer_revision===saved.answer_revision));}
function clearCurrentAnswers(project){for(const saved of list(project.question_answers))state.answers.delete(answerKey(project,saved));}
function renderSavedDrafts(target,project){
  const drafts=[...state.answers.values()].filter((draft)=>draft.projectId===project.id && draft.candidateRevision!==project.candidate_revision);
  if(!drafts.length)return;
  const section=node("details","compact-details");section.append(node("summary",null,"이전 질문에 작성한 미저장 답변"));
  for(const draft of drafts){const row=node("div","artifact-row");row.append(node("h3",null,draft.question),node("p",null,draft.answer));const discard=node("button","text-button","확인하고 사본 정리");discard.type="button";discard.addEventListener("click",()=>{state.answers.delete(draft.key);render();});row.append(discard);section.append(row);}
  target.append(section);
}
function referenceUrl(value){try{const url=new URL(value);return url.protocol==="https:" && !url.username && !url.password ? url.href:null;}catch{return null;}}
function renderAdvice(target,stage,{editable=true,available=true,artifactKey=null}={}){
  const row=node("section","workflow-advice"),records=list(state.view?.pipeline?.advice).filter((item)=>item.stage===stage),record=records[records.length-1];
  const title=stage==="features"?"선택 내용 점검":stage==="reference"?"참고자료 관련성 점검":"구현 내용 점검";
  if(editable){
    const check=markAction(button(title,()=>action("advise",{stage},{message:"점검을 요청했습니다. 결과는 참고 의견이며 승인은 별도로 진행합니다."})));
    check.dataset.allowed=String(available && state.services?.jev_configured===true);
    if(stage==="features")check.dataset.cleanFeatures="true";
    if(artifactKey)check.dataset.cleanArtifact=artifactKey;
    row.append(actionRow(check));
    if(stage==="features")row.append(node("p","field-help","현재 선택을 저장한 뒤 점검할 수 있습니다."));
  }
  const job=state.view?.pipeline?.advice_job || currentProject()?.advice_job;
  if(job?.stage===stage && job.state==="failed")row.append(node("p","notice error",safeError(job.error)));
  if(record){
    const details=node("details","compact-details");details.open=true;details.append(node("summary",null,`${title} 의견${record.current===false?" · 이전 입력 기준":""}`));
    const labels={requested:"고객 요청과 연결됨",conflicts:"선택 내용이 충돌함",relevant:"프로젝트와 관련 있음",supported:"참고 근거가 구체적임",aligned:"요구사항과 일치함",unsupported_claim:"근거 없는 주장이 포함됨"};
    const rows=list(record.result?.judgments),ul=node("ul","plain-list");
    for(const item of rows){const judgment=item.uncertain || !Number.isFinite(item.probability)?"추가 확인 필요":item.probability>=0.5?"해당할 수 있다는 의견":"해당 가능성이 낮다는 의견";ul.append(node("li",null,`${text(item.item_id)} · ${labels[item.kind]||"내용 확인"}: ${judgment}`));}
    details.append(ul,node("p","field-help","모델의 참고 의견입니다. 실제 작동 검사나 승인을 대신하지 않습니다."));
    if(record.current===false)details.append(node("p","field-help","입력 내용이 바뀌었습니다. 현재 내용으로 다시 점검하세요."));
    row.append(details);
  }
  if(row.childElementCount)target.append(row);
}
function renderResearch(target,project,confirmed){
  const section=node("details","compact-details research-tools");section.append(node("summary",null,"참고 사이트·요구사항 점검"));
  const references=list(project.references);
  if(!confirmed){
    const form=node("form","flow-form"),label=node("label",null,"살펴볼 공개 사이트 주소");label.htmlFor="research-reference-url";
    const input=node("input");input.type="url";input.id=label.htmlFor;input.required=true;input.maxLength=2048;input.placeholder="https://example.com";input.value=state.referenceDrafts.get(project.id)||"";input.dataset.mutating="true";
    input.addEventListener("input",()=>{if(input.value)state.referenceDrafts.set(project.id,input.value);else state.referenceDrafts.delete(project.id);});
    const collect=markAction(button("참고 사이트 살펴보기",()=>{}));collect.type="submit";collect.dataset.allowed=String(state.services?.services?.reference_collect?.available===true);
    const row=node("div","research-actions");row.append(input,collect);form.append(label,row,node("p","field-help","입력한 공개 주소를 서버의 자료 수집 서비스로 보냅니다. 수집 내용은 참고 자료로 보관합니다."));
    if(collect.dataset.allowed==="false")form.append(node("p","field-help","자료 수집은 서버 연결 후 사용할 수 있습니다."));
    form.addEventListener("submit",async(event)=>{event.preventDefault();if(working()||collect.disabled)return;const url=referenceUrl(input.value.trim());if(!url){notice("로그인 정보가 없는 HTTPS 공개 사이트 주소를 입력하세요.",true);return;}const draft=input.value;await action("collect_reference",{url},{beforeRender:()=>{if(state.referenceDrafts.get(project.id)===draft)state.referenceDrafts.delete(project.id);},message:"참고 사이트 조사를 요청했습니다. 수집 결과는 요구사항으로 자동 확정되지 않습니다."});});section.append(form);
    const review=markAction(button("요구사항 점검",()=>action("review",{},{message:"요구사항 점검을 요청했습니다. 결과를 읽고 직접 판단해 주세요."})));review.dataset.allowed=String(Boolean(project.candidate) && state.view.pipeline.analysis_current===true && state.services?.jev_configured===true);review.dataset.cleanIntake="true";
    section.append(actionRow(review),node("p","field-help","고객 원문과 요구사항 사이의 빠진 근거·모순을 살펴봅니다. 점검 결과는 승인이나 실제 기능 검사를 대신하지 않습니다."));
    if(state.services?.jev_configured!==true)section.append(node("p","field-help","요구사항 점검은 서버 연결 후 사용할 수 있습니다."));
  }
  const judgmentLabels={supported:"입력 근거에서 확인됨",unsupported:"입력 근거를 찾지 못함",contradicted:"입력 근거와 모순될 수 있음",unclear:"추가 확인 필요"};
  if(project.jev){const review=node("section","research-result");review.append(node("h3",null,`요구사항 점검 의견${state.view.pipeline.analysis_current===false?" · 이전 입력 기준":""}`));const rows=list(project.jev.items);if(rows.length){const ul=node("ul");for(const item of rows)ul.append(node("li",null,`${text(item.requirement_id)} · ${judgmentLabels[item.judgment]||"판정 내용 확인 필요"}`));review.append(ul);}else review.append(node("p","field-help","표시할 점검 의견이 없습니다."));review.append(node("p","field-help","참고 의견 · 자동 승인하지 않습니다."));section.append(review);}
  for(const item of [...references].reverse()){
    const row=node("article","research-result");row.append(node("h3",null,text(item.title)||"수집한 참고 자료"));const url=referenceUrl(item.url);
    if(url){const link=node("a",null,url);link.href=url;link.target="_blank";link.rel="noopener noreferrer";row.append(link);}
    row.append(node("p","field-help",`${displayDate(item.collected_at)} · 참고용${item.truncated?" · 일부 내용만 수집":""}`));
    if(item.text){const details=node("details");details.append(node("summary",null,"수집 내용 보기"),node("p",null,text(item.text)));row.append(details);}section.append(row);
  }
  renderAdvice(section,"reference",{editable:!confirmed,available:references.length>0});
  target.append(section);
}
function renderRequirements(){
  const project=currentProject(),confirmed=state.view.pipeline.confirmed,target=$("requirements-work");target.replaceChildren();
  $("requirements-heading").textContent=confirmed?"확정한 요구사항":"요구사항 정리";
  $("source-text").textContent=text(project.source?.text);
  if(projectWorking()){
    target.append(node("p","stage-status status-dot",project.state==="EXTRACTING"?"Codex가 요청·답변을 정리하고, 미결정 기능에 맞는 방식을 추천하고 있습니다.":"프로젝트 작업을 진행하고 있습니다."));
    renderRequirementsList(target,project);return;
  }
  const lastExtraction=[...list(project.events)].reverse().find((event)=>event.kind==="candidate_recorded" || (event.kind==="job_failed" && event.payload?.operation==="extract"));
  if(lastExtraction?.kind==="job_failed")target.append(node("p","notice error",safeError(lastExtraction.payload?.code)));
  const lastResearch=[...list(project.events)].reverse().find((event)=>["reference_collected","advice_recorded","job_failed"].includes(event.kind));
  if(lastResearch?.kind==="job_failed" && ["jev","reference","reference_collect"].includes(lastResearch.payload?.operation))target.append(node("p","notice error",safeError(lastResearch.payload?.code)));
  renderResearch(target,project,confirmed);
  if(confirmed){
    renderRequirementsList(target,project);
    guidance.render(target,state.view,{readonly:true});
    renderAdvice(target,"features",{editable:false});
    const go=button("화면 설계로 계속",()=>setStage("wireframe"),true);target.append(actionRow(go));
    const back=node("button","text-button","요구사항 수정으로 돌아가기");back.type="button";back.addEventListener("click",()=>action("return",{}, {message:"요구사항 수정으로 돌아왔습니다. 이후 단계는 다시 확인해야 합니다."}));target.append(markAction(back));return;
  }
  guidance.render(target,state.view);
  if(state.view.pipeline.guidance?.enabled)renderAdvice(target,"features");
  if(!project.candidate){
    target.append(node("h2",null,"요청을 함께 정리해요"),node("p","field-help","원문을 내 Codex로 보내 필요한 질문과 요구사항을 정리합니다."));
    if(!state.view.pipeline.guidance?.enabled){const analyze=markAction(button("내 Codex로 정리",()=>submitAnswers("analyze"),true),true);target.append(actionRow(analyze,true));}return;
  }
  const questions=list(project.candidate.questions);
  const analysisCurrent=state.view.pipeline.analysis_current !== false && !needsAnalysis(project);
  if(questions.length){
    target.append(node("h2",null,"조금 더 알려주세요"),node("p","field-help","Codex가 다음 내용을 확인하고 있어요."));
    const form=node("form","answer-form");let conflict=false;
    questions.forEach((question,index)=>{
      const saved=list(project.question_answers).find((row)=>row.question_id===question.id);if(!saved)return;
      const key=answerKey(project,saved),draft=state.answers.get(key),changed=Boolean(draft && draft.savedRevision!==saved.answer_revision);conflict ||= changed;
      const row=node("div","question-field"),label=node("label",null,`${index+1}. ${text(question.text)}`);label.htmlFor=`answer-${index}`;
      const input=node("textarea");input.id=label.htmlFor;input.rows=3;input.maxLength=2000;input.value=draft?.answer ?? saved.answer ?? "";input.dataset.answerKey=key;input.dataset.mutating="true";
      input.addEventListener("input",()=>{state.answers.set(key,{key,projectId:project.id,candidateRevision:project.candidate_revision,question:question.text,answer:input.value,savedRevision:draft?.savedRevision || saved.answer_revision});const confirm=$("confirm-requirements");if(confirm){confirm.disabled=true;confirm.dataset.allowed="false";}});
      row.append(label,input);
      if(changed){row.append(node("p","field-help",`다른 창에서 저장한 답변: ${saved.answer || "(빈 답변)"}`));const accept=node("button","text-button","현재 답변 확인함 · 내 입력 유지");accept.type="button";accept.addEventListener("click",()=>{draft.savedRevision=saved.answer_revision;render();});row.append(accept);}
      form.append(row);
    });
    const analyze=markAction(button("답변 저장하고 다시 정리",()=>{},true),true);analyze.type="submit";analyze.dataset.allowed=String(!conflict);
    form.append(actionRow(analyze,true));
    const save=node("button","text-button","답변만 저장");save.type="button";save.dataset.allowed=String(!conflict);markAction(save);save.addEventListener("click",()=>submitAnswers("save_answers"));form.append(save);
    form.addEventListener("submit",(event)=>{event.preventDefault();if(!analyze.disabled)submitAnswers("analyze");});
    target.append(form);
  }
  if(!questions.length && !analysisCurrent && !state.view.pipeline.guidance?.enabled){const analyze=markAction(button(list(project.source_additions).length?"추가 요청 반영해 다시 정리":"요구사항 다시 정리",()=>submitAnswers("analyze"),true),true);target.append(actionRow(analyze,true));}
  renderRequirementsList(target,project);
  const canConfirm=!project.answer_summary?.blocking_unanswered && !hasCurrentDrafts() && !guidance.hasCurrentUnsaved(state.view) && !guidance.unresolved(state.view).length && analysisCurrent;
  const confirm=markAction(button("이 내용으로 확정하고 계속",async()=>{const result=await action("confirm",{});if(result){state.stage="wireframe";render();}},!questions.length && analysisCurrent));confirm.id="confirm-requirements";confirm.dataset.allowed=String(canConfirm);
  target.append(actionRow(confirm));
  if(!canConfirm)target.append(node("p","field-help","기능 선택과 필요한 답변을 저장하고 다시 정리한 뒤 명세를 확정할 수 있습니다. ‘잘 모르겠어요’와 ‘나중에 결정’은 확인이 필요합니다."));
  renderSavedDrafts(target,project);
}
async function submitAnswers(actionName){
  if(working())return;const project=currentProject(),answers=answerPayload();
  if(answers.some((answer)=>Array.from(answer.answer).length>2000)){notice("답변은 각각 2,000자까지 입력하세요.",true);return;}
  if(actionName==="analyze"){
    if(!canExecute()){notice("내 계정에서 Codex를 연결하면 입력한 상황에 맞춰 추천받을 수 있습니다.",true);return;}
    if(state.additionalRequests.has(project.id)){notice("작성 중인 추가 요청을 먼저 저장한 뒤 추천을 요청하세요.",true);return;}
    if(guidance.hasConflict(state.view)){notice("다른 창에서 저장한 기능 선택을 확인한 뒤 다시 정리하세요.",true);return;}
    if(hasAnswerConflict(project)){notice("다른 창에서 저장한 답변을 확인한 뒤 다시 정리하세요.",true);return;}
    const sequence=state.sequence;
    if(guidance.hasCurrentUnsaved(state.view) && !await saveFeatureChoices())return;
    if(sequence!==state.sequence || currentProject()?.id!==project.id)return;
  }
  const result=await action(actionName,{answers},{beforeRender:()=>clearCurrentAnswers(project),message:actionName==="save_answers"?"답변을 저장했습니다. 다시 정리하면 요구사항에 반영됩니다.":"내 Codex에 정리를 요청했습니다."});
  if(result)render();
}
function hasAnswerConflict(project){return list(project?.question_answers).some((saved)=>{const draft=state.answers.get(answerKey(project,saved));return draft && draft.savedRevision!==saved.answer_revision;});}
async function saveFeatureChoices(){
  if(working() || !state.view?.pipeline?.guidance?.enabled)return null;
  if(guidance.hasConflict(state.view)){notice("다른 창에서 저장한 기능 선택을 확인한 뒤 저장하세요.",true);return null;}
  const saved=guidance.snapshot(state.view);
  if(saved.decisions.some((item)=>Array.from(item.note).length>1000)){notice("기능별 설명은 1,000자까지 입력하세요.",true);return null;}
  return action("save_features",{decisions:saved.decisions},{revision:saved.revision,beforeRender:()=>guidance.acceptSaved(saved),message:"기능 선택을 저장했습니다. 다시 정리한 뒤 요구사항을 확인하세요."});
}
function renderStageNavigation(target,stage){
  if(!["erd","api","database"].includes(stage.id))return;
  const row=node("div","data-substeps");
  for(const id of ["erd","api","database"]){const selected=currentStages().find((item)=>item.id===id);const link=node("button","text-button",{erd:"ERD",api:"API 계약",database:"DB 생성문"}[id]);link.type="button";link.disabled=!selected || selected.state==="locked";if(id===stage.id)link.setAttribute("aria-current","step");link.addEventListener("click",()=>setStage(id));row.append(link);}
  target.append(row);
}
function renderArtifactFiles(target,artifact,stage){
  const files=node("details","compact-details");files.append(node("summary",null,`생성한 파일 · ${list(artifact.files).length}개`));
  const editable=["wireframe","api","backend","frontend"].includes(stage.id) && stage.can_edit===true;
  const key=`${currentProject().id}:${stage.id}:${artifact.digest}`;
  const outdated=[...state.fileDrafts.entries()].filter(([draftKey])=>draftKey.startsWith(`${currentProject().id}:${stage.id}:`) && draftKey!==key);
  if(outdated.length){
    const retained=node("details","compact-details");retained.append(node("summary",null,"이전 파일에 작성한 미저장 수정"));
    for(const [draftKey,draft] of outdated){for(const file of list(draft.files)){const item=node("details","artifact-row");item.append(node("summary",null,file.path),node("pre","artifact-code",file.content));retained.append(item);}if(draft.note)retained.append(node("p","field-help",draft.note));const discard=node("button","text-button","확인하고 수정 사본 정리");discard.type="button";discard.addEventListener("click",()=>{state.fileDrafts.delete(draftKey);render();});retained.append(discard);}
    target.append(retained);
  }
  const draft=state.fileDrafts.get(key);
  const editedFiles=structuredClone(draft?.files || artifact.files);
  const remember=()=>{state.fileDrafts.set(key,{files:editedFiles,note:state.fileDrafts.get(key)?.note || ""});updateControls();};
  for(const [index,file] of list(artifact.files).entries()){
    const item=node("details","artifact-row");item.append(node("summary",null,text(file.path)));
    if(editable){const label=node("label","field-help",`${text(file.path)} 내용`);label.htmlFor=`artifact-file-${index}`;const input=node("textarea","artifact-code artifact-editor");input.id=label.htmlFor;input.rows=10;input.spellcheck=false;input.value=text(editedFiles[index].content);input.addEventListener("input",()=>{editedFiles[index].content=input.value;remember();});item.append(label,input);}
    else item.append(node("pre","artifact-code",text(file.content)));
    files.append(item);
  }
  if(editable){
    const form=node("form","flow-form"),label=node("label",null,"수정 이유");label.htmlFor="artifact-edit-note";const note=node("textarea");note.id=label.htmlFor;note.rows=2;note.maxLength=2000;note.required=true;note.value=draft?.note || "";note.placeholder="무엇을 수정했고 왜 바꿨는지 적어 주세요.";note.addEventListener("input",()=>{remember();state.fileDrafts.get(key).note=note.value;});
    const save=markAction(button("수정 저장",()=>{}));save.type="submit";form.append(label,note,actionRow(save),node("p","field-help","저장하면 이 단계의 승인과 이후 단계의 결과를 다시 확인해야 합니다."));
    const discard=node("button","text-button","수정 취소");discard.type="button";discard.addEventListener("click",()=>{state.fileDrafts.delete(key);render();});form.append(discard);
    form.addEventListener("submit",async(event)=>{event.preventDefault();if(working()||!note.value.trim())return;await action("edit",{stage:stage.id,files:editedFiles,notes:list(artifact.notes),note:note.value},{beforeRender:()=>state.fileDrafts.delete(key),message:"수정한 파일을 저장했습니다. 변경 내용을 검토한 뒤 다시 승인하세요."});});files.append(form);
  }
  target.append(files);
  const notes=list(artifact.notes);
  if(notes.length){const section=node("details","compact-details");section.append(node("summary",null,"확인할 내용"));const ul=node("ul","plain-list");notes.forEach((value)=>ul.append(node("li",null,text(value))));section.append(ul);target.append(section);}
  if(artifact.checks && Object.keys(artifact.checks).length){const checks=node("details","compact-details");checks.append(node("summary",null,"검증 기록"),node("pre","artifact-code",JSON.stringify(artifact.checks,null,2)));target.append(checks);}
}
function renderArtifact(){
  const stage=currentStages().find((stage)=>stage.id===state.stage);if(!stage)return;
  const copy=stageCopy[stage.id] || {},target=$("artifact-work");target.replaceChildren();
  $("artifact-heading").textContent=copy.title;$("artifact-description").textContent=copy.description;
  renderStageNavigation(target,stage);
  if(stage.state==="locked"){target.append(node("p","stage-status","앞 단계의 결과를 확인하고 승인하면 이 단계를 시작할 수 있습니다."));return;}
  if(stage.state==="running"){target.append(node("p","stage-status status-dot",`${copy.title} 작업을 진행하고 있습니다.`));return;}
  const failed=state.view.pipeline.job?.state==="failed" && state.view.pipeline.job.stage===stage.id;
  if(failed)target.append(node("p","notice error",safeError(state.view.pipeline.job.error)));
  if(stage.state==="stale")target.append(node("p","notice",stage.can_edit?"앞 단계의 내용이 변경되었습니다. 파일을 수정하고 이유와 함께 저장하거나 초안을 다시 만들어 주세요.":"앞 단계의 내용이 변경되었습니다. 현재 명세에 맞춰 초안을 다시 만들어 주세요."));
  if(stage.artifact){
    target.append(node("p","stage-status",stage.state==="approved"?"검토 기록과 함께 승인했습니다.":"초안을 만들었습니다. 파일과 확인할 내용을 검토하세요."));
    if(["wireframe","frontend"].includes(stage.id) && ["generated","approved"].includes(stage.state)){
      const preview=node("details","compact-details");preview.append(node("summary",null,"화면 미리보기"),node("p","field-help","정적 화면입니다. 실제 앱 동작은 별도 실행으로 확인하세요."));
      const frame=node("iframe","artifact-preview");frame.setAttribute("sandbox","");frame.title=`${copy.title} 화면 미리보기`;frame.loading="lazy";frame.referrerPolicy="no-referrer";
      preview.append(frame);preview.addEventListener("toggle",()=>{if(preview.open && !frame.getAttribute("src"))frame.src=`/studio-preview/${encodeURIComponent(currentProject().id)}/${stage.id}`;});target.append(preview);
    }
    if(stage.id==="erd"){
      const draft=currentProject().erd_draft,count=list(draft?.result?.schema?.entities).length;
      target.append(node("p","field-help",count?`테이블 ${count}개 · 실제 운영 DB에는 적용하지 않았습니다.`:"저장할 데이터가 없는 초안입니다. 설계 근거를 확인하세요."));
      if(currentProject().erd_current){const link=node("a","button secondary","ERD·DB 편집 열기");link.href=`/editor?delivery=${encodeURIComponent(currentProject().id)}`;link.addEventListener("click",(event)=>{if(hasUnsaved()&&!confirm("저장하지 않은 내용이 있습니다. 편집기로 이동할까요?"))event.preventDefault();});target.append(actionRow(link));}
      const rationale=node("details","compact-details");rationale.append(node("summary",null,"설계 근거"));const ul=node("ul","plain-list");for(const item of list(draft?.result?.traceability))ul.append(node("li",null,`${item.entity} · ${list(item.requirement_ids).join(", ")}`));for(const item of list(draft?.result?.unmapped_requirements))ul.append(node("li",null,`${item.requirement_id} · DB 미반영: ${item.reason}`));if(draft?.traceability_current===false)rationale.append(node("p","field-help","수동 편집한 구조와 요구사항의 연결 근거를 다시 확인하세요."));rationale.append(ul);target.append(rationale);
    }
    renderArtifactFiles(target,stage.artifact,stage);
    renderAdvice(target,stage.id,{available:["generated","approved"].includes(stage.state),artifactKey:`${currentProject().id}:${stage.id}:${stage.artifact.digest}`});
  }
  if(stage.can_approve){
    const form=node("form","flow-form");const label=node("label",null,"검토한 내용");label.htmlFor="approval-note";const input=node("textarea");input.id="approval-note";input.rows=2;input.maxLength=2000;input.required=true;const key=`${currentProject().id}:${stage.id}:${stage.artifact.digest}`;input.value=state.notes.get(key)||"";input.placeholder="확인한 항목과 남은 작업을 짧게 적어 주세요.";input.addEventListener("input",()=>state.notes.set(key,input.value));form.append(label,input);
    const approve=markAction(button(stage.id==="delivery"?"검토 기록 저장하고 납품 준비":"검토 기록 저장하고 다음 단계",()=>{},true));approve.type="submit";approve.dataset.cleanArtifact=key;if(stage.id==="delivery")approve.dataset.cleanPolicies="true";form.append(actionRow(approve));
    const unsaved=node("p","field-help","수정한 파일을 먼저 저장한 뒤 이 결과를 승인하세요.");unsaved.id="artifact-unsaved-message";unsaved.dataset.artifact=key;unsaved.hidden=!state.fileDrafts.has(key);form.append(unsaved);
    form.addEventListener("submit",async(event)=>{event.preventDefault();if(!input.value.trim()||working()||approve.disabled)return;const result=await action("approve",{stage:stage.id,note:input.value},{beforeRender:()=>state.notes.delete(key)});if(result){state.stage=nextStage();render();}});target.append(form);
  }else if(stage.state==="approved"){
    if(stage.id==="delivery" && state.view.pipeline.ready_for_delivery){const bundle=markAction(button("납품 파일 내려받기",downloadBundle,true));bundle.dataset.cleanPolicies="true";target.append(actionRow(bundle));target.append(node("p","field-help","파일 묶음을 내려받습니다. 실제 앱 실행·운영 환경 검수와 공개는 별도로 진행하세요."));}
    else {const next=markAction(button("다음 단계로 계속",()=>setStage(nextStage()),true));next.dataset.cleanArtifact=`${currentProject().id}:${stage.id}:${stage.artifact.digest}`;target.append(actionRow(next));}
  }
  if(stage.can_generate){
    const generated=Boolean(stage.artifact)&&stage.state!=="stale";
    const create=markAction(button(generated?"초안 다시 만들기":copy.create,()=>action("generate",{stage:stage.id}),!generated),copy.ai);
    if(stage.id==="delivery")create.dataset.cleanPolicies="true";
    if(stage.id==="delivery" && !state.view.pipeline.obligations.assessment.ready){create.dataset.allowed="false";target.append(node("p","field-help","운영·정책 7개 항목을 먼저 완성해 주세요."));const open=node("button","text-button","운영·정책 입력하기");open.type="button";open.addEventListener("click",()=>{$("obligations-panel").open=true;$("obligations-panel").scrollIntoView({block:"start",behavior:"smooth"});});target.append(open);}
    target.append(actionRow(create,copy.ai));
  }
  if(stage.id==="delivery"){const unsaved=node("p","field-help","수정한 운영·정책을 먼저 저장한 뒤 납품 작업을 계속하세요.");unsaved.id="delivery-unsaved-message";unsaved.hidden=!policy.hasCurrentUnsaved(currentProject()?.id);target.append(unsaved);}
}
function render(){
  const project=currentProject();$("header-project").textContent=project?.name || "새 프로젝트";document.title=`ChannelShift · ${project?.name || "새 프로젝트"}`;
  $("new-project-panel").hidden=Boolean(project);$("requirements-panel").hidden=!project || state.stage!=="requirements";$("artifact-panel").hidden=!project || state.stage==="requirements";
  renderSteps();renderProjects();
  if(project){if(state.stage==="requirements"){renderRequirements();renderAnswerHistory();renderAdditionalRequest();}else renderArtifact();}
  policy.render(state.view);renderHistory();updateControls();state.renderedRevision=state.view?.pipeline?.revision;
}
async function refreshProjects(){const result=await api("/api/studio/projects");state.projects=list(result.items);renderProjects();}
function stopPoll(){clearTimeout(state.timer);state.timer=null;}
function schedulePoll(){stopPoll();if(!document.hidden && currentProject() && working())state.timer=setTimeout(poll,1500);}
async function poll(){
  const id=currentProject()?.id,sequence=state.sequence;if(!id||document.hidden)return;
  try{const result=await api(`/api/studio/projects/${encodeURIComponent(id)}`);if(sequence!==state.sequence)return;const changed=result.pipeline.revision!==state.view.pipeline.revision;state.view=result;if(changed)render();}
  catch(error){if(sequence===state.sequence)notice(error.message,true);}
  finally{if(sequence===state.sequence)schedulePoll();}
}
async function selectProject(id){
  if(state.busy)return;policy.remember();const sequence=++state.sequence;stopPoll();notice("");
  try{const result=await api(`/api/studio/projects/${encodeURIComponent(id)}`);if(sequence!==state.sequence)return;state.view=result;state.stage=nextStage();const url=new URL(location.href);url.searchParams.set("project",id);history.replaceState(null,"",url);render();schedulePoll();}
  catch(error){if(sequence===state.sequence)notice(error.message,true);}
}
async function action(actionName,payload,{revision,message,beforeRender,quiet=false}={}){
  if(!currentProject() || state.busy)return null;
  state.busy=true;updateControls();if(!quiet)notice("");const sequence=state.sequence;
  try{
    const result=await api("/api/studio/action",{project_id:currentProject().id,expected_revision:revision || state.view.pipeline.revision,action:actionName,payload});
    if(sequence!==state.sequence)return null;
    beforeRender?.();state.view=result;render();if(message)notice(message);return result;
  }catch(error){
    if(sequence===state.sequence){notice(error.message,true);if(error.status===409){try{state.view=await api(`/api/studio/projects/${encodeURIComponent(currentProject().id)}`);render();}catch{}}}
    if(quiet)throw error;return null;
  }finally{if(sequence===state.sequence){state.busy=false;updateControls();schedulePoll();}}
}
async function downloadBundle(){
  if(working()||!state.view?.pipeline?.ready_for_delivery||policy.hasCurrentUnsaved(currentProject()?.id))return;state.busy=true;updateControls();
  try{const blob=await api("/api/studio/bundle",{project_id:currentProject().id,expected_revision:state.view.pipeline.revision},true);download(blob,"channelshift-project.zip");state.view=await api(`/api/studio/projects/${encodeURIComponent(currentProject().id)}`);notice("납품 파일을 준비했습니다. 브라우저의 다운로드를 확인하세요.");render();}
  catch(error){notice(error.message,true);}finally{state.busy=false;updateControls();}
}
$("new-project").addEventListener("click",()=>{if(state.busy)return;policy.remember();++state.sequence;stopPoll();state.view=null;state.stage="requirements";notice("");const url=new URL(location.href);url.searchParams.delete("project");history.replaceState(null,"",url);render();$("project-request").focus();});
$("new-project-form").addEventListener("input",()=>{state.newDirty=true;});
$("new-project-form").addEventListener("submit",async(event)=>{
  event.preventDefault();if(state.busy)return;state.busy=true;updateControls();notice("");
  try{const result=await api("/api/studio/projects",{name:$("project-name").value.trim(),client_request:$("project-request").value,site_type:$("site-type").value});state.view=result;state.stage="requirements";state.newDirty=false;$("new-project-form").reset();const url=new URL(location.href);url.searchParams.set("project",result.project.id);history.replaceState(null,"",url);await refreshProjects();render();}
  catch(error){notice(error.message,true);}finally{state.busy=false;updateControls();}
});
window.addEventListener("beforeunload",(event)=>{if(hasUnsaved()){event.preventDefault();event.returnValue="";}});
window.addEventListener("pagehide",stopPoll);
document.addEventListener("visibilitychange",()=>{if(document.hidden)stopPoll();else if(currentProject()){poll();}});
async function init(){
  render();
  const results=await Promise.allSettled([api("/api/studio/catalog"),api("/api/studio/projects"),api("/api/delivery/status")]);
  if(results[0].status==="fulfilled")policy.setCatalog(results[0].value.obligations);else notice(results[0].reason.message,true);
  if(results[1].status==="fulfilled")state.projects=list(results[1].value.items);else notice(results[1].reason.message,true);
  if(results[2].status==="fulfilled"){state.services=results[2].value;account.setStatus(results[2].value);}else notice(results[2].reason.message,true);
  const id=new URL(location.href).searchParams.get("project");
  if(id && /^[a-f0-9]{32}$/.test(id))await selectProject(id);else render();
}
init();
