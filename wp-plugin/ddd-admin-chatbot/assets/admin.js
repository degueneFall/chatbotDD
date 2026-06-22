/* DDD Chatbot Admin — admin.js */
/* globals DDD_ADMIN */

let DDD_ALL_QUESTIONS = [];

// ── Initialisation ────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    dddRefresh();
    document.getElementById('ddd-filter-status').addEventListener('change', dddRender);
    document.getElementById('ddd-search').addEventListener('input', dddRender);
});

// ── Charger les questions depuis Flask ───────────────────────────────────────
async function dddRefresh() {
    const url = `${DDD_ADMIN.api_base}/admin/unknown-queries/data?token=${encodeURIComponent(DDD_ADMIN.token)}`;
    document.getElementById('ddd-questions-list').innerHTML = '<p>Chargement…</p>';
    try {
        const r    = await fetch(url);
        const data = await r.json();
        DDD_ALL_QUESTIONS = (data.queries || []).sort((a, b) => b.count - a.count);
        dddUpdateStats();
        dddRender();
    } catch (e) {
        document.getElementById('ddd-questions-list').innerHTML =
            `<div class="notice notice-error"><p>Erreur de connexion à l'API Flask : ${e.message}</p></div>`;
    }
}

function dddUpdateStats() {
    const total     = DDD_ALL_QUESTIONS.length;
    const attente   = DDD_ALL_QUESTIONS.filter(q => q.status === 'en_attente' || !q.status).length;
    const repondus  = DDD_ALL_QUESTIONS.filter(q => q.status === 'repondu').length;
    const redirigés = DDD_ALL_QUESTIONS.filter(q => q.status === 'redirige').length;
    document.getElementById('ddd-stats').innerHTML =
        `<span class="ddd-stat">📋 Total : <strong>${total}</strong></span>
         <span class="ddd-stat ddd-stat-warning">⏳ En attente : <strong>${attente}</strong></span>
         <span class="ddd-stat ddd-stat-success">✅ Répondus : <strong>${repondus}</strong></span>
         <span class="ddd-stat ddd-stat-info">↗ Redirigés : <strong>${redirigés}</strong></span>`;
}

// ── Rendu de la liste ─────────────────────────────────────────────────────────
function dddRender() {
    const search = document.getElementById('ddd-search').value.toLowerCase();
    const filter = document.getElementById('ddd-filter-status').value;

    const list = DDD_ALL_QUESTIONS.filter(q => {
        const status = q.status || 'en_attente';
        if (filter !== 'all' && status !== filter) return false;
        if (search && !q.question.toLowerCase().includes(search)) return false;
        return true;
    });

    if (!list.length) {
        document.getElementById('ddd-questions-list').innerHTML =
            '<p class="ddd-empty">Aucune question pour ces filtres.</p>';
        return;
    }

    const rows = list.map(q => {
        const status    = q.status || 'en_attente';
        const statusBadge = {
            en_attente: '<span class="ddd-badge ddd-badge-warning">⏳ En attente</span>',
            repondu:    '<span class="ddd-badge ddd-badge-success">✅ Répondu</span>',
            redirige:   '<span class="ddd-badge ddd-badge-info">↗ Redirigé</span>',
        }[status] || '';

        const resolvedInfo = status === 'repondu'
            ? `<div class="ddd-resolved-info">💬 <em>${esc(q.reponse_text || '')}</em>${q.page_cible_url ? ` — <a href="${esc(q.page_cible_url)}" target="_blank">Page WP</a>` : ''}</div>`
            : status === 'redirige'
            ? `<div class="ddd-resolved-info">↗ <a href="${esc(q.page_cible_url || '')}" target="_blank">${esc(q.page_cible_url || '')}</a></div>`
            : '';

        const actions = status === 'en_attente'
            ? `<button class="button button-primary button-small" onclick="dddOpenReponse('${esc(q.id)}', ${JSON.stringify(q.question)})">✏️ Répondre</button>
               <button class="button button-small" onclick="dddOpenRedirect('${esc(q.id)}', ${JSON.stringify(q.question)})">↗ Rediriger</button>`
            : `<button class="button button-small" onclick="dddOpenReponse('${esc(q.id)}', ${JSON.stringify(q.question)})">✏️ Modifier</button>`;

        return `<div class="ddd-question-card ddd-status-${status}">
            <div class="ddd-question-header">
                <span class="ddd-question-text">${esc(q.question)}</span>
                ${statusBadge}
                <span class="ddd-badge ddd-badge-count">${q.count}×</span>
            </div>
            <div class="ddd-question-meta">
                Première fois : ${q.first_seen || '—'} &nbsp;|&nbsp; Dernière fois : ${q.last_seen || '—'}
                ${q.note ? ` &nbsp;|&nbsp; Note : <em>${esc(q.note)}</em>` : ''}
            </div>
            ${resolvedInfo}
            <div class="ddd-question-actions">${actions}</div>
        </div>`;
    });

    document.getElementById('ddd-questions-list').innerHTML = rows.join('');
}

