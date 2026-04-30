/* labrats single-page UI: digest viewer, config editor, run management. */

const state = {
	personas: [],
	profiles: [],
	settings: {},
	models: {},
	digestProfiles: [],
	digestData: {},          // profile name -> cached cards
	currentProfile: null,
	currentView: 'digest',   // 'digest' | 'config'
	lastCompleted: 0,
	sort: { key: 'score', dir: 'desc' },
	activePersonaKey: null,
	personaData: {},         // per-card persona detail keyed by 'pd-<cid>-<i>'
};

let _dlCounter = 0;
let _cardId = 0;

const BIORXIV_CATEGORIES = [
	'animal behavior and cognition', 'biochemistry', 'bioengineering',
	'bioinformatics', 'biophysics', 'cancer biology', 'cell biology',
	'clinical trials', 'developmental biology', 'ecology', 'epidemiology',
	'evolutionary biology', 'genetics', 'genomics', 'immunology',
	'microbiology', 'molecular biology', 'neuroscience', 'paleontology',
	'pathology', 'pharmacology and toxicology', 'physiology',
	'plant biology', 'scientific communication and education',
	'synthetic biology', 'systems biology', 'zoology',
];

const ARXIV_CATEGORIES = [
	'astro-ph', 'cond-mat', 'cs.AI', 'cs.CL', 'cs.CV', 'cs.LG', 'cs.RO',
	'eess.SP', 'gr-qc', 'hep-ex', 'hep-ph', 'hep-th', 'math.CO',
	'math.OC', 'math.ST', 'nlin.CD', 'physics.bio-ph', 'physics.chem-ph',
	'physics.med-ph', 'q-bio.BM', 'q-bio.CB', 'q-bio.GN', 'q-bio.MN',
	'q-bio.NC', 'q-bio.OT', 'q-bio.PE', 'q-bio.QM', 'q-bio.SC',
	'q-bio.TO', 'q-fin.ST', 'stat.AP', 'stat.ME', 'stat.ML',
];

const PROVIDERS = [
	{ key: 'openai',      label: 'OpenAI',          env: 'OPENAI_API_KEY' },
	{ key: 'anthropic',   label: 'Anthropic',       env: 'ANTHROPIC_API_KEY' },
	{ key: 'gemini',      label: 'Google / Gemini', env: 'GOOGLE_API_KEY' },
	{ key: 'groq',        label: 'Groq',            env: 'GROQ_API_KEY' },
	{ key: 'mistral',     label: 'Mistral',         env: 'MISTRAL_API_KEY' },
	{ key: 'cohere',      label: 'Cohere',          env: 'COHERE_API_KEY' },
	{ key: 'together_ai', label: 'Together AI',     env: 'TOGETHERAI_API_KEY' },
];

/* ── helpers ── */

const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => root.querySelectorAll(sel);

function esc(s) {
	if (s == null) return '';
	const d = document.createElement('div');
	d.textContent = String(s);
	return d.innerHTML;
}

function toast(msg) {
	const el = $('toast');
	el.textContent = msg;
	el.classList.add('show');
	setTimeout(() => el.classList.remove('show'), 1800);
}

async function api(method, path, body) {
	const opts = { method, headers: {} };
	if (body !== undefined) {
		opts.headers['Content-Type'] = 'application/json';
		opts.body = JSON.stringify(body);
	}
	const r = await fetch(path, opts);
	return r.json();
}

/* ── action dispatch ──
   Replaces inline onclick=. Every interactive element declares its
   handler via data-action="name" (click) or data-change="name" (change),
   plus optional data-* args read by the handler. */

const actions = {};

document.addEventListener('click', (ev) => {
	const t = ev.target.closest('[data-action]');
	if (t && actions[t.dataset.action]) actions[t.dataset.action](t, ev);
});

document.addEventListener('change', (ev) => {
	const t = ev.target.closest('[data-change]');
	if (t && actions[t.dataset.change]) actions[t.dataset.change](t, ev);
});

/* ── model datalist + checkbox helpers ── */

function activeModelOptions() {
	const opts = [];
	const cloud = state.models.cloud || {};
	for (const provider of Object.keys(cloud)) {
		if (cloud[provider].active) opts.push(...(cloud[provider].models || []));
	}
	const local = state.models.local || {};
	for (const label of Object.keys(local)) {
		opts.push(...(local[label] || []));
	}
	return opts;
}

