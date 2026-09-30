// Run: node --test tests/test_studio_guidance.mjs
// A small DOM adapter exercises the real editor's public methods and user event
// handlers. It makes no HTTP calls and does not replace rendered browser QA.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.textContent = "";
  }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  fire(name) { this.listeners[name]?.({ preventDefault() {} }); }
}

globalThis.__channelshiftGuidanceTestElement = Element;
const domAdapter = `
export function node(tag, className, value) {
  const element = new globalThis.__channelshiftGuidanceTestElement(tag);
  element.className = className;
  if (value !== undefined) element.textContent = String(value);
  return element;
}
export const list = value => Array.isArray(value) ? value : [];
export const text = value => typeof value === "string" ? value : "";
export function button(label, callback) {
  const element = node("button", null, label);
  element.addEventListener("click", callback);
  return element;
}`;
const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const source = await readFile(new URL("../src/channelshift/web/studio-guidance.js", import.meta.url), "utf8");
assert(source.includes('from "/studio-dom.js"'), "Keep the test adapter bound to the actual DOM helper import");
const { createFeatureGuidance } = await import(dataModule(source.replace('"/studio-dom.js"', JSON.stringify(dataModule(domAdapter)))));

function view() {
  return {
    project: { id: "project-a" },
    pipeline: {
      revision: "revision-1",
      guidance: {
        enabled: true, catalog_version: "catalog-1", unresolved: [],
        cards: Array.from({ length: 8 }, (_, index) => ({
          id: `card-${index}`, title: `Feature ${index}`, description: "A customer capability",
          why: "It changes the customer workflow", effort: "A moderate amount of work",
          selected: null, note: "",
          options: [
            { id: "basic", label: "Basic", description: "The basic implementation" },
            { id: "none", label: "Not needed", description: "Do not add this feature" },
            { id: "unsure", label: "Not sure", description: "Request a recommendation" },
            { id: "later", label: "Decide later", description: "Keep the decision pending" },
          ],
        })),
      },
    },
  };
}
const descendants = element => [element, ...element.children.flatMap(descendants)];
function render(editor, project, options) {
  const root = new Element("section");
  editor.render(root, project, options);
  return root;
}
const card = (root, index = 0) => descendants(root).filter(element => element.tagName === "fieldset")[index];
function choose(root, option, index = 0) {
  const control = descendants(card(root, index)).find(element => element.dataset.option === option);
  assert(control, `Option ${option} must be offered`);
  control.fire("click");
}
function writeNote(root, value, index = 0) {
  const input = descendants(card(root, index)).find(element => element.tagName === "textarea");
  assert(input, "Every feature must allow a written explanation");
  input.value = value;
  input.fire("input");
}
const recommendations = root => descendants(root).filter(element => element.dataset.featureRecommendation === "true");

function recommendedView() {
  const project = view();
  project.pipeline.analysis_current = true;
  project.project.feature_revision = "features-1";
  project.pipeline.guidance.cards.forEach((item, index) => { item.selected = index === 0 ? "unsure" : "basic"; });
  project.project.candidate_input = {
    feature_revision: project.project.feature_revision,
    feature_decisions: {
      catalog_version: project.pipeline.guidance.catalog_version,
      decisions: project.pipeline.guidance.cards.map(item => ({ id: item.id, option: item.selected, note: item.note })),
    },
  };
  project.project.candidate = { feature_recommendations: [{
    card_id: "card-0", option_id: "basic", reason: "This fits the supplied workflow",
    tradeoff: "The operator still needs to review requests",
  }] };
  return project;
}

test("choices have no defaults, every note stays available, and unsure invokes only its explicit callback", () => {
  let unsure = 0, saves = 0, analyses = 0;
  const editor = createFeatureGuidance({ onUnsure: () => unsure++, onSave: () => saves++, onAnalyze: () => analyses++ });
  const project = view(), root = render(editor, project);
  assert.equal(editor.unresolved(project).length, 8);
  assert.equal(descendants(root).filter(element => element.tagName === "textarea").length, 8);
  assert.equal(descendants(root).filter(element => element.attributes["aria-pressed"] === "true").length, 0);
  writeNote(root, "I want to approve bookings myself");
  choose(root, "unsure");
  assert.equal(unsure, 1);
  assert.equal(saves, 0);
  assert.equal(analyses, 0);
  assert.equal(editor.unresolved(project).length, 8);
  assert.equal(editor.snapshot(project).decisions[0].note, "I want to approve bookings myself");
  assert.equal(descendants(render(editor, project)).filter(element => element.tagName === "textarea").length, 8);
});