// ── Modals ────────────────────────────────────────────────────────────────────
function dddOpenReponse(uid, question) {
    document.getElementById('ddd-modal-uid').value              = uid;
    document.getElementById('ddd-modal-question-text').textContent = question;
    document.getElementById('ddd-modal-reponse-text').value     = '';
    document.getElementById('ddd-modal-page-id').value          = '';
    document.getElementById('ddd-modal-reponse-msg').textContent = '';

    // Préremplir si déjà répondu
    const existing = DDD_ALL_QUESTIONS.find(q => q.id === uid);
    if (existing && existing.reponse_text) {
        document.getElementById('ddd-modal-reponse-text').value = existing.reponse_text;
    }

    dddShowModal('ddd-modal-reponse');
}

function dddOpenRedirect(uid, question) {
    document.getElementById('ddd-modal-redirect-uid').value               = uid;
    document.getElementById('ddd-modal-redirect-question').textContent    = question;
    document.getElementById('ddd-modal-redirect-page-id').value           = '';
    document.getElementById('ddd-modal-redirect-msg').textContent         = '';
    dddShowModal('ddd-modal-redirect');
}

function dddShowModal(id) {
    document.getElementById(id).style.display          = 'flex';
    document.getElementById('ddd-modal-overlay').style.display = 'block';
}

function dddCloseModal(id) {
    document.getElementById(id).style.display          = 'none';
    document.getElementById('ddd-modal-overlay').style.display = 'none';
}

function dddCloseAllModals() {
    ['ddd-modal-reponse', 'ddd-modal-redirect'].forEach(dddCloseModal);
}

// ── Soumettre une réponse ─────────────────────────────────────────────────────
async function dddSubmitReponse() {
    const uid          = document.getElementById('ddd-modal-uid').value;
    const reponse_text = document.getElementById('ddd-modal-reponse-text').value.trim();
    const pageSelect   = document.getElementById('ddd-modal-page-id');
    const page_id      = pageSelect.value;
    const page_url     = page_id ? pageSelect.options[pageSelect.selectedIndex].dataset.url : '';
    const question     = document.getElementById('ddd-modal-question-text').textContent;
    const msgEl        = document.getElementById('ddd-modal-reponse-msg');

    if (!reponse_text) { msgEl.textContent = '⚠️ La réponse ne peut pas être vide.'; return; }

    msgEl.textContent = 'Enregistrement…';

    // 1. Injecter dans la page WordPress si demandé
    if (page_id) {
        const fd = new FormData();
        fd.append('action',       'ddd_inject_qa');
        fd.append('nonce',        DDD_ADMIN.nonce);
        fd.append('page_id',      page_id);
        fd.append('question',     question);
        fd.append('reponse_text', reponse_text);

        const wpRes = await fetch(DDD_ADMIN.ajax_url, { method: 'POST', body: fd });
        const wpData = await wpRes.json();
        if (!wpData.success) {
            msgEl.textContent = `❌ Erreur WP : ${wpData.data}`;
            return;
        }
    }

    // 2. Mettre à jour dans Flask
    const flaskUrl = `${DDD_ADMIN.api_base}/admin/unknown-queries/${uid}/resolve?token=${encodeURIComponent(DDD_ADMIN.token)}`;
    const flaskRes = await fetch(flaskUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            status:        'repondu',
            reponse_text:  reponse_text,
            page_cible_id: page_id ? parseInt(page_id) : null,
            page_cible_url: page_url,
        }),
    });
    const flaskData = await flaskRes.json();
    if (flaskData.status === 'ok') {
        msgEl.textContent = '✅ Réponse enregistrée !';
        setTimeout(() => { dddCloseAllModals(); dddRefresh(); }, 1200);
    } else {
        msgEl.textContent = `❌ Erreur Flask : ${JSON.stringify(flaskData)}`;
    }
}

// ── Soumettre une redirection ─────────────────────────────────────────────────
async function dddSubmitRedirect() {
    const uid      = document.getElementById('ddd-modal-redirect-uid').value;
    const select   = document.getElementById('ddd-modal-redirect-page-id');
    const page_id  = select.value;
    const page_url = page_id ? select.options[select.selectedIndex].dataset.url : '';
    const msgEl    = document.getElementById('ddd-modal-redirect-msg');

    if (!page_id) { msgEl.textContent = '⚠️ Choisissez une page cible.'; return; }

    msgEl.textContent = 'Enregistrement…';

    const flaskUrl = `${DDD_ADMIN.api_base}/admin/unknown-queries/${uid}/resolve?token=${encodeURIComponent(DDD_ADMIN.token)}`;
    const res = await fetch(flaskUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            status:         'redirige',
            page_cible_id:  parseInt(page_id),
            page_cible_url: page_url,
        }),
    });
    const data = await res.json();
    if (data.status === 'ok') {
        msgEl.textContent = '✅ Redirection enregistrée !';
        setTimeout(() => { dddCloseAllModals(); dddRefresh(); }, 1200);
    } else {
        msgEl.textContent = `❌ Erreur : ${JSON.stringify(data)}`;
    }
}

function esc(s) {
    return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