function modelInputHTML(cls, value) {
	const id = `dl-${++_dlCounter}`;
	const opts = activeModelOptions()
		.map(m => `<option value="${esc(m)}">`).join('');
	return `
		<input type="text" class="${cls}" value="${esc(value)}"
		       list="${id}" autocomplete="off">
		<datalist id="${id}">${opts}</datalist>`;
}

function checkboxesHTML(items, selected, valueFn, labelFn) {
	const val = (it) => valueFn ? valueFn(it) : it;
	const lbl = (it) => labelFn ? labelFn(it) : it;
	const allChecked = items.every(it => selected.includes(val(it)));
	const all = `
		<label class="check-item check-all">
			<input type="checkbox" data-change="toggleAll"
			       ${allChecked ? 'checked' : ''}>
			Select all
		</label>`;
	const rest = items.map(it => `
		<label class="check-item">
			<input type="checkbox" value="${esc(val(it))}"
			       ${selected.includes(val(it)) ? 'checked' : ''}>
			${esc(lbl(it))}
		</label>`).join('');
	return all + rest;
}

actions.toggleAll = (cb) => {
	const grid = cb.closest('.check-grid');
	$$('input[type=checkbox]:not([data-change])', grid)
		.forEach(b => { b.checked = cb.checked; });
};

function readChecked(container, selector) {
	const sel = `${selector} input[type=checkbox]:not([data-change])`;
	return Array.from($$(sel, container))
		.filter(cb => cb.checked)
		.map(cb => cb.value);
}

/* ── navigation ── */

actions.showDigest = (btn) => showDigest(btn.dataset.profile);
actions.showConfig = () => showConfig();
actions.showConfigTab = (btn) => showConfigTab(btn.dataset.tab);

function showDigest(profileName) {
	state.currentView = 'digest';
	state.currentProfile = profileName;
	$('digest-view').classList.add('active');
	$('config-view').classList.remove('active');
	$('tab-config').classList.remove('active');
	$$('.digest-tab').forEach(b =>
		b.classList.toggle('active', b.dataset.profile === profileName));
	loadDigestCards(profileName);
}

function showConfig() {
	state.currentView = 'config';
	$('digest-view').classList.remove('active');
	$('config-view').classList.add('active');
	$('tab-config').classList.add('active');
	$$('.digest-tab').forEach(b => b.classList.remove('active'));
}

function showConfigTab(name) {
	['personas', 'profiles', 'settings', 'view'].forEach(t => {
		$(`cpanel-${t}`)?.classList.toggle('active', t === name);
	});
	$$('.config-tab-btn').forEach(btn =>
		btn.classList.toggle('active', btn.dataset.tab === name));
}

/* ── sidebar ── */

function renderSidebar() {
	$('digest-tabs').innerHTML = state.digestProfiles.map(p => `
		<button class="tab-btn digest-tab${p.name === state.currentProfile ? ' active' : ''}"
		        data-action="showDigest" data-profile="${esc(p.name)}">
			<span>${esc(p.name)}</span>
			<span class="tab-count">${p.paper_count}</span>
		</button>`).join('');
}

/* ── digest cards ── */

async function loadDigestCards(profile) {
	if (state.digestData[profile] !== undefined) {
		renderDigestCards(profile, state.digestData[profile]);
		return;
	}
	const cards = await api('GET', `/api/digest/${encodeURIComponent(profile)}`);
	state.digestData[profile] = cards;
	renderDigestCards(profile, cards);
}

function renderDigestCards(profile, cards) {
	state.personaData = {};
	closePersonaPanel();
	if (!cards || !cards.length) {
		$('digest-content').innerHTML = `
			<div class="empty-state">
				<h2>No papers yet</h2>
				<p>Click Run to fetch and analyze papers.</p>
			</div>`;
		return;
	}
	state.sort = { key: 'score', dir: 'desc' };
	const controls = `
		<div class="panel-controls">
			Sort by
			<button class="sort-btn active" data-action="sortDigest" data-key="score">
				score <span class="sort-arrow">▼</span>
			</button>
			<button class="sort-btn" data-action="sortDigest" data-key="date">
				date <span class="sort-arrow" hidden>▼</span>
			</button>
		</div>`;
	$('digest-content').innerHTML = controls + cards.map(renderOneCard).join('');
}

function parseLlmSummary(text) {
	const sections = ['Background', 'Methods', 'Results', 'Discussion'];
	const out = {};
	sections.forEach((label, i) => {
		const next = sections[i + 1];
		const tail = next ? `(?=${next}:)` : '$';
		const re = new RegExp(`${label}:\\s*([\\s\\S]*?)${tail}`, 'i');
		const m = text.match(re);
		out[label] = m ? m[1].trim() : '';
	});
	return out;
}

