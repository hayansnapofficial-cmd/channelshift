"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  let sections = [], project = null, pending = false, selection = 0;
  const fields = {site_name: 'seo-site-name', title: 'seo-title', description: 'seo-description', url: 'seo-url', image_url: 'seo-image-url'};
  function message(text) { $('message').textContent = text; $('message').hidden = !text; }
  async function api(path, body) {
    const options = {credentials: 'same-origin', cache: 'no-store', headers: {'X-ChannelShift-Token': document.querySelector('meta[name="channelshift-token"]').content}};
    if (body !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
    const response = await fetch(path, options);
    if (response.status === 401) { window.location.assign('/login'); throw new Error('다시 로그인해 주세요.'); }
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error === 'delivery_revision_conflict' ? '다른 창에서 수정했습니다. 입력은 유지됩니다. 프로젝트를 다시 선택해 최신 초안을 확인하세요.' : '저장하지 못했습니다. 입력 형식과 연결 상태를 확인하세요.');
    return result;
  }
  function values() { return {...Object.fromEntries(Object.entries(fields).map(([key,id]) => [key,$(id).value])), noindex: $('seo-noindex').checked}; }
  function preview() {
    const v = values(); $('preview-title').textContent = v.title || '검색·공유 제목';
    $('preview-description').textContent = v.description || '설명을 입력하세요.';
    $('preview-site').textContent = v.site_name || v.url || '사이트 주소';
    $('preview-image').textContent = v.image_url ? '공유 이미지 주소 설정됨 · 접근 미검증' : '공유 이미지 미설정';
  }
  function renderProject() {
    const v = project?.workspace_settings?.seo || {};
    Object.entries(fields).forEach(([key,id]) => { $(id).value = v[key] || ''; }); $('seo-noindex').checked = v.noindex === true;
    $('project-stage').textContent = project ? `${project.name} · ${project.workflow_stage === 'requirements_review' ? '요구사항 검수 중' : '요구사항 접수 중'}` : '프로젝트를 선택하세요.';
    $('save-seo').disabled = !project || pending;
    preview();
  }
  function renderSection() {
    const key = location.hash.slice(1) || 'backend';
    const section = sections.find((item) => item.id === key) || sections[0];
    if (!section) return;
    document.title = `ChannelShift · ${section.title}`;
    $('section-title').textContent = section.title; $('section-description').textContent = section.description;
    $('remaining').textContent = section.remaining; $('seo-editor').hidden = section.id !== 'seo';
    document.querySelectorAll('nav a[href^="#"]').forEach((link) => { if (link.hash === `#${section.id}`) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
    $('stage-list').replaceChildren();
    for (const item of [...section.stages, ...section.checks]) {
      const row = document.createElement('article'); row.className = 'stage-item';
      const title = document.createElement('h3'); title.textContent = item.title; row.append(title);
      if (item.action) { const text = document.createElement('p'); text.textContent = item.action; row.append(text); }
      const status = document.createElement('small'); status.textContent = '기준만 제공 · 실행 결과 없음'; row.append(status); $('stage-list').append(row);
    }
  }
  $('project-select').addEventListener('change', async () => {
    const id = $('project-select').value, revision = ++selection; pending = false; project = null; renderProject(); message('');
    if (!id) return;
    try { const result = await api(`/api/delivery/projects/${encodeURIComponent(id)}`); if (revision === selection) { project = result.project; renderProject(); } }
    catch (error) { if (revision === selection) message(error.message); }
  });
  $('seo-form').addEventListener('input', preview);
  $('seo-form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (!project || pending) return;
    const revision = selection; pending = true; $('save-seo').disabled = true;
    try {
      const result = await api('/api/workbench/settings', {project_id: project.id, section: 'seo', values: values(), expected_revision: project.settings_revisions.seo});
      if (revision === selection) { project = result.project; message('초안을 저장했습니다.'); }
    } catch (error) { if (revision === selection) message(error.message); }
    finally { if (revision === selection) { pending = false; $('save-seo').disabled = !project; } }
  });
  window.addEventListener('hashchange', renderSection);
  Promise.all([api('/api/workbench/profile'), api('/api/delivery/projects')]).then(([profile,list]) => {
    sections = profile.sections; renderSection();
    for (const item of list.items) { const option = document.createElement('option'); option.value = item.id; option.textContent = item.name; $('project-select').append(option); }
    if (!list.items.length) message('요구사항 화면에서 프로젝트를 먼저 등록하세요.');
  }).catch((error) => message(error.message));
})();
