import { $, node, list, text } from "/studio-dom.js";

export function createPolicyEditor(save,onChange = () => {}) {
  let catalog = null, currentId = null, currentRevision = null, values = null, baseline = null, dirty = false, busy = false;
  const drafts = new Map();
  const read = () => {
    const result = structuredClone(values || catalog?.empty_values || {});
    $("obligations-fields").querySelectorAll("[data-field]").forEach((input) => { const [group,key] = input.dataset.field.split("."); result[group][key] = input.value; });
    return result;
  };
  function remember() { if (dirty && currentId) drafts.set(currentId, {values:read(), revision:currentRevision,baseline}); }
  function render(view, force = false) {
    const id = view?.project?.id;
    const record = view?.pipeline?.obligations;
    $("obligations-wrap").hidden = !id;
    if (!id || !catalog) return;
    const assessment = record?.assessment;
    $("obligations-count").textContent = assessment ? `필수 7개 항목 · ${Number(assessment.complete_count) || 0}개 작성됨` : "필수 7개 항목";
    $("save-obligations").disabled = busy;
    if (currentId === id && dirty && !force) {
      if (JSON.stringify(record?.values) === baseline) currentRevision = view.pipeline.revision;
      else if (currentRevision !== view.pipeline.revision && !document.getElementById("policy-conflict")) {
        const conflict=node("div","notice");conflict.id="policy-conflict";
        conflict.append(node("p",null,"다른 창에서 운영·정책을 수정했습니다. 현재 저장 내용을 확인한 뒤 내 입력을 저장하세요."));
        const details=node("details");details.append(node("summary",null,"현재 저장된 운영·정책"));
        for(const [path,meta] of Object.entries(catalog.fields || {})){const [group,key]=path.split(".");details.append(node("strong",null,meta.label),node("p",null,text(record?.values?.[group]?.[key]) || "(입력 없음)"));}
        const rebase=node("button","text-button","현재 내용을 확인하고 내 입력 유지");rebase.type="button";rebase.addEventListener("click",()=>{currentRevision=view.pipeline.revision;baseline=JSON.stringify(record?.values);conflict.remove();$("obligations-message").textContent="내 입력을 유지했습니다. 저장하려면 운영·정책 저장을 누르세요.";});
        conflict.append(details,rebase);$("obligations-fields").prepend(conflict);
      }
      return;
    }
    remember(); currentId = id;
    const draft = drafts.get(id);
    currentRevision = draft?.revision || view.pipeline.revision;
    values = structuredClone(draft?.values || record?.values || catalog.empty_values);
    baseline = draft?.baseline || JSON.stringify(record?.values);
    dirty = Boolean(draft);
    const target = $("obligations-fields"); target.replaceChildren();
    for (const item of list(catalog.items)) {
      const section = node("fieldset","obligation-item");
      const legend = node("legend",null,item.label);
      if (list(assessment?.missing_items).includes(item.id)) legend.append(node("span","missing-label","입력 필요"));
      section.append(legend);
      const grid = node("div","field-grid");
      for (const path of list(item.fields)) {
        const meta = catalog.fields?.[path] || {};
        const [group,key] = path.split(".");
        const holder = node("div",meta.multiline || path === "company.address" ? "wide" : "");
        const label = node("label",null,meta.label || path); label.htmlFor = `policy-${group}-${key}`;
        const input = node(meta.multiline ? "textarea" : "input"); input.id = label.htmlFor; input.dataset.field = path; input.value = text(values[group]?.[key]);
        if (meta.max_length) input.maxLength = meta.max_length;
        if (!meta.multiline) input.type = path === "contact.email" ? "email" : path === "contact.phone" ? "tel" : "text";
        input.autocomplete = "off";
        input.addEventListener("input",() => { dirty = true; $("obligations-message").textContent = "저장하지 않은 내용이 있습니다."; onChange(); });
        holder.append(label,input); grid.append(holder);
      }
      section.append(grid); target.append(section);
    }
  }
  $("obligations-form").addEventListener("submit",async(event) => {
    event.preventDefault(); if (!currentId || busy) return;
    busy = true; $("save-obligations").disabled = true; $("obligations-message").textContent = "저장 중…";
    try {
      const result = await save({values:read()},currentRevision);
      if (!result) throw new Error("다른 작업이 진행 중입니다. 완료 후 다시 저장하세요.");
      drafts.delete(currentId); dirty = false; currentRevision = result.pipeline.revision;
      render(result,true); $("obligations-message").textContent = "저장했습니다.";
    } catch (error) { remember(); $("obligations-message").textContent = error.message; }
    finally { busy = false; $("save-obligations").disabled = false; onChange(); }
  });
  return {setCatalog(value){catalog=value;}, render, hasUnsaved:() => dirty || drafts.size > 0, hasCurrentUnsaved:(id) => (currentId===id && dirty) || drafts.has(id), remember, resetRevision(revision){if (!dirty) currentRevision=revision;} };
}