function renderAbstractSection(card, cid) {
	const abs = `<p class="abstract" id="abs-${cid}">${esc(card.paper.abstract)}</p>`;
	if (!card.llm_summary) return abs;
	const parsed = parseLlmSummary(card.llm_summary);
	const inner = ['Background', 'Methods', 'Results', 'Discussion']
		.map(l => `<span class="summary-label">${l}:</span> ${esc(parsed[l] || 'Not stated.')}`)
		.join('<br>');
	return `
		<div class="abstract-tabs">
			<button class="abstract-tab-btn active"
			        data-action="switchAbstractTab"
			        data-show="abs-${cid}" data-hide="sum-${cid}">Abstract</button>
			<button class="abstract-tab-btn"
			        data-action="switchAbstractTab"
			        data-show="sum-${cid}" data-hide="abs-${cid}">LLM Summary</button>
		</div>
		${abs}
		<div class="llm-summary" id="sum-${cid}">${inner}</div>`;
}

actions.switchAbstractTab = (btn) => {
	$(btn.dataset.show).style.display = 'block';
	$(btn.dataset.hide).style.display = 'none';
	$$('.abstract-tab-btn', btn.closest('.abstract-tabs'))
		.forEach(b => b.classList.remove('active'));
	btn.classList.add('active');
};

function renderOneCard(card) {
	const cid = ++_cardId;
	const p = card.paper;
	const results = card.results || [];

	const tooltipPersonas = results.map(r => {
		const scores = Object.keys(r.scores)
			.map(k => `${esc(k)}: ${r.scores[k].toFixed(0)}`).join(' · ');
		return `
			<div class="t-persona">${esc(r.persona_name)}</div>
			<div class="t-scores">${scores}</div>`;
	}).join('');

	const disputedField = card.disputed_field
		? card.disputed_field.replace(/_/g, ' ') : '';
	const consensus = card.disputed
		? `Personas disagreed on <strong>${esc(disputedField)}</strong> — worth reading yourself.`
		: 'Personas broadly agree on this paper.';
	const tensionBadge = card.disputed
		? `<span class="badge badge-tension">disputed: ${esc(disputedField)}</span>` : '';

	const personaBtns = results.map((r, i) => {
		const key = `pd-${cid}-${i}`;
		state.personaData[key] = {
			persona_name: r.persona_name,
			model: r.model || '',
			summary: r.summary,
			scores: r.scores,
		};
		return `
			<button class="persona-btn" data-action="selectPersona"
			        data-key="${key}" data-cid="${cid}">${esc(r.persona_name)}</button>`;
	}).join('');

	const authors = (p.authors || []).join(', ');
	const personaPanel = results.length ? `
		<div class="card-persona-panel" id="cpp-${cid}" hidden>
			<div class="pp-header">
				<div id="cpt-${cid}"></div>
				<button class="pp-close" data-action="closePersonaPanel" data-cid="${cid}">×</button>
			</div>
			<div class="pp-body" id="cpb-${cid}"></div>
		</div>` : '';

	return `
		<div class="card-wrapper"
		     data-score="${card.avg_score.toFixed(6)}"
		     data-date="${esc(p.date)}">
			<div class="card">
				<div class="card-header">
					<div class="card-title">
						<a href="${esc(p.url)}" target="_blank">${esc(p.title)}</a>
					</div>
					<div class="badges">
						<span class="badge badge-interest tooltip-wrap">
							${card.avg_score.toFixed(1)}
							<span class="tooltip-box">
								<div class="t-head">Score: ${card.avg_score.toFixed(2)}</div>
								<div class="t-formula">${consensus}</div>
								${results.length ? `<hr class="t-divider">${tooltipPersonas}` : ''}
							</span>
						</span>
						${tensionBadge}
					</div>
				</div>
				<div class="card-meta">
					${esc(authors)} · ${esc(p.category)} · ${esc(p.date)}
				</div>
				${renderAbstractSection(card, cid)}
				${results.length ? `<div class="persona-row">${personaBtns}</div>` : ''}
			</div>
			${personaPanel}
		</div>`;
}

