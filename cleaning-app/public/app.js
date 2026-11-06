'use strict';

// ---------------------------------------------------------------- helpers

const DAY = 86400;
let S = null;                       // latest /api/state
let tab = load('tab') || 'chores';
let meId = Number(load('me')) || null;

function load(key) { try { return localStorage.getItem(key); } catch { return null; } }
function save(key, val) { try { localStorage.setItem(key, val); } catch { /* ignore */ } }

// Tiny DOM builder: h('div', {class: 'x', onclick: fn}, 'text', child, ...)
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'style') el.style.cssText = v;
    else if (v === true) el.setAttribute(k, '');
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function ago(seconds) {
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < DAY) return `${Math.floor(seconds / 3600)}h ago`;
  const d = Math.floor(seconds / DAY);
  return d === 1 ? 'yesterday' : `${d} days ago`;
}

function person(id) { return S.people.find(p => p.id === id); }
function me() { return person(meId) || null; }

let toastTimer;
function toast(msg, action) {
  const t = document.getElementById('toast');
  t.replaceChildren(h('span', null, msg));
  if (action) t.append(h('button', { onclick: () => { t.hidden = true; action.run(); } }, action.label));
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, action ? 6000 : 3000);
}

// Run an action, show errors, then refresh.
async function act(fn) {
  try { await fn(); } catch (e) { toast(e.message); }
  await refresh();
}

// Modal list of people; resolves to a person or null.
function pickPerson(title) {
  const dlg = document.getElementById('picker');
  return new Promise(resolve => {
    const choose = p => { dlg.close(); resolve(p); };
    dlg.replaceChildren(
      h('h3', null, title),
      h('div', { class: 'choices' },
        S.people.map(p => h('button', { onclick: () => choose(p) },
          h('span', { class: 'dot', style: `background:${p.color}` }), p.name)),
        h('button', { onclick: () => choose(null) }, 'Cancel')));
    dlg.onclose = () => resolve(null);
    dlg.showModal();
  });
}

// ---------------------------------------------------------------- actions

async function completeChore(chore, who) {
  who = who || me() || await pickPerson(`Who did “${chore.name}”?`);
  if (!who) return;
  await act(async () => {
    const r = await api('POST', '/api/complete', { chore_id: chore.id, person_id: who.id });
    toast(`+${r.points} for ${who.name}`, {
      label: 'Undo', run: () => act(() => api('DELETE', `/api/completions/${r.id}`)),
    });
  });
}

async function redeemReward(reward, who) {
  if (!who || !confirm(`Spend ${reward.cost} points of ${who.name}'s on “${reward.name}”?`)) return;
  await act(async () => {
    const r = await api('POST', '/api/redeem', { reward_id: reward.id, person_id: who.id });
    toast(`🎉 ${who.name} got “${reward.name}”`, {
      label: 'Undo', run: () => act(() => api('DELETE', `/api/redemptions/${r.id}`)),
    });
  });
}

// ---------------------------------------------------------------- views

function choreStatus(c) {
  if (!c.every_days) return { sort: Infinity, text: 'Anytime', cls: '' };
  const due = c.last_at ? c.last_at + c.every_days * DAY : 0;
  const left = due - S.now;
  if (left <= 0) {
    const late = Math.floor(-left / DAY);
    return { sort: due, text: !c.last_at ? 'Due' : late >= 1 ? `Overdue ${late}d` : 'Due', cls: 'status-due' };
  }
  const days = Math.ceil(left / DAY);
  return { sort: due, text: days === 1 ? 'Due tomorrow' : `Due in ${days} days`, cls: days <= 1 ? 'status-soon' : 'status-ok' };
}

