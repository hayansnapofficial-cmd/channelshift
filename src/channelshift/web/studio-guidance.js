import { node, list, text, button } from "/studio-dom.js";

const decisions = (guidance) => list(guidance?.cards).map((card) => ({id:card.id,option:card.selected ?? null,note:text(card.note)}));
const signature = (guidance) => JSON.stringify({cards:list(guidance?.cards).map((card) => ({id:card.id,options:list(card.options).map((option) => option.id)})),decisions:decisions(guidance)});
const keyFor = (view) => `${view?.project?.id}:${view?.pipeline?.guidance?.catalog_version}`;

// Drafts stay in this tab, scoped to project and catalog. A new server revision
// may rebase unchanged choices, but cannot silently replace a local decision.
export function createFeatureGuidance({onChange = () => {},onSave,onAnalyze,onUnsure = () => {},onRender = () => {}}) {
  const drafts = new Map();
  function current(view) {
    const draft=drafts.get(keyFor(view));
    if(draft && draft.baseline===signature(view.pipeline.guidance))draft.revision=view.pipeline.revision;
    return draft;
  }
  function hasConflict(view) {const draft=current(view);return Boolean(draft && draft.baseline!==signature(view.pipeline.guidance));}
  function payload(view) {return structuredClone(current(view)?.decisions || decisions(view?.pipeline?.guidance));}
  function remember(view,id,change) {
    const key=keyFor(view),existing=current(view),rows=payload(view),row=rows.find((item)=>item.id===id);
    if(!row)return;
    Object.assign(row,change);
    if(!existing && JSON.stringify(rows)===JSON.stringify(decisions(view.pipeline.guidance)))return;
    const draft=existing || {baseline:signature(view.pipeline.guidance),revision:view.pipeline.revision};
    draft.decisions=rows;
    if(draft.baseline===signature(view.pipeline.guidance) && JSON.stringify(rows)===JSON.stringify(decisions(view.pipeline.guidance)))drafts.delete(key);
    else drafts.set(key,draft);
    onChange();
  }
  function snapshot(view) {
    const draft=current(view);
    return {key:keyFor(view),revision:draft?.revision || view.pipeline.revision,decisions:payload(view)};
  }
  function acceptSaved(saved) {
    const draft=drafts.get(saved.key);
    if(draft && JSON.stringify(draft.decisions)===JSON.stringify(saved.decisions))drafts.delete(saved.key);
  }
  function unresolved(view) {
    const guidance=view?.pipeline?.guidance;
    if(!guidance?.enabled)return [];
    const rows=payload(view);
    return list(guidance.cards).filter((card)=>{const option=rows.find((row)=>row.id===card.id)?.option;return !option || ["unsure","later"].includes(option) || !list(card.options).some((item)=>item.id===option);}).map((card)=>card.id);
  }
  function render(target,view,{readonly=false,busy=false}={}) {
    const guidance=view?.pipeline?.guidance;
    if(!guidance?.enabled || !list(guidance.cards).length)return;
    const rows=payload(view),conflict=hasConflict(view),section=node("section","feature-guidance");
    section.setAttribute("aria-labelledby","feature-guidance-heading");
    const heading=node("h2",null,"함께 결정할 기능");heading.id="feature-guidance-heading";
    const progress=node("p","field-help feature-progress");progress.id="feature-progress";progress.setAttribute("role","status");
    const refreshProgress=()=>{const remaining=unresolved(view).length;progress.textContent=remaining?`${list(guidance.cards).length}개 중 ${remaining}개 확인 필요 · 버튼을 고르고 아래에 원하는 방식을 적어 주세요.`:"기능을 모두 선택했습니다. 요구사항에 반영한 뒤 내용을 확인하세요.";};refreshProgress();
    section.append(heading,progress);
    if(conflict){
      const warning=node("div","notice feature-conflict");warning.append(node("p",null,"다른 창에서 기능 선택이 바뀌었습니다. 내 입력을 보관하고 있습니다."));
      const saved=node("details");saved.append(node("summary",null,"현재 저장된 선택 보기"));
      for(const card of list(guidance.cards)){const selected=list(card.options).find((option)=>option.id===card.selected);saved.append(node("p",null,`${text(card.title)}: ${text(selected?.label)||"선택 전"}${card.note?` · ${card.note}`:""}`));}
      const keep=button("저장된 선택 확인 · 내 입력 유지",()=>{const draft=current(view);if(draft){draft.baseline=signature(guidance);draft.revision=view.pipeline.revision;}onRender();});keep.className="text-button";keep.disabled=busy || readonly;
      warning.append(saved,keep);section.append(warning);
    }
    for(const [index,card] of list(guidance.cards).entries()){
      const value=rows.find((row)=>row.id===card.id) || {option:null,note:""};
      const field=node("fieldset","feature-card"),legend=node("legend",null,`${index+1}. ${text(card.title)}`);field.append(legend);
      if(card.description)field.append(node("p","feature-description",card.description));
      const rationale=node("p","field-help feature-rationale",[text(card.why),card.effort?`작업 규모: ${text(card.effort)}`:""].filter(Boolean).join(" · "));field.append(rationale);
      const options=node("div","feature-options"),explanation=node("p","field-help feature-option-help");explanation.id=`feature-explanation-${index}`;explanation.setAttribute("aria-live","polite");
      const controls=[];
      for(const option of list(card.options)){
        const control=button(option.label,()=>{remember(view,card.id,{option:option.id});value.option=option.id;controls.forEach((item)=>item.setAttribute("aria-pressed",String(item.dataset.option===option.id)));explanation.textContent=text(option.description);refreshProgress();if(option.id==="unsure")onUnsure();});
        control.className="feature-option";control.dataset.option=option.id;control.dataset.mutating="true";control.dataset.allowed=String(!readonly);control.setAttribute("aria-pressed",String(value.option===option.id));control.setAttribute("aria-describedby",explanation.id);control.disabled=readonly||busy;
        controls.push(control);options.append(control);
      }
      explanation.textContent=text(list(card.options).find((option)=>option.id===value.option)?.description) || "아직 선택하지 않았습니다.";
      const label=node("label","feature-note-label","원하는 방식이나 궁금한 점");label.htmlFor=`feature-note-${index}`;
      const input=node("textarea","feature-note");input.id=label.htmlFor;input.rows=2;input.maxLength=1000;input.value=value.note;input.placeholder="잘 모르겠다면 상황을 편하게 설명해 주세요. 선택한 내용에 조건을 덧붙여도 됩니다.";input.dataset.mutating="true";input.dataset.allowed=String(!readonly);input.disabled=readonly||busy;
      input.addEventListener("input",()=>{remember(view,card.id,{note:input.value});});
      field.append(options,explanation,label,input);
      const inputFeatures=view.project.candidate_input?.feature_decisions;
      const basisCurrent=view.pipeline.analysis_current===true && inputFeatures?.catalog_version===guidance.catalog_version && view.project.candidate_input?.feature_revision===view.project.feature_revision && decisions(guidance).every((row)=>list(inputFeatures?.decisions).some((saved)=>saved.id===row.id && saved.option===row.option && text(saved.note)===row.note));
      const recommendation=list(view.project.candidate?.feature_recommendations).find((item)=>item.card_id===card.id),recommendedOption=list(card.options).find((option)=>option.id===recommendation?.option_id && !["unsure","later"].includes(option.id));
      if(basisCurrent && card.selected==="unsure" && recommendation && recommendedOption){
        const advice=node("div","feature-recommendation");advice.dataset.featureRecommendation="true";advice.hidden=Boolean(current(view));
        advice.append(node("strong",null,`Codex 추천 · ${text(recommendedOption.label)}`),node("p",null,text(recommendation.reason)),node("p","field-help",`함께 고려할 점: ${text(recommendation.tradeoff)}`));
        if(!readonly){const accept=button("이 추천으로 선택",()=>{remember(view,card.id,{option:recommendedOption.id});onRender();});accept.dataset.mutating="true";advice.append(accept);}
        advice.append(node("p","field-help","추천은 자동 선택되지 않습니다. 선택·저장 후 다시 정리해 주세요."));field.append(advice);
      }
      section.append(field);
    }
    const oldDrafts=[...drafts.entries()].filter(([key])=>key.startsWith(`${view.project.id}:`) && key!==keyFor(view));
    if(oldDrafts.length){const retained=node("details","compact-details");retained.append(node("summary",null,"이전 기능 목록에 작성한 미저장 선택"));for(const [key,draft] of oldDrafts){retained.append(node("pre","artifact-code",JSON.stringify(draft.decisions,null,2)));const discard=button("확인하고 이전 사본 정리",()=>{drafts.delete(key);onRender();});discard.className="text-button";retained.append(discard);}section.append(retained);}
    if(!readonly){
      const actions=node("div","action-row"),save=button("선택만 저장",()=>onSave()),analyze=button("선택·답변 반영해 정리",()=>onAnalyze(),true);
      save.id="save-feature-decisions";analyze.id="analyze-feature-decisions";
      for(const control of [save,analyze]){control.dataset.mutating="true";control.dataset.featureSave="true";control.dataset.allowed=String(!conflict);control.disabled=busy||conflict;}
      analyze.dataset.ai="true";actions.append(save,analyze);
      const status=node("p","field-help");status.id="feature-draft-status";status.setAttribute("role","status");status.textContent=current(view)?"저장하지 않은 선택이 있습니다.":"저장 후 다시 정리해야 요구사항에 반영됩니다. 확정은 별도로 진행합니다.";
      section.append(actions,status);
    }
    target.append(section);
  }
  return {render,snapshot,acceptSaved,hasConflict,unresolved,hasUnsaved:()=>drafts.size>0,hasCurrentUnsaved:(view)=>Boolean(current(view))};
}