actions.selectPersona = (btn) => {
	const { key, cid } = btn.dataset;
	const wasActive = state.activePersonaKey === key;
	$$('.card-persona-panel').forEach(p => { p.hidden = true; });
	$$('.persona-btn').forEach(b => b.classList.remove('active'));
	if (wasActive) { state.activePersonaKey = null; return; }
	state.activePersonaKey = key;
	btn.classList.add('active');
	const d = state.personaData[key];
	if (!d) return;
	$(`cpt-${cid}`).innerHTML = `
		<div class="pp-name">${esc(d.persona_name)}</div>
		${d.model ? `<span class="pp-model">${esc(d.model)}</span>` : ''}`;
	const scores = Object.keys(d.scores)
		.map(k => `${esc(k.replace(/_/g, ' '))}: ${d.scores[k].toFixed(0)}`)
		.join(' · ');
	$(`cpb-${cid}`).innerHTML = `
		<div class="pp-summary">${esc(d.summary)}</div>
		<div class="pp-scores">${scores}</div>`;
	$(`cpp-${cid}`).hidden = false;
};

actions.closePersonaPanel = (btn) => closePersonaPanel(btn.dataset.cid);

function closePersonaPanel(cid) {
	state.activePersonaKey = null;
	if (cid != null) {
		const panel = $(`cpp-${cid}`);
		if (panel) panel.hidden = true;
		$$(`.persona-btn[data-cid="${cid}"]`)
			.forEach(b => b.classList.remove('active'));
	} else {
		$$('.card-persona-panel').forEach(p => { p.hidden = true; });
		$$('.persona-btn').forEach(b => b.classList.remove('active'));
	}
}

actions.sortDigest = (btn) => {
	const key = btn.dataset.key;
	if (state.sort.key === key) {
		state.sort.dir = state.sort.dir === 'desc' ? 'asc' : 'desc';
	} else {
		state.sort = { key, dir: 'desc' };
	}
	const { dir } = state.sort;
	const container = $('digest-content');
	const cards = Array.from($$('.card-wrapper', container));
	cards.sort((a, b) => {
		let cmp;
		if (key === 'score') {
			cmp = parseFloat(b.dataset.score) - parseFloat(a.dataset.score);
		} else {
			cmp = b.dataset.date > a.dataset.date ? 1
				: b.dataset.date < a.dataset.date ? -1 : 0;
		}
		return dir === 'desc' ? cmp : -cmp;
	});
	cards.forEach(c => container.appendChild(c));
	$$('.sort-btn', container).forEach(b => {
		b.classList.remove('active');
		const arrow = b.querySelector('.sort-arrow');
		if (arrow) arrow.hidden = true;
	});
	btn.classList.add('active');
	const arrow = btn.querySelector('.sort-arrow');
	if (arrow) {
		arrow.hidden = false;
		arrow.textContent = dir === 'desc' ? '▼' : '▲';
	}
};

/* ── run ── */

actions.startRun = async () => {
	state.lastCompleted = 0;
	const btn = $('run-btn');
	btn.disabled = true;
	btn.textContent = 'Running...';
	const res = await api('POST', '/api/run');
	if (res.error) {
		toast(res.error);
		btn.disabled = false;
		btn.textContent = 'Run';
		return;
	}
	pollStatus();
};

async function pollStatus() {
	const s = await api('GET', '/api/run/status');
	const el = $('run-status');
	const bar = $('progress-bar');
	const btn = $('run-btn');

	if (s.status === 'running') {
		el.className = 'run-status active';
		const prog = s.progress || {};
		const parts = [];
		if (s.phase) parts.push(s.phase);
		if (s.profile) parts.push(s.profile);
		if (prog.total > 0) {
			parts.push(`${prog.completed}/${prog.total}`);
			const pct = (prog.completed / prog.total * 100).toFixed(0);
			bar.style.width = `${pct}%`;
		}
		el.textContent = parts.join(' — ');
		// stream new cards into the visible profile as they complete
		const completed = prog.completed || 0;
		if (completed > state.lastCompleted &&
		    state.currentView === 'digest' &&
		    state.currentProfile && s.profile === state.currentProfile) {
			state.lastCompleted = completed;
			delete state.digestData[state.currentProfile];
			loadDigestCards(state.currentProfile);
		}
		setTimeout(pollStatus, 1500);
		return;
	}

	if (s.status === 'done') {
		el.className = 'run-status active';
		el.textContent = 'Done';
		bar.style.width = '100%';
		btn.disabled = false;
		btn.textContent = 'Run';
		state.lastCompleted = 0;
		setTimeout(() => { bar.style.width = '0'; el.textContent = ''; }, 3000);
		state.digestData = {};
		const dp = await api('GET', '/api/digest');
		state.digestProfiles = dp;
		renderSidebar();
		if (state.currentView === 'digest' && state.currentProfile) {
			loadDigestCards(state.currentProfile);
		} else if (state.currentView === 'digest' && dp.length) {
			showDigest(dp[0].name);
		}
		return;
	}

	if (s.status === 'error') {
		el.className = 'run-status error';
		el.textContent = `Error: ${s.error || 'unknown'}`;
		bar.style.width = '0';
		btn.disabled = false;
		btn.textContent = 'Run';
		return;
	}

	el.textContent = '';
	bar.style.width = '0';
	btn.disabled = false;
	btn.textContent = 'Run';
}