const views = {
  chores() {
    if (!S.chores.length) return h('p', { class: 'empty' }, 'No chores yet. Add some under ⚙️ Manage.');
    const list = S.chores.map(c => ({ c, st: choreStatus(c) })).sort((a, b) => a.st.sort - b.st.sort);
    return list.map(({ c, st }) => {
      const last = c.last_at && person(c.last_by);
      return h('div', { class: 'card' },
        h('div', { class: 'grow' },
          h('div', { class: 'title' }, c.name),
          h('div', { class: 'sub' },
            h('span', { class: st.cls }, st.text),
            c.last_at ? ` · ${last ? last.name + ' ' : ''}${ago(S.now - c.last_at)}` : ' · never done')),
        h('span', { class: 'pts' }, `+${c.points}`),
        h('button', { class: 'btn ghost', title: 'Someone else did it', 'aria-label': 'Someone else did it',
          onclick: async () => { const p = await pickPerson(`Who did “${c.name}”?`); if (p) completeChore(c, p); } }, '👥'),
        h('button', { class: 'btn', onclick: () => completeChore(c) }, 'Done'));
    });
  },

  scores() {
    if (!S.people.length) return h('p', { class: 'empty' }, 'Add people under ⚙️ Manage.');
    const byWeek = [...S.people].sort((a, b) => b.week - a.week);
    return [
      h('h2', null, 'Last 7 days'),
      byWeek.map((p, i) => h('div', { class: 'card' },
        h('span', { class: 'rank' }, ['🥇', '🥈', '🥉'][i] || i + 1),
        h('span', { class: 'dot', style: `background:${p.color}` }),
        h('div', { class: 'grow' },
          h('div', { class: 'title' }, p.name),
          h('div', { class: 'sub' }, `${p.lifetime} earned all-time`)),
        h('div', { class: 'big' }, p.week))),
      h('h2', null, 'Points to spend'),
      S.people.map(p => h('div', { class: 'card' },
        h('span', { class: 'dot', style: `background:${p.color}` }),
        h('div', { class: 'grow title' }, p.name),
        h('span', { class: 'pts' }, p.balance))),
    ];
  },

  rewards() {
    if (!S.rewards.length) return h('p', { class: 'empty' }, 'No rewards yet. Add some under ⚙️ Manage.');
    const who = me();
    return [
      h('p', { class: 'hint' }, who ? `${who.name} has ${who.balance} points.` : 'Pick who you are at the top to redeem.'),
      S.rewards.map(r => h('div', { class: 'card' },
        h('div', { class: 'grow title' }, r.name),
        h('span', { class: 'pts' }, r.cost),
        h('button', { class: 'btn', disabled: !who || who.balance < r.cost, onclick: () => redeemReward(r, who) }, 'Redeem'))),
    ];
  },

  history() {
    if (!S.history.length) return h('p', { class: 'empty' }, 'Nothing yet. Go clean something!');
    return S.history.map(e => {
      const p = person(e.person_id);
      return h('div', { class: 'card' },
        h('span', { class: 'dot', style: `background:${p ? p.color : 'var(--muted)'}` }),
        h('div', { class: 'grow' },
          h('div', { class: 'title' }, e.kind === 'redemption' ? `🎁 ${e.what}` : e.what),
          h('div', { class: 'sub' }, `${e.person} · ${ago(S.now - e.at)}`)),
        h('span', { class: e.delta < 0 ? 'pts neg' : 'pts' }, e.delta > 0 ? `+${e.delta}` : e.delta),
        h('button', { class: 'btn ghost', title: 'Undo', 'aria-label': 'Undo', onclick: () => {
          if (confirm(`Remove “${e.what}” by ${e.person}?`)) act(() => api('DELETE', `/api/${e.kind}s/${e.id}`));
        } }, '✕'));
    });
  },

  manage() {
    const addForm = (fields, submit) => {
      const inputs = fields.map(f => h('input', f));
      return h('form', { class: 'add', onsubmit: ev => {
        ev.preventDefault();
        const vals = inputs.map(i => i.value.trim());
        act(async () => { await submit(...vals); inputs.forEach(i => { i.value = ''; }); });
      } }, inputs, h('button', { class: 'btn' }, 'Add'));
    };

    const row = (table, item, label, sub, editPrompts) => h('div', { class: 'card' },
      h('div', { class: 'grow' }, h('div', { class: 'title' }, label), sub && h('div', { class: 'sub' }, sub)),
      h('button', { class: 'btn ghost small', onclick: () => {
        const body = {};
        for (const [key, question] of editPrompts) {
          const cur = item[key] == null ? '' : String(item[key]);
          const val = prompt(question, cur);
          if (val === null) return;            // cancelled
          body[key] = val.trim();
        }
        act(() => api('PATCH', `/api/${table}/${item.id}`, body));
      } }, 'Edit'),
      h('button', { class: 'btn ghost small', onclick: () => {
        if (confirm(`Remove “${item.name}”? Its history is kept.`)) act(() => api('DELETE', `/api/${table}/${item.id}`));
      } }, 'Remove'));

    return [
      h('h2', null, 'People'),
      addForm([{ placeholder: 'Name', required: true, maxlength: 40 }],
        name => api('POST', '/api/people', { name })),
      S.people.map(p => row('people', p, p.name, null, [['name', 'Name']])),

      h('h2', null, 'Chores'),
      addForm([
        { placeholder: 'Chore', required: true, maxlength: 80 },
        { placeholder: 'Points', class: 'num', type: 'number', min: 0, required: true, inputmode: 'numeric' },
        { placeholder: 'Days', class: 'num', type: 'number', min: 1, max: 365, inputmode: 'numeric', title: 'Leave blank for “anytime”' },
      ], (name, points, every_days) => api('POST', '/api/chores', { name, points, every_days })),
      h('p', { class: 'hint' }, 'Days = repeat every N days. Leave it blank for chores with no schedule.'),
      S.chores.map(c => row('chores', c, c.name,
        `${c.points} pts · ${c.every_days ? `every ${c.every_days} day${c.every_days > 1 ? 's' : ''}` : 'anytime'}`,
        [['name', 'Chore name'], ['points', 'Points'], ['every_days', 'Repeat every N days (blank = anytime)']])),

      h('h2', null, 'Rewards'),
      addForm([
        { placeholder: 'Reward', required: true, maxlength: 80 },
        { placeholder: 'Cost', class: 'num', type: 'number', min: 1, required: true, inputmode: 'numeric' },
      ], (name, cost) => api('POST', '/api/rewards', { name, cost })),
      S.rewards.map(r => row('rewards', r, r.name, `${r.cost} pts`, [['name', 'Reward name'], ['cost', 'Cost']])),

      h('h2', null, 'Backup'),
      h('p', { class: 'hint' }, 'Downloads a copy of the whole database file.'),
      h('a', { class: 'btn', href: '/api/backup', download: '' }, 'Download backup'),
    ];
  },
};

// ---------------------------------------------------------------- render loop

function render() {
  const sel = document.getElementById('me');
  sel.replaceChildren(
    h('option', { value: '' }, '—'),
    ...S.people.map(p => h('option', { value: p.id, selected: p.id === meId }, p.name)));

  for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('on', b.dataset.tab === tab);

  const y = window.scrollY;
  document.getElementById('view').replaceChildren(...[views[tab]()].flat(Infinity));
  window.scrollTo(0, y);
}

async function refresh() {
  try {
    S = await api('GET', '/api/state');
    render();
  } catch (e) {
    if (!S) document.getElementById('view').replaceChildren(h('p', { class: 'empty' }, `Can't reach the server: ${e.message}`));
    else toast("Can't reach the server");
  }
}

document.getElementById('tabs').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  tab = b.dataset.tab;
  save('tab', tab);
  window.scrollTo(0, 0);
  if (S) render();
});

document.getElementById('me').addEventListener('change', e => {
  meId = Number(e.target.value) || null;
  save('me', meId || '');
  render();
});

// Keep in sync with other phones.
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
setInterval(() => { if (!document.hidden) refresh(); }, 30000);

if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});

refresh();
