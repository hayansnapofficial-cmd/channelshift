"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const types = ["uuid", "varchar", "text", "integer", "bigint", "decimal", "boolean", "date", "timestamp", "json"];
  const state = {
    schema: emptySchema(), templates: [], projects: [], selectedTemplate: null,
    topic: null, savedId: null, dirty: false, revision: 0, busy: false,
    view: "entities", format: "sql", files: [], fileIndex: 0, notificationTimer: null, confirmResolver: null,
  };
  const errors = {
    invalid_schema: "구조가 올바르지 않습니다. 구조 검증에서 수정할 항목을 확인하세요.",
    validation_failed: "구조가 올바르지 않습니다. 구조 검증에서 수정할 항목을 확인하세요.",
    invalid_package: "Java 패키지 이름을 확인하세요. 예: com.example.app",
    invalid_java_package: "Java 패키지 이름을 확인하세요. 예: com.example.app",
    unsupported_conversion: "현재 데이터베이스에서 지원하지 않는 변환입니다. 다른 데이터베이스를 선택하거나 구조를 조정하세요.",
    not_found: "요청한 항목을 찾을 수 없습니다. 목록을 새로고침하세요.",
    csrf_failed: "연결 확인이 만료되었습니다. 편집 내용을 JSON으로 내려받은 뒤 페이지를 새로고침하세요.",
    forbidden: "연결을 확인할 수 없습니다. 편집 내용을 JSON으로 내려받은 뒤 페이지를 새로고침하세요.",
    body_too_large: "파일이 너무 큽니다. 2 MiB 이하의 설계 파일을 사용하세요.",
    too_large: "파일이 너무 큽니다. 2 MiB 이하의 설계 파일을 사용하세요.",
  };

  function emptySchema() {
    return { format: "channelshift.schema/v1", name: "새 프로젝트", database: "postgresql", entities: [], relations: [] };
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function button(text, className, handler, label) {
    const node = element("button", className, text);
    node.type = "button";
    if (label) node.setAttribute("aria-label", label);
    node.addEventListener("click", handler);
    return node;
  }

  function input(value, label, onChange, options = {}) {
    const { commitOn = "input", ...attributes } = options;
    const node = element("input");
    node.type = attributes.type || "text";
    node.value = value === undefined || value === null ? "" : String(value);
    node.setAttribute("aria-label", label);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    node.addEventListener(commitOn, () => onChange(node.value, node));
    return node;
  }

  function option(value, text) {
    const node = element("option", null, text === undefined ? value : text);
    node.value = value;
    return node;
  }

  function select(values, value, label, onChange) {
    const node = element("select");
    node.setAttribute("aria-label", label);
    values.forEach((item) => node.append(option(item)));
    node.value = value;
    node.addEventListener("change", () => onChange(node.value));
    return node;
  }

  function checkbox(checked, label, onChange) {
    const node = element("input");
    node.type = "checkbox";
    node.checked = Boolean(checked);
    node.setAttribute("aria-label", label);
    node.addEventListener("change", () => onChange(node.checked));
    return node;
  }

  function snapshot() { return JSON.parse(JSON.stringify(state.schema)); }

  async function api(path, body) {
    const token = document.querySelector('meta[name="channelshift-token"]').content;
    const options = { headers: { "X-ChannelShift-Token": token }, credentials: "same-origin", cache: "no-store" };
    if (body !== undefined) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try { response = await fetch(path, options); }
    catch { throw new Error("서버에 연결할 수 없습니다. 로컬 ChannelShift 서버가 실행 중인지 확인하세요."); }
    let data;
    try { data = await response.json(); }
    catch { throw new Error("서버 응답을 읽지 못했습니다. 잠시 후 다시 시도하세요."); }
    if (!response.ok || data.ok !== true) {
      const code = typeof data.error === "string" ? data.error : "unknown_error";
      const safeCode = /^[a-z0-9_]{1,80}$/.test(code) ? code : "unknown_error";
      throw new Error(errors[safeCode] || `요청을 완료하지 못했습니다. 오류 코드: ${safeCode}`);
    }
    return data;
  }

  function notify(message, isError = false) {
    const node = $("notification");
    node.textContent = message;
    node.classList.toggle("error", isError);
    node.hidden = false;
    clearTimeout(state.notificationTimer);
    state.notificationTimer = setTimeout(() => { node.hidden = true; }, isError ? 12000 : 6000);
  }

  async function run(action) {
    if (state.busy) return;
    state.busy = true;
    updateButtons();
    try { await action(); }
    catch (error) { notify(error.message || "작업을 완료하지 못했습니다.", true); }
    finally { state.busy = false; updateButtons(); }
  }

  function updateButtons() {
    ["save-project", "generate-template", "validate", "generate-output", "refresh-projects", "import-trigger", "new-project"].forEach((id) => {
      $(id).disabled = state.busy || (id === "generate-template" && !state.selectedTemplate);
    });
    $("add-relation").disabled = !state.schema.entities.length;
    $("project-name").disabled = state.busy;
    $("database").disabled = state.busy;
    document.querySelector(".app-layout").inert = state.busy;
    document.querySelector(".app-layout").setAttribute("aria-busy", String(state.busy));
    $("save-state").textContent = state.busy ? "처리 중…" : state.dirty ? "저장하지 않은 변경" : state.savedId ? "저장됨" : "저장 전";
  }

  function updateCounts() {
    $("entity-count").textContent = String(state.schema.entities.length);
    $("relation-count").textContent = String(state.schema.relations.length);
    $("empty-state").hidden = state.schema.entities.length > 0;
    updateButtons();
  }

  function changed() {
    state.dirty = true;
    state.revision += 1;
    state.files = [];
    state.fileIndex = 0;
    $("validation-title").textContent = "구조가 변경되었습니다. 다시 검증하세요.";
    $("validation-dot").className = "status-dot";
    $("validation-issues").hidden = true;
    renderOutput();
    updateCounts();
  }

  function confirmDialog(message) {
    if (state.confirmResolver) return Promise.resolve(false);
    return new Promise((resolve) => {
      state.confirmResolver = resolve;
      $("confirm-message").textContent = message;
      $("confirm-dialog").returnValue = "cancel";
      $("confirm-dialog").showModal();
      $("confirm-cancel").focus();
    });
  }

  async function confirmReplace({ skipEmpty = false } = {}) {
    if (!state.dirty || (skipEmpty && !state.schema.entities.length && !state.schema.relations.length)) return true;
    return confirmDialog("저장하지 않은 변경이 있습니다. 현재 편집 내용을 바꾸시겠습니까? 필요하면 먼저 버전 저장 또는 JSON 다운로드를 해 주세요.");
  }

  function applySchema(schema, { savedId = null, topic = null, dirty = false } = {}) {
    state.schema = schema;
    state.savedId = savedId;
    state.topic = topic;
    state.dirty = dirty;
    state.revision += 1;
    state.files = [];
    state.fileIndex = 0;
    $("project-name").value = schema.name;
    $("database").value = schema.database;
    $("validation-title").textContent = "저장 전에 구조를 확인하세요.";
    $("validation-dot").className = "status-dot";
    $("validation-issues").hidden = true;
    renderEntities();
    renderRelations();
    renderOutput();
    renderProjects();
    updateCounts();
    switchView("entities");
  }

  async function loadTemplates() {
    const result = await api("/api/templates");
    state.templates = result.items;
    if (state.templates.length) state.selectedTemplate = state.templates[0].id;
    renderTemplates();
    updateButtons();
  }

  function renderTemplates() {
    const list = $("template-list");
    list.replaceChildren(element("legend", "sr-only", "시작할 템플릿 선택"));
    if (!state.templates.length) {
      list.append(element("p", "subtle", "사용할 수 있는 템플릿이 없습니다."));
      return;
    }
    state.templates.forEach((template) => {
      const label = element("label", "template-option");
      const radio = element("input");
      radio.type = "radio";
      radio.name = "template";
      radio.value = template.id;
      radio.checked = state.selectedTemplate === template.id;
      radio.addEventListener("change", () => { state.selectedTemplate = template.id; updateButtons(); });
      const copy = element("span");
      copy.append(element("strong", null, template.title), element("small", null, template.description), element("small", "template-count", `테이블 ${template.tableCount}개`));
      label.append(radio, copy);
      list.append(label);
    });
  }

  async function loadProjects() {
    const result = await api("/api/projects");
    state.projects = result.items;
    renderProjects();
  }

  function renderProjects() {
    const list = $("project-list");
    list.replaceChildren();
    if (!state.projects.length) {
      list.append(element("p", "empty-versions", "아직 저장한 버전이 없습니다.\n구조를 만들고 첫 버전을 남겨 보세요."));
      return;
    }
    state.projects.forEach((project) => {
      const load = button("", "project-version", async () => {
        if (state.busy || !await confirmReplace()) return;
        run(async () => {
          const result = await api(`/api/projects/${encodeURIComponent(project.id)}`);
          applySchema(result.schema, { savedId: result.id, topic: project.topic || null });
          notify(`‘${result.schema.name}’ 버전을 불러왔습니다.`);
        });
      }, `${project.name} 저장 버전 불러오기`);
      load.classList.toggle("active", project.id === state.savedId);
      if (project.id === state.savedId) load.setAttribute("aria-current", "true");
      const date = new Date(project.updatedAt);
      const formatted = Number.isNaN(date.getTime()) ? "저장한 버전" : new Intl.DateTimeFormat("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
      load.append(element("strong", null, project.name), element("small", null, `${formatted} · ${project.database} · 테이블 ${project.tableCount}개`));
      load.title = `버전 ${project.id}`;
      list.append(load);
    });
  }

  function uniqueName(prefix, names) {
    let count = 1;
    let candidate = prefix;
    while (names.includes(candidate)) candidate = `${prefix}_${++count}`;
    return candidate;
  }

  function addEntity() {
    if (state.schema.entities.length >= 100) { notify("테이블은 최대 100개까지 만들 수 있습니다.", true); return; }
    const name = uniqueName("new_table", state.schema.entities.map((entity) => entity.name));
    state.schema.entities.push({ name, description: "", attributes: [{ name: "id", type: "uuid", primary_key: true, nullable: false, unique: false }], indexes: [] });
    changed();
    renderEntities();
    renderRelations();
    switchView("entities");
    const fields = document.querySelectorAll(".entity-name");
    const last = fields[fields.length - 1];
    if (last) { last.focus(); last.select(); }
  }

  function renameEntity(entity, name) {
    const previous = entity.name;
    entity.name = name;
    state.schema.relations.forEach((relation) => {
      if (relation.from.entity === previous) relation.from.entity = name;
      if (relation.to.entity === previous) relation.to.entity = name;
    });
    changed();
    renderRelations();
  }

  async function removeEntity(entity) {
    const attached = state.schema.relations.filter((relation) => relation.from.entity === entity.name || relation.to.entity === entity.name);
    if (!await confirmDialog(`‘${entity.name}’ 테이블의 필드 ${entity.attributes.length}개${attached.length ? `와 연결된 관계 ${attached.length}개` : ""}를 삭제하시겠습니까?`)) return;
    state.schema.entities = state.schema.entities.filter((item) => item !== entity);
    state.schema.relations = state.schema.relations.filter((relation) => relation.from.entity !== entity.name && relation.to.entity !== entity.name);
    changed();
    renderEntities();
    renderRelations();
  }

  function renameAttribute(entity, attribute, name) {
    const previous = attribute.name;
    attribute.name = name;
    (entity.indexes || []).forEach((index) => { index.columns = index.columns.map((column) => column === previous ? name : column); });
    state.schema.relations.forEach((relation) => {
      [relation.from, relation.to].forEach((endpoint) => {
        if (endpoint.entity === entity.name) endpoint.columns = endpoint.columns.map((column) => column === previous ? name : column);
      });
    });
    changed();
    renderRelations();
  }

  async function removeAttribute(entity, attribute) {
    const attached = state.schema.relations.filter((relation) => [relation.from, relation.to].some((endpoint) => endpoint.entity === entity.name && endpoint.columns.includes(attribute.name)));
    const indexes = (entity.indexes || []).filter((index) => index.columns.includes(attribute.name));
    const losses = [`‘${entity.name}.${attribute.name}’ 필드`, ...(attached.length ? [`관계 ${attached.length}개`] : []), ...(indexes.length ? [`인덱스 ${indexes.length}개`] : [])];
    if (!await confirmDialog(`${losses.join(", ")}를 삭제하시겠습니까?`)) return;
    entity.attributes = entity.attributes.filter((item) => item !== attribute);
    entity.indexes = (entity.indexes || []).filter((index) => !index.columns.includes(attribute.name));
    state.schema.relations = state.schema.relations.filter((relation) => !attached.includes(relation));
    changed();
    renderEntities();
    renderRelations();
  }

  function renderEntities() {
    const list = $("entity-list");
    list.replaceChildren();
    state.schema.entities.forEach((entity) => {
      const card = element("article", "entity-card");
      const head = element("div", "entity-card-head");
      const identity = element("div", "entity-identity");
      const icon = element("span", "entity-icon");
      icon.setAttribute("aria-hidden", "true");
      for (let i = 0; i < 4; i += 1) icon.append(element("i"));
      const nameWrap = element("div", "entity-name-wrap");
      const name = input(entity.name, "테이블 이름", (value) => renameEntity(entity, value), { maxlength: 63, pattern: "[a-z][a-z0-9_]{0,62}", spellcheck: false });
      name.className = "entity-name";
      const description = input(entity.description || "", `${entity.name} 테이블 설명`, (value) => { entity.description = value; changed(); }, { maxlength: 1000, placeholder: "테이블 설명 추가" });
      description.className = "entity-description";
      nameWrap.append(name, description);
      identity.append(icon, nameWrap);
      head.append(identity, button("×", "icon-button entity-remove", () => removeEntity(entity), `${entity.name} 테이블 삭제`));
      const scroll = element("div", "attribute-scroll");
      const table = element("table", "attribute-table");
      const caption = element("caption", "sr-only", `${entity.name} 필드 편집`);
      const thead = element("thead");
      const headers = element("tr");
      ["필드 이름", "타입", "길이 / 자릿수", "PK", "NULL", "고유", "삭제"].forEach((text) => {
        const th = element("th", null, text); th.scope = "col"; headers.append(th);
      });
      thead.append(headers);
      const tbody = element("tbody");
      entity.attributes.forEach((attribute) => tbody.append(attributeRow(entity, attribute)));
      table.append(caption, thead, tbody);
      scroll.append(table);
      const foot = element("div", "entity-card-foot");
      foot.append(button("+ 필드 추가", "text-button", () => {
        const count = state.schema.entities.reduce((sum, item) => sum + item.attributes.length, 0);
        if (count >= 2000) { notify("필드는 전체 2,000개까지 만들 수 있습니다.", true); return; }
        entity.attributes.push({ name: uniqueName("new_field", entity.attributes.map((item) => item.name)), type: "text", nullable: true, primary_key: false, unique: false });
        changed(); renderEntities();
      }, `${entity.name}에 필드 추가`), element("small", null, `필드 ${entity.attributes.length}개 · PK 기본 키 · NULL 비어 있음 허용`));
      card.append(head, scroll, foot);
      card.append(advancedFields(entity));
      list.append(card);
    });
    updateCounts();
  }

  function attributeRow(entity, attribute) {
    const row = element("tr");
    const fieldName = input(attribute.name, `${entity.name} 필드 이름`, (value) => renameAttribute(entity, attribute, value), { maxlength: 63, pattern: "[a-z][a-z0-9_]{0,62}", spellcheck: false });
    const type = select(types, attribute.type, `${entity.name}.${attribute.name} 타입`, (value) => {
      attribute.type = value;
      if (value !== "varchar") delete attribute.length;
      if (value !== "decimal") { delete attribute.precision; delete attribute.scale; }
      changed(); renderEntities();
    });
    const detail = element("div", "type-detail");
    if (attribute.type === "varchar") {
      detail.append(input(attribute.length, `${entity.name}.${attribute.name} 최대 길이`, (value) => {
        if (value === "") delete attribute.length; else attribute.length = Number(value);
        changed();
      }, { type: "number", min: 1, max: 65535, placeholder: "기본" }));
    } else if (attribute.type === "decimal") {
      detail.append(input(attribute.precision, `${entity.name}.${attribute.name} 전체 자릿수`, (value) => {
        if (value === "") delete attribute.precision; else attribute.precision = Number(value);
        changed();
      }, { type: "number", min: 1, max: 38, placeholder: "P" }), element("span", null, ","), input(attribute.scale, `${entity.name}.${attribute.name} 소수 자릿수`, (value) => {
        if (value === "") delete attribute.scale; else attribute.scale = Number(value);
        changed();
      }, { type: "number", min: 0, max: 38, placeholder: "S" }));
    } else detail.append(element("span", "type-dash", "—"));
    const pk = checkbox(attribute.primary_key, `${entity.name}.${attribute.name} 기본 키`, (checked) => {
      if (checked) {
        entity.attributes.forEach((item) => { item.primary_key = false; });
        attribute.primary_key = true; attribute.nullable = false;
      } else attribute.primary_key = false;
      changed(); renderEntities();
    });
    const nullable = checkbox(attribute.nullable, `${entity.name}.${attribute.name} NULL 허용`, (checked) => { attribute.nullable = checked; changed(); });
    nullable.disabled = Boolean(attribute.primary_key);
    if (nullable.disabled) nullable.title = "기본 키는 NULL을 허용할 수 없습니다.";
    const unique = checkbox(attribute.unique, `${entity.name}.${attribute.name} 고유 값`, (checked) => { attribute.unique = checked; changed(); });
    [fieldName, type, detail, pk, nullable, unique, button("×", "icon-button", () => removeAttribute(entity, attribute), `${entity.name}.${attribute.name} 필드 삭제`)].forEach((node, index) => {
      const cell = element("td", index > 2 && index < 6 ? "check-cell" : index === 6 ? "remove-cell" : "");
      cell.append(node); row.append(cell);
    });
    return row;
  }

  function advancedFields(entity) {
    const details = element("details", "advanced-fields");
    const count = entity.attributes.filter((attribute) => Object.hasOwn(attribute, "default")).length;
    details.append(element("summary", null, `기본값 ${count}개 · 인덱스 ${(entity.indexes || []).length}개`));
    const content = element("div", "advanced-content");
    content.append(element("p", "subtle", "기본값은 값으로만 저장됩니다. 임의의 SQL 표현식은 사용하지 않습니다."));
    entity.attributes.forEach((attribute) => {
      const line = element("div", "default-row");
      line.append(element("span", null, attribute.name));
      const modes = ["없음", "값", ...(attribute.type === "timestamp" ? ["현재 시각"] : [])];
      const current = !Object.hasOwn(attribute, "default") ? "없음" : attribute.default && typeof attribute.default === "object" ? "현재 시각" : "값";
      const defaultInput = input(current === "값" ? JSON.stringify(attribute.default) : "", `${entity.name}.${attribute.name} 기본값 (JSON 값)`, (value) => {
        try {
          const parsed = JSON.parse(value);
          if (parsed !== null && !["string", "number", "boolean"].includes(typeof parsed)) throw new Error();
          if (typeof parsed === "number" && !Number.isFinite(parsed)) throw new Error();
          attribute.default = parsed;
          changed();
        } catch { notify('기본값을 JSON 값으로 입력하세요. 예: "문자", 10, true, null', true); defaultInput.value = JSON.stringify(attribute.default); }
      }, { placeholder: '예: "문자", 10, true', maxlength: 10000, spellcheck: false, commitOn: "change" });
      defaultInput.disabled = current !== "값";
      const mode = select(modes, current, `${entity.name}.${attribute.name} 기본값 방식`, (value) => {
        if (value === "없음") delete attribute.default;
        else if (value === "현재 시각") attribute.default = { function: "current_timestamp" };
        else attribute.default = attribute.type === "boolean" ? false : ["integer", "bigint", "decimal"].includes(attribute.type) ? 0 : "";
        defaultInput.disabled = value !== "값";
        defaultInput.value = value === "값" ? JSON.stringify(attribute.default) : "";
        changed();
        if (value === "값") defaultInput.focus();
      });
      line.append(mode, defaultInput);
      content.append(line);
    });
    (entity.indexes || []).forEach((index) => {
      const line = element("div", "index-row");
      line.append(element("code", null, `${index.name} (${index.columns.join(", ")})${index.unique ? " · UNIQUE" : ""}`), button("삭제", "text-button", async () => {
        if (!await confirmDialog(`‘${index.name}’ 인덱스를 삭제하시겠습니까? 이 인덱스를 참조하는 관계는 검증에서 확인하세요.`)) return;
        entity.indexes = entity.indexes.filter((item) => item !== index);
        changed(); renderEntities();
      }, `${index.name} 인덱스 삭제`));
      content.append(line);
    });
    details.append(content);
    return details;
  }

  function switchView(view) {
    state.view = view;
    ["entities", "relations"].forEach((name) => {
      const active = name === view;
      $(`${name}-tab`).classList.toggle("active", active);
      $(`${name}-tab`).setAttribute("aria-pressed", String(active));
      $(`${name}-panel`).hidden = !active;
    });
  }

  function renderRelations() {
    const list = $("relation-list");
    list.replaceChildren();
    if (!state.schema.relations.length) {
      list.append(element("p", "empty-relations", "아직 연결한 관계가 없습니다.\n테이블을 만든 뒤 외래 키 필드를 연결하세요."));
      return;
    }
    state.schema.relations.forEach((relation) => {
      const card = element("article", "relation-card");
      const head = element("div", "relation-card-head");
      head.append(element("h3", null, relation.name), button("×", "icon-button", async () => {
        if (!await confirmDialog(`‘${relation.name}’ 관계를 삭제하시겠습니까? 테이블과 필드는 유지됩니다.`)) return;
        state.schema.relations = state.schema.relations.filter((item) => item !== relation);
        changed(); renderRelations();
      }, `${relation.name} 관계 삭제`));
      const path = element("div", "relation-path");
      path.append(element("span", null, `${relation.from.entity}.${relation.from.columns.join(", ")}`), element("b", null, "→"), element("span", null, `${relation.to.entity}.${relation.to.columns.join(", ")}`));
      card.append(head, path, element("small", null, `대상 삭제 시 · ${(relation.on_delete || "restrict").toUpperCase().replaceAll("_", " ")}`));
      list.append(card);
    });
    updateCounts();
  }

  function relationFields(side) {
    const entityName = $(`relation-${side}-entity`).value;
    const entity = state.schema.entities.find((item) => item.name === entityName);
    const fields = $(`relation-${side}-field`);
    fields.replaceChildren();
    if (!entity) return;
    const eligible = side === "to" ? entity.attributes.filter((attribute) => attribute.primary_key || attribute.unique || (entity.indexes || []).some((index) => index.unique && index.columns.length === 1 && index.columns[0] === attribute.name)) : entity.attributes;
    eligible.forEach((attribute) => fields.append(option(attribute.name, `${attribute.name} · ${attribute.type}`)));
    if (!eligible.length) fields.append(option("", "사용할 수 있는 키가 없습니다"));
  }

  function showRelationDialog() {
    if (!state.schema.entities.length) { notify("먼저 테이블을 만들어 주세요."); return; }
    ["from", "to"].forEach((side) => {
      const choices = $(`relation-${side}-entity`);
      choices.replaceChildren(...state.schema.entities.map((entity) => option(entity.name)));
      if (side === "to" && state.schema.entities.length > 1) choices.selectedIndex = 1;
      relationFields(side);
    });
    $("relation-name").value = uniqueName("fk_relation", state.schema.relations.map((relation) => relation.name));
    $("relation-delete").value = "restrict";
    $("relation-dialog").showModal();
  }

  function displayValidation(result) {
    const valid = result.valid;
    $("validation-dot").className = `status-dot ${valid ? "valid" : "invalid"}`;
    $("validation-title").textContent = valid ? "검증 완료 · 구조에 문제가 없습니다." : `${result.issues.length}개 항목을 확인해 주세요.`;
    const list = $("validation-issues");
    list.replaceChildren();
    list.hidden = valid;
    result.issues.forEach((issue) => {
      const item = element("li");
      item.append(element("code", null, issue.path), document.createTextNode(` ${issue.message}`));
      list.append(item);
    });
  }

  async function validateCurrent(announce = true) {
    const revision = state.revision;
    const result = await api("/api/validate", { schema: snapshot() });
    if (revision !== state.revision) { notify("검증 중 구조가 바뀌었습니다. 다시 검증해 주세요."); return false; }
    displayValidation(result);
    if (announce) notify(result.valid ? "구조 검증을 통과했습니다." : `수정할 항목 ${result.issues.length}개가 있습니다. 편집기 아래의 검증 결과를 확인하세요.`, !result.valid);
    return result.valid;
  }

  function switchFormat(format) {
    state.format = format;
    state.files = [];
    state.fileIndex = 0;
    document.querySelectorAll(".output-tab").forEach((tab) => {
      const active = tab.dataset.format === format;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    $("output-panel").setAttribute("aria-labelledby", `${format}-tab`);
    renderOutput();
  }

  function renderOutput() {
    const format = state.format;
    const names = { sql: "SQL", java: "Java", json: "JSON" };
    const description = {
      sql: "선택한 데이터베이스의 테이블 생성문입니다.",
      java: "엔티티와 Spring Data 저장소를 파일별로 확인하세요.",
      json: "현재 편집 내용입니다. 다시 열어 설계를 이어갈 수 있습니다.",
    };
    $("java-options").hidden = format !== "java";
    $("generate-output").textContent = `${names[format]} ${format === "json" ? "구조 검증" : "생성"}`;
    $("output-description").textContent = description[format];
    $("export-note").textContent = format === "java" ? "생성한 Java 코드는 프로젝트의 JDK·Spring·ORM 버전과 규칙을 확인한 뒤 적용하세요. 외래 키는 단순 필드로 유지됩니다." : format === "json" ? "ChannelShift 전용 설계 파일입니다. 레코드 데이터나 접속 정보를 포함하지 않습니다. 파일을 열 때 구조를 검증합니다." : "생성한 SQL은 직접 확인한 뒤 사용하세요. 이 편집기는 SQL을 실행하지 않습니다.";
    if (format === "json") state.files = [{ path: "schema.channelshift.json", content: JSON.stringify(state.schema, null, 2) + "\n" }];
    $("file-selector-wrapper").hidden = state.files.length < 2;
    const choices = $("output-file");
    choices.replaceChildren();
    state.files.forEach((file, index) => choices.append(option(String(index), file.path)));
    choices.value = String(state.fileIndex);
    const current = state.files[state.fileIndex];
    $("code-filename").textContent = current ? current.path : format === "java" ? "Entity.java" : "schema.sql";
    $("code-status").textContent = current ? format === "json" ? "현재 구조" : `${state.fileIndex + 1} / ${state.files.length}` : "미리보기";
    $("code-content").textContent = current ? current.content : `// ${names[format]}를 생성하면 여기에 표시됩니다.\n// 구조를 수정한 뒤에는 다시 생성하세요.`;
    $("download-file").disabled = !current;
    $("copy-output").disabled = !current;
    $("download-bundle").hidden = format !== "java" || state.files.length === 0;
  }

  function download(filename, content, mime = "text/plain;charset=utf-8") {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const anchor = element("a");
    anchor.href = url;
    anchor.download = filename.split(/[\\/]/).pop() || "channelshift.txt";
    anchor.hidden = true;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  $("project-name").addEventListener("input", () => { state.schema.name = $("project-name").value; changed(); });
  $("database").addEventListener("change", () => { state.schema.database = $("database").value; changed(); });
  $("add-entity").addEventListener("click", addEntity);
  $("empty-add").addEventListener("click", addEntity);
  $("entities-tab").addEventListener("click", () => switchView("entities"));
  $("relations-tab").addEventListener("click", () => switchView("relations"));
  $("new-project").addEventListener("click", async () => {
    if (!await confirmReplace()) return;
    const schema = emptySchema(); schema.database = $("database").value;
    applySchema(schema);
    $("project-name").focus(); $("project-name").select();
    notify("빈 프로젝트를 만들었습니다.");
  });
  $("generate-template").addEventListener("click", async () => {
    if (!await confirmReplace({ skipEmpty: true })) return;
    run(async () => {
      const selected = state.selectedTemplate;
      const result = await api("/api/generate", { templateId: selected, project: $("project-name").value, database: $("database").value });
      applySchema(result.schema, { topic: selected, dirty: true });
      notify(`테이블 ${result.schema.entities.length}개를 만들었습니다. 서비스에 맞게 다듬어 보세요.`);
    });
  });
  $("refresh-projects").addEventListener("click", () => run(async () => { await loadProjects(); notify("저장한 버전을 새로 불러왔습니다."); }));
  $("validate").addEventListener("click", () => run(() => validateCurrent()));
  $("save-project").addEventListener("click", () => run(async () => {
    if (!await validateCurrent(false)) { notify("저장하기 전에 검증 결과를 확인해 주세요.", true); return; }
    const revision = state.revision;
    const body = { schema: snapshot() };
    if (state.topic) body.topic = state.topic;
    const result = await api("/api/save", body);
    if (state.revision === revision) { state.dirty = false; state.savedId = result.id; }
    await loadProjects();
    notify(result.stored ? "새 버전을 이 컴퓨터에 저장했습니다." : "동일한 구조가 이미 저장되어 있습니다.");
  }));
  $("add-relation").addEventListener("click", showRelationDialog);
  ["close-relation", "cancel-relation"].forEach((id) => $(id).addEventListener("click", () => $("relation-dialog").close()));
  ["from", "to"].forEach((side) => $(`relation-${side}-entity`).addEventListener("change", () => relationFields(side)));
  $("relation-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("relation-name").value;
    if (state.schema.relations.some((relation) => relation.name === name)) { notify("같은 이름의 관계가 있습니다. 다른 이름을 입력하세요.", true); return; }
    state.schema.relations.push({ name, from: { entity: $("relation-from-entity").value, columns: [$("relation-from-field").value] }, to: { entity: $("relation-to-entity").value, columns: [$("relation-to-field").value] }, on_delete: $("relation-delete").value });
    changed(); renderRelations();
    $("relation-dialog").close();
    notify("관계를 추가했습니다. 구조 검증으로 키와 타입을 확인하세요.");
  });
  document.querySelectorAll(".output-tab").forEach((tab) => {
    tab.addEventListener("click", () => switchFormat(tab.dataset.format));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const formats = ["sql", "java", "json"];
      let index = formats.indexOf(state.format);
      index = event.key === "Home" ? 0 : event.key === "End" ? 2 : (index + (event.key === "ArrowRight" ? 1 : -1) + 3) % 3;
      switchFormat(formats[index]); $(`${formats[index]}-tab`).focus();
    });
  });
  $("java-package").addEventListener("input", () => { if (state.format === "java") { state.files = []; state.fileIndex = 0; renderOutput(); } });
  $("generate-output").addEventListener("click", () => run(async () => {
    const format = state.format;
    if (format === "json") { await validateCurrent(); return; }
    if (!await validateCurrent(false)) { notify("코드를 생성하기 전에 검증 결과를 확인해 주세요.", true); return; }
    const revision = state.revision;
    const javaPackage = $("java-package").value;
    const body = { schema: snapshot(), format };
    if (format === "java") body.package = javaPackage;
    const result = await api("/api/export", body);
    if (state.revision !== revision || state.format !== format || (format === "java" && javaPackage !== $("java-package").value)) { notify("생성 중 설정이 바뀌었습니다. 다시 생성해 주세요."); return; }
    state.files = result.files;
    state.fileIndex = 0;
    renderOutput();
    notify(`${format === "sql" ? "SQL" : "Java"} 파일 ${state.files.length}개를 생성했습니다.`);
  }));
  $("output-file").addEventListener("change", () => { state.fileIndex = Number($("output-file").value); renderOutput(); });
  $("download-file").addEventListener("click", () => {
    const file = state.files[state.fileIndex];
    if (file) download(file.path, file.content, state.format === "json" ? "application/json;charset=utf-8" : "text/plain;charset=utf-8");
  });
  $("download-bundle").addEventListener("click", () => {
    download("channelshift-java-files.json", JSON.stringify({ format: "channelshift.java-files/v1", files: state.files }, null, 2) + "\n", "application/json;charset=utf-8");
  });
  $("copy-output").addEventListener("click", async () => {
    const file = state.files[state.fileIndex]; if (!file) return;
    try { await navigator.clipboard.writeText(file.content); notify("클립보드에 복사했습니다."); }
    catch { notify("클립보드에 접근할 수 없습니다. 미리보기의 텍스트를 선택해 복사하거나 파일을 내려받으세요.", true); }
  });
  $("import-trigger").addEventListener("click", () => $("import-file").click());
  $("import-file").addEventListener("change", async () => {
    const file = $("import-file").files[0];
    $("import-file").value = "";
    if (!file || !await confirmReplace()) return;
    if (file.size > 2 * 1024 * 1024) { notify("2 MiB 이하의 JSON 설계 파일을 선택하세요.", true); return; }
    run(async () => {
      let schema;
      try { schema = JSON.parse(await file.text()); }
      catch { throw new Error("JSON 파일을 읽을 수 없습니다. 올바른 ChannelShift 설계 파일인지 확인하세요."); }
      const result = await api("/api/validate", { schema });
      if (!result.valid) {
        const first = result.issues[0];
        notify(`파일을 열지 못했습니다. ${first ? `${first.path}: ${first.message}` : "올바른 설계 형식인지 확인하세요."}`, true);
        return;
      }
      applySchema(schema, { dirty: true });
      displayValidation(result);
      notify(`‘${schema.name}’ 설계 파일을 열었습니다.`);
    });
  });
  $("confirm-cancel").addEventListener("click", () => $("confirm-dialog").close("cancel"));
  $("confirm-continue").addEventListener("click", () => $("confirm-dialog").close("continue"));
  $("confirm-dialog").addEventListener("close", () => {
    const resolve = state.confirmResolver;
    state.confirmResolver = null;
    if (resolve) resolve($("confirm-dialog").returnValue === "continue");
  });
  document.querySelector(".brand").addEventListener("click", async (event) => {
    event.preventDefault();
    if (await confirmReplace()) window.location.reload();
  });

  renderEntities();
  renderRelations();
  renderOutput();
  Promise.allSettled([loadTemplates(), loadProjects()]).then((results) => {
    results.forEach((result, index) => {
      if (result.status === "rejected") {
        const target = $(index === 0 ? "template-list" : "project-list");
        target.replaceChildren(element("p", "subtle", index === 0 ? "템플릿을 불러오지 못했습니다. 서버 연결을 확인하고 새로고침하세요." : "저장한 버전을 불러오지 못했습니다. 새로고침을 눌러 다시 시도하세요."));
        notify(result.reason.message, true);
      }
    });
  });
})();