/* ── personas (config) ── */

function personaCardHTML(p) {
	const fields = p.scored_fields
		.map(f => `<span class="badge badge-field">${esc(f)}</span>`).join('');
	const model = p.model
		? `<span class="badge badge-model">${esc(p.model)}</span>` : '';
	const role = p.role.length > 120 ? p.role.slice(0, 120) + '...' : p.role;
	return `
		<div class="card${p.enabled ? '' : ' disabled'}" id="pc-${esc(p.stem)}">
			<div class="card-header">
				<div class="card-title">${esc(p.name)}</div>
				<label class="toggle">
					<input type="checkbox" data-change="togglePersonaEnabled"
					       data-stem="${esc(p.stem)}"${p.enabled ? ' checked' : ''}>
					<span class="toggle-slider"></span>
				</label>
			</div>
			<div class="card-meta">${esc(role)}</div>
			<div class="badges">${fields}${model}</div>
			<div class="btn-row">
				<button class="btn" data-action="toggleEdit" data-stem="${esc(p.stem)}">Edit</button>
				<button class="btn btn-danger" data-action="deletePersona" data-stem="${esc(p.stem)}">Delete</button>
			</div>
			<div class="edit-form" id="ef-${esc(p.stem)}">
				${personaFormHTML(p, 'update')}
			</div>
		</div>`;
}

function personaFormHTML(p, mode) {
	const stem = p?.stem || '';
	const name = p?.name || '';
	const role = p?.role || '';
	const fields = p
		? p.scored_fields.join(', ')
		: 'methodological_rigor, novelty, relevance';
	const model = p?.model || '';
	const isUpdate = mode === 'update';
	const saveAction = isUpdate ? 'savePersona' : 'createPersona';
	const cancelAction = isUpdate ? 'toggleEdit' : 'hideNewPersona';
	return `
		<div class="form-group">
			<label>Name</label>
			<input type="text" class="pf-name" value="${esc(name)}">
		</div>
		<div class="form-group">
			<label>Role</label>
			<textarea class="pf-role">${esc(role)}</textarea>
		</div>
		<div class="form-group">
			<label>Scored fields</label>
			<input type="text" class="pf-fields" value="${esc(fields)}">
			<div class="form-hint">Comma-separated</div>
		</div>
		<div class="form-group">
			<label>Model override</label>
			${modelInputHTML('pf-model', model)}
			<div class="form-hint">Leave blank for default</div>
		</div>
		<div class="btn-row">
			<button class="btn btn-primary" data-action="${saveAction}" data-stem="${esc(stem)}">Save</button>
			<button class="btn" data-action="${cancelAction}" data-stem="${esc(stem)}">Cancel</button>
		</div>`;
}

function renderPersonas() {
	$('persona-list').innerHTML = state.personas.map(personaCardHTML).join('');
}

function readPersonaForm(container) {
	return {
		name: container.querySelector('.pf-name').value.trim(),
		role: container.querySelector('.pf-role').value.trim(),
		scored_fields: container.querySelector('.pf-fields').value
			.split(',').map(s => s.trim()).filter(Boolean),
		model: container.querySelector('.pf-model').value.trim() || null,
		enabled: true,
	};
}

actions.showNewPersona = () => {
	$('new-persona-form').hidden = false;
	$('new-persona-fields').innerHTML = personaFormHTML(null, 'create');
};
actions.hideNewPersona = () => { $('new-persona-form').hidden = true; };

actions.createPersona = async () => {
	const data = readPersonaForm($('new-persona-fields'));
	if (!data.name) return toast('Name is required');
	if (!data.scored_fields.length) return toast('At least one scored field required');
	state.personas = await api('POST', '/api/personas', data);
	renderPersonas(); renderProfiles();
	$('new-persona-form').hidden = true;
	toast('Persona created');
};