test("drafts survive project switches and unchanged polling without being saved automatically", () => {
  const editor = createFeatureGuidance({}), project = view();
  const root = render(editor, project);
  choose(root, "basic");
  writeNote(root, "Keep this draft");
  const other = structuredClone(project);
  other.project.id = "project-b";
  render(editor, other);
  assert.equal(editor.snapshot(other).decisions[0].note, "");
  const polled = structuredClone(project);
  polled.pipeline.revision = "revision-2";
  render(editor, polled);
  assert.equal(editor.snapshot(polled).decisions[0].note, "Keep this draft");
  assert.equal(editor.snapshot(polled).revision, "revision-2");
  assert.equal(editor.hasConflict(polled), false);
  assert(editor.hasUnsaved());
});

test("server choice changes require an explicit conflict rebase and preserve the local choice", () => {
  const editor = createFeatureGuidance({}), project = view();
  choose(render(editor, project), "basic");
  const changed = structuredClone(project);
  changed.pipeline.revision = "revision-2";
  changed.pipeline.guidance.cards[0].selected = "none";
  assert(editor.hasConflict(changed));
  assert.equal(editor.snapshot(changed).revision, "revision-1");
  assert.equal(editor.snapshot(changed).decisions[0].option, "basic");
  const root = render(editor, changed);
  assert.equal(descendants(root).find(element => element.id === "save-feature-decisions").disabled, true);
  descendants(root).find(element => element.textContent === "저장된 선택 확인 · 내 입력 유지").fire("click");
  assert.equal(editor.hasConflict(changed), false);
  assert.equal(editor.snapshot(changed).revision, "revision-2");
  assert.equal(editor.snapshot(changed).decisions[0].option, "basic");
  assert(editor.hasUnsaved());
});

test("failed saves retain drafts and an earlier save acknowledgment cannot delete a newer edit", () => {
  const editor = createFeatureGuidance({}), project = view(), root = render(editor, project);
  writeNote(root, "First draft");
  const request = editor.snapshot(project);
  // A failed request has no acknowledgment; re-rendering must retain its input.
  render(editor, project);
  assert.equal(editor.snapshot(project).decisions[0].note, "First draft");
  writeNote(root, "Newer edit while the save was pending");
  editor.acceptSaved(request);
  assert(editor.hasUnsaved());
  assert.equal(editor.snapshot(project).decisions[0].note, "Newer edit while the save was pending");
  editor.acceptSaved(editor.snapshot(project));
  assert.equal(editor.hasUnsaved(), false);
});

test("accepting a current recommendation changes only the local choice and requires a later save", () => {
  let saves = 0, analyses = 0;
  const editor = createFeatureGuidance({ onSave: () => saves++, onAnalyze: () => analyses++ });
  const project = recommendedView(), root = render(editor, project);
  assert.equal(recommendations(root).length, 1);
  assert.equal(editor.snapshot(project).decisions[0].option, "unsure");
  descendants(root).find(element => element.textContent === "이 추천으로 선택").fire("click");
  assert.equal(editor.snapshot(project).decisions[0].option, "basic");
  assert.equal(project.pipeline.guidance.cards[0].selected, "unsure");
  assert(editor.hasUnsaved());
  assert.equal(saves, 0);
  assert.equal(analyses, 0);
  assert.equal(recommendations(render(editor, project))[0].hidden, true);
});

test("stale or mismatched analysis cannot offer a recommendation", () => {
  for (const change of [
    project => { project.pipeline.analysis_current = false; },
    project => { project.project.candidate_input.feature_revision = "old-features"; },
    project => { project.project.candidate_input.feature_decisions.decisions[0].note = "Different input"; },
    project => { project.project.candidate_input.feature_decisions.catalog_version = "old-catalog"; },
  ]) {
    const project = recommendedView();
    change(project);
    assert.equal(recommendations(render(createFeatureGuidance({}), project)).length, 0);
  }
});

test("forged recommendations cannot override an excluded, deferred, selected, or unanswered card", () => {
  for (const selected of ["none", "later", "basic", null]) {
    const project = recommendedView();
    project.pipeline.guidance.cards[0].selected = selected;
    project.project.candidate_input.feature_decisions.decisions[0].option = selected;
    assert.equal(recommendations(render(createFeatureGuidance({}), project)).length, 0, `Must hide recommendation for ${selected}`);
  }
});

test("confirmed cards are read-only and old catalog drafts remain separate from a new catalog", () => {
  const editor = createFeatureGuidance({}), project = view();
  writeNote(render(editor, project), "Old catalog note");
  const readonly = render(editor, project, { readonly: true });
  assert(descendants(readonly).filter(element => element.tagName === "textarea").every(element => element.disabled));
  assert(!descendants(readonly).some(element => element.id === "save-feature-decisions"));
  const upgraded = structuredClone(project);
  upgraded.pipeline.guidance.catalog_version = "catalog-2";
  render(editor, upgraded);
  assert.equal(editor.snapshot(upgraded).decisions[0].note, "");
  assert.equal(editor.snapshot(project).decisions[0].note, "Old catalog note");
  assert(editor.hasUnsaved());
});