actions.toggleEdit = (btn) => {
	$(`ef-${btn.dataset.stem}`).classList.toggle('open');
};

actions.savePersona = async (btn) => {
	const stem = btn.dataset.stem;
	const data = readPersonaForm($(`ef-${stem}`));
	const orig = state.personas.find(p => p.stem === stem);
	if (orig) data.enabled = orig.enabled;
	if (!data.name) return toast('Name is required');
	if (!data.scored_fields.length) return toast('At least one scored field required');
	state.personas = await api('PUT', `/api/personas/${encodeURIComponent(stem)}`, data);
	renderPersonas(); renderProfiles();
	toast('Persona saved');
};

actions.deletePersona = async (btn) => {
	if (!confirm('Delete this persona?')) return;
	const stem = btn.dataset.stem;
	state.personas = await api('DELETE', `/api/personas/${encodeURIComponent(stem)}`);
	renderPersonas(); renderProfiles();
	toast('Persona deleted');
};

actions.togglePersonaEnabled = async (cb) => {
	const stem = cb.dataset.stem;
	const p = state.personas.find(x => x.stem === stem);
	if (!p) return;
	const data = { ...p, enabled: !p.enabled };
	delete data.stem;
	state.personas = await api('PUT', `/api/personas/${encodeURIComponent(stem)}`, data);
	renderPersonas(); renderProfiles();
	toast(data.enabled ? 'Enabled' : 'Disabled');
};

/* ── profiles (config) ── */

function profileCardHTML(prof, idx) {
	const badges = [
		...(prof.model ? [`<span class="badge badge-model">${esc(prof.model)}</span>`] : []),
		...(prof.keywords || []).map(k => `<span class="badge badge-kw">${esc(k)}</span>`),
		...(prof.categories || []).map(c => `<span class="badge badge-cat">${esc(c)}</span>`),
		...(prof.arxiv_categories || []).map(c => `<span class="badge badge-arxiv">${esc(c)}</span>`),
	].join('');
	const assigned = prof.personas || [];
	const assignedText = assigned.length
		? `${assigned.length} persona${assigned.length > 1 ? 's' : ''} assigned`
		: 'all personas';
	const maxP = prof.max_papers || 500;
	return `
		<div class="card" id="prc-${idx}">
			<div class="card-header">
				<div class="card-title">${esc(prof.name)}</div>
				<span class="card-aside">${esc(assignedText)} · max ${maxP}</span>
			</div>
			<div class="badges badges-row">${badges}</div>
			<div class="btn-row">
				<button class="btn" data-action="toggleProfileEdit" data-idx="${idx}">Edit</button>
				<button class="btn btn-danger" data-action="deleteProfile" data-idx="${idx}">Delete</button>
			</div>
			<div class="edit-form" id="pef-${idx}">
				${profileFormHTML(prof, idx, 'update')}
			</div>
		</div>`;
}

function profileFormHTML(prof, idx, mode) {
	const name = prof?.name || '';
	const kws = prof ? (prof.keywords || []).join(', ') : '';
	const selCats = prof?.categories || [];
	const selArxiv = prof?.arxiv_categories || [];
	const maxP = prof?.max_papers || 500;
	const profMdl = prof?.model || '';
	const purpose = prof?.purpose || '';
	const personaPrompt = prof?.persona_prompt || '';
	const assigned = prof?.personas || [];
	const isUpdate = mode === 'update';
	const saveAction = isUpdate ? 'saveProfile' : 'createProfile';
	const cancelAction = isUpdate ? 'toggleProfileEdit' : 'hideNewProfile';

	return `
		<div class="form-group">
			<label>Name</label>
			<input type="text" class="pr-name" value="${esc(name)}">
		</div>
		<div class="form-group">
			<label>Purpose</label>
			<textarea class="pr-purpose" rows="3">${esc(purpose)}</textarea>
			<div class="form-hint">The 'why' — your research context, open questions, or what you're watching for. Passed to all personas.</div>
		</div>
		<div class="form-group">
			<label>Keywords</label>
			<input type="text" class="pr-kws" value="${esc(kws)}">
			<div class="form-hint">Comma-separated</div>
		</div>
		<div class="form-group">
			<label>Default model</label>
			${modelInputHTML('pr-model', profMdl)}
			<div class="form-hint">Leave blank for CLI default</div>
		</div>
		<div class="form-group">
			<label>Categories (biorxiv)</label>
			<div class="check-grid pr-cats">
				${checkboxesHTML(BIORXIV_CATEGORIES, selCats)}
			</div>
		</div>
		<div class="form-group">
			<label>Categories (arxiv)</label>
			<div class="check-grid pr-arxiv">
				${checkboxesHTML(ARXIV_CATEGORIES, selArxiv)}
			</div>
		</div>
		<div class="form-group">
			<label>Max papers</label>
			<input type="number" class="pr-max" value="${maxP}" min="1">
		</div>
		<div class="form-group">
			<label>Persona prompt</label>
			<textarea class="pr-persona-prompt" rows="5">${esc(personaPrompt)}</textarea>
			<div class="form-hint">
				Use {role} as placeholder. Leave blank to use the default prompt.
			</div>
		</div>
		<div class="form-group">
			<label>LabRats</label>
			<div class="check-grid pr-personas">
				${checkboxesHTML(state.personas, assigned, p => p.stem, p => p.name)}
			</div>
			<div class="form-hint">Unchecking all = use all enabled personas</div>
		</div>
		<div class="btn-row">
			<button class="btn btn-primary" data-action="${saveAction}" data-idx="${idx}">Save</button>
			<button class="btn" data-action="${cancelAction}" data-idx="${idx}">Cancel</button>
		</div>`;
}

function renderProfiles() {
	$('profile-list').innerHTML = state.profiles.map(profileCardHTML).join('');
}

function readProfileForm(container) {
	const mdl = container.querySelector('.pr-model').value.trim();
	const purpose = container.querySelector('.pr-purpose').value.trim();
	const pp = container.querySelector('.pr-persona-prompt').value.trim();
	const selectedPersonas = readChecked(container, '.pr-personas');
	const data = {
		name: container.querySelector('.pr-name').value.trim(),
		keywords: container.querySelector('.pr-kws').value
			.split(',').map(s => s.trim()).filter(Boolean),
		categories: readChecked(container, '.pr-cats'),
		arxiv_categories: readChecked(container, '.pr-arxiv'),
		max_papers: parseInt(container.querySelector('.pr-max').value, 10) || 500,
	};
	if (mdl) data.model = mdl;
	if (purpose) data.purpose = purpose;
	if (pp) data.persona_prompt = pp;
	if (selectedPersonas.length) data.personas = selectedPersonas;
	return data;
}

actions.showNewProfile = () => {
	$('new-profile-form').hidden = false;
	$('new-profile-fields').innerHTML = profileFormHTML(null, -1, 'create');
};
actions.hideNewProfile = () => { $('new-profile-form').hidden = true; };

async function refreshSidebar() {
	state.digestProfiles = await api('GET', '/api/digest');
	renderSidebar();
}

actions.createProfile = async () => {
	const data = readProfileForm($('new-profile-fields'));
	if (!data.name) return toast('Name is required');
	if (!data.keywords.length && !data.categories.length) {
		return toast('Need at least one keyword or category');
	}
	state.profiles = await api('POST', '/api/profiles', data);
	renderProfiles();
	$('new-profile-form').hidden = true;
	toast('Profile created');
	refreshSidebar();
};

actions.toggleProfileEdit = (btn) => {
	$(`pef-${btn.dataset.idx}`).classList.toggle('open');
};

actions.saveProfile = async (btn) => {
	const idx = btn.dataset.idx;
	const data = readProfileForm($(`pef-${idx}`));
	if (!data.name) return toast('Name is required');
	state.profiles = await api('PUT', `/api/profiles/${idx}`, data);
	renderProfiles();
	toast('Profile saved');
	refreshSidebar();
};

actions.deleteProfile = async (btn) => {
	if (!confirm('Delete this profile?')) return;
	const idx = btn.dataset.idx;
	state.profiles = await api('DELETE', `/api/profiles/${idx}`);
	renderProfiles();
	toast('Profile deleted');
	refreshSidebar();
};

/* ── settings (config) ── */

function pickBadgeHTML(m) {
	return `<span class="badge badge-model badge-clickable"
	              data-action="pickModel" data-name="${esc(m)}">${esc(m)}</span>`;
}

function renderDefaultModel() {
	let html = `<div class="form-group">
		${modelInputHTML('st-default-model', state.settings.default_model || '')}
	</div>`;

	const local = state.models.local || {};
	const cloud = state.models.cloud || {};
	const labels = Object.keys(local);
	const activeCloud = Object.keys(cloud).filter(p => cloud[p].active);

	if (labels.length || activeCloud.length) {
		html += `<div class="model-list">
			<div class="model-list-title">Available models</div>`;
		for (const label of labels) {
			const models = local[label] || [];
			html += `
				<div class="model-list-group">
					<span class="model-list-label">${esc(label)}</span>
					<div class="model-list-badges">${models.map(pickBadgeHTML).join('')}</div>
				</div>`;
		}
		for (const provider of activeCloud) {
			const info = cloud[provider];
			const models = info.models || [];
			const link = info.docs
				? ` <a href="${esc(info.docs)}" target="_blank" class="model-list-link">[all models]</a>`
				: '';
			html += `
				<details class="model-list-group">
					<summary>
						<span class="model-list-label">${esc(provider)}</span>
						<span class="model-list-count">(${models.length})</span>${link}
					</summary>
					<div class="model-list-badges">${models.map(pickBadgeHTML).join('')}</div>
				</details>`;
		}
		html += `</div>`;
	}

	$('default-model-field').innerHTML = html;
}

actions.pickModel = (el) => {
	const input = document.querySelector('.st-default-model');
	if (input) input.value = el.dataset.name;
};

function renderSettings() {
	renderDefaultModel();
	const keys = state.settings.api_keys || {};
	const cloud = state.models.cloud || {};
	$('settings-fields').innerHTML = PROVIDERS.map(p => {
		const info = cloud[p.key] || {};
		const dot = `<span class="key-dot${info.active ? ' active' : ''}">●</span>`;
		const docs = info.docs
			? ` <a href="${esc(info.docs)}" target="_blank" class="key-docs">[models]</a>`
			: '';
		return `
			<div class="form-group">
				<label>
					${dot} ${esc(p.label)}${docs}
					<span class="key-env">($${p.env})</span>
				</label>
				<div class="key-row">
					<input type="password" class="sk-${p.key} key-input" value="${esc(keys[p.key] || '')}">
					<button class="btn" data-action="toggleVis">Show</button>
				</div>
			</div>`;
	}).join('');
}

actions.toggleVis = (btn) => {
	const input = btn.previousElementSibling;
	const showing = input.type === 'text';
	input.type = showing ? 'password' : 'text';
	btn.textContent = showing ? 'Show' : 'Hide';
};

actions.saveDefaultModel = async () => {
	const el = document.querySelector('.st-default-model');
	await api('PUT', '/api/settings', { default_model: el?.value.trim() || '' });
	toast('Default model saved');
	state.settings = await api('GET', '/api/settings');
	renderDefaultModel();
};

actions.saveApiKeys = async () => {
	const keys = {};
	for (const p of PROVIDERS) {
		const el = document.querySelector(`.sk-${p.key}`);
		if (el) keys[p.key] = el.value.trim();
	}
	await api('PUT', '/api/settings', { api_keys: keys });
	toast('API keys saved');
	const [s, m] = await Promise.all([
		api('GET', '/api/settings'),
		api('GET', '/api/models'),
	]);
	state.settings = s;
	state.models = m;
	renderSettings();
};

/* ── view tab ── */

// Apply persisted dark mode before first paint.
if (localStorage.getItem('darkMode')) document.body.classList.add('dark');

actions.toggleDarkMode = (input) => {
	document.body.classList.toggle('dark', input.checked);
	localStorage.setItem('darkMode', input.checked ? '1' : '');
};


/* ── init ── */

async function init() {
	const [personas, profiles, settings, models, digestProfiles] = await Promise.all([
		api('GET', '/api/personas'),
		api('GET', '/api/profiles'),
		api('GET', '/api/settings'),
		api('GET', '/api/models'),
		api('GET', '/api/digest'),
	]);
	Object.assign(state, { personas, profiles, settings, models, digestProfiles });

	const dmToggle = document.getElementById('dark-mode-toggle');
	if (dmToggle) dmToggle.checked = !!localStorage.getItem('darkMode');

	renderPersonas();
	renderProfiles();
	renderSettings();
	renderSidebar();

	if (digestProfiles.length) {
		showDigest(digestProfiles[0].name);
	} else {
		$('digest-content').innerHTML = `
			<div class="empty-state">
				<h2>Welcome to labrats</h2>
				<p>Configure your profiles in Settings, then click Run.</p>
			</div>`;
	}

	// reconnect to any in-progress background run
	const s = await api('GET', '/api/run/status');
	if (s.status === 'running') {
		const btn = $('run-btn');
		btn.disabled = true;
		btn.textContent = 'Running...';
		pollStatus();
	}
}

init();
