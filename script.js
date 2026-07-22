const chat = document.getElementById('chat')
const form = document.getElementById('f')
const qin = document.getElementById('q')
const micBtn = document.getElementById('micBtn')
let recognition = null
let isListening = false

function sendQuickReply(text) {
  if (!qin || !form) return
  qin.value = text
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
}

/** Derniers échanges (max 6 tours user + assistant) envoyés à /ask */
let conversationHistory = []
const CONVERSATION_HISTORY_MAX_MESSAGES = 12

function assistantPlainTextFromJson(json) {
  if (!json || typeof json !== 'object') return ''
  let t = (json.answer || json.summary || json.clarification_prompt || '').trim()
  if (t) return t
  if (json.query_type === 'lines_to_stop') {
    const stop = json.stop_requested || ''
    const rows = Array.isArray(json.results) ? json.results : []
    const nums = rows.map((r) => r && r.number).filter(Boolean).join(', ')
    if (nums) return `Lignes pour « ${stop} » : ${nums}`
  }
  if (json.query_type === 'all_lines_summary') return 'Liste du réseau urbain'
  if (json.query_type === 'line_details' && json.line_details) {
    const L = json.line_details
    return `${L.number} : ${L.start} ↔ ${L.end}`
  }
  return 'Réponse du chatbot'
}

function pushConversationExchange(userText, json) {
  const u = String(userText || '').trim()
  const a = assistantPlainTextFromJson(json)
  conversationHistory.push({ role: 'user', content: u })
  conversationHistory.push({ role: 'assistant', content: a })
  while (conversationHistory.length > CONVERSATION_HISTORY_MAX_MESSAGES) {
    conversationHistory.splice(0, conversationHistory.length - CONVERSATION_HISTORY_MAX_MESSAGES)
  }
}

// Phrases courantes
const bonjour = [
  'bonjour', 'salut', 'bonsoir', 'bonne journée', 'bonne soirée', 
  'bon matin', 'bon après-midi', 'hello', 'hi','coucou'
]

const bye = [
  'au revoir', 'à bientôt', 'bonne nuit', 'bye', 'adieu', 'ciao', 'à plus'
]

const merci = [
  'merci', 'merci beaucoup', 'gracias', 'thank you', 'thanks'
]

const aide = [
  'aide', 'assistance', 'help'
]

const qui = [
 'toi', 'qui es tu', 'qui êtes vous', 'présente toi'
]

// Variable pour stocker la question actuelle
let currentQuestion = ''

/**
 * URL de base du backend Flask.
 * - Prod : définir avant script.js : window.CHATBOT_API_BASE = 'https://…' (sans slash final).
 * - Local XAMPP (page sur :80) : laisser vide → appels vers http://127.0.0.1:5000 (Flask).
 * - Local tout-Flask : ouvrir http://127.0.0.1:5000/ → même origine :5000.
 */
function getChatbotApiBases(suffix) {
  const raw =
    typeof window !== 'undefined' && window.CHATBOT_API_BASE != null
      ? String(window.CHATBOT_API_BASE).trim()
      : ''
  const base = raw.replace(/\/+$/g, '')
  if (base) return [base + suffix]
  // Même origine que la page (ex. chatbot derrière Nginx sur le même host que l’API).
  try {
    if (typeof window !== 'undefined' && window.location && window.location.origin) {
      const origin = window.location.origin
      if (origin && origin !== 'null' && !origin.startsWith('file:')) {
        const host = (window.location.hostname || '').toLowerCase()
        const portStr = window.location.port
        const portNum = portStr
          ? parseInt(portStr, 10)
          : window.location.protocol === 'https:'
            ? 443
            : 80
        const isLoopback = host === 'localhost' || host === '127.0.0.1'
        // XAMPP / Apache sur :80 : l’API Flask est sur :5000 — ne pas utiliser origin seul.
        if (!(isLoopback && portNum !== 5000)) {
          return [origin + suffix]
        }
      }
    }
  } catch (e) {}
  return ['http://127.0.0.1:5000' + suffix, 'http://localhost:5000' + suffix]
}

function escapeHtml(str) {
  return (str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/**
 * URL pour « Plus d'infos » selon le type de réponse et la source.
 * - Villes interurbaines → page officielle réseau interurbain
 * - Lignes / arrêts → page réseau urbain Dakar
 * - Page chatbot → accueil demdikk.sn
 */
function resolveSourceUrl(url, json) {
  const qt = json && json.query_type
  if (qt === 'city_info' || (json && json.is_city_query)) {
    return 'https://demdikk.sn/reseau-interurbain/'
  }
  if (
    qt === 'all_lines_summary' ||
    qt === 'line_details' ||
    qt === 'line_summary_only' ||
    qt === 'lines_to_stop' ||
    (json && json.is_line_query)
  ) {
    return 'https://demdikk.sn/reseau-urbain-dakar/'
  }
  const u = (url || '').trim()
  if (!u) return 'https://demdikk.sn/'
  if (/chatbot-2303/i.test(u)) return 'https://demdikk.sn/'
  if (/reseau-interurbain/i.test(u)) return 'https://demdikk.sn/reseau-interurbain/'
  if (/reseau-urbain-dakar/i.test(u)) return 'https://demdikk.sn/reseau-urbain-dakar/'
  return u
}

/** Bloc meta cliquable « Plus d'infos » vers la page source */
function formatMoreInfoMeta(json) {
  const src = json.sources && json.sources[0]
  const href = resolveSourceUrl(src && src.url, json)
  const safeHref = escapeHtml(href)
  return `<div class="meta meta-more-info"><a href="${safeHref}" target="_blank" rel="noopener noreferrer" class="more-info-link">Plus d'infos</a></div>`
}

/** Affiche le lien seulement si la réponse repose sur un extrait du site (index, lignes, fiche ville…). */
function shouldShowMoreInfoLink(json) {
  if (!json) return false
  if (json.show_more_info === false) return false
  if (json.show_more_info === true) return true

  // Réponses conversationnelles / politesse → pas de bouton "Plus d'infos"
  const ans = (json.answer || '').trim()
  // Bloc "non trouvé" → jamais de bouton Plus d'infos
  if (/^je n['']ai pas trouv[eé]/i.test(ans)) return false
  const CONVERSATIONAL = /^(je vous en prie|avec plaisir|de rien|pas de probl[eè]me|c[''']est un plaisir|enchant[eé]|bienvenue)/i
  if (CONVERSATIONAL.test(ans)) return false
  // Réponse courte sans données factuelles (prix, horaires, lignes, coordonnées...)
  if (
    ans.length < 160 &&
    !/\d{3,}|fcfa|prix|tarif|d[eé]part|terminus|ligne\s+\d|bus\s+n|arr[eê]t|\+221/i.test(ans)
  ) return false

  if (json.is_city_query || json.is_line_query) return true
  if (json.has_structured_data) return true
  const hasResults = json.results && json.results.length > 0
  const hasLinesData = (json.lines_summary && json.lines_summary.length) ||
    (json.lines && json.lines.length) ||
    (json.total_lines != null && json.total_lines > 0)
  if (hasResults || hasLinesData) return true
  const qt = json.query_type || ''
  if (['all_lines_summary', 'line_details', 'line_summary_only', 'lines_to_stop', 'city_info'].includes(qt)) {
    return true
  }
  return false
}

/** Réponse courte sans extrait site : texte simple (pas de cartes / listes décorées par ligne). */
function formatPlainAnswerHtml(text) {
  const t = (text || '').trim()
  if (!t) return ''
  return `<div class="answer-content answer-plain">${escapeHtml(t).replace(/\r?\n/g, '<br>')}</div>`
}

/** Icône « copier » (deux plans superposés) — utilisée dans `.copy-fab-row` sous la réponse. */
const COPY_FAB_SVG =
  '<svg class="copy-fab__svg" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<rect x="8" y="8" width="12" height="12" rx="2"/>' +
  '<rect x="4" y="4" width="12" height="12" rx="2"/>' +
  '</svg>'

/**
 * Copie du texte dans le presse-papier — fonctionne même sans HTTPS.
 * Utilise clipboard API moderne avec fallback execCommand.
 * @param {string} text - Texte à copier
 * @param {HTMLElement} btn - Bouton à mettre à jour visuellement
 */
function robustCopy(text, btn) {
  const feedback = () => {
    if (!btn) return
    if (btn.classList && btn.classList.contains('copy-fab')) {
      btn.classList.add('copy-fab--ok')
      btn.setAttribute('aria-label', 'Copié')
      setTimeout(() => {
        btn.classList.remove('copy-fab--ok')
        btn.setAttribute('aria-label', 'Copier')
      }, 2000)
      return
    }
    const original = btn.innerHTML
    btn.innerHTML = '<span>✓</span> Copié'
    setTimeout(() => { btn.innerHTML = original }, 2000)
  }
  const fallback = () => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    try { document.execCommand('copy') } catch (e) { /* silencieux */ }
    document.body.removeChild(ta)
    feedback()
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(feedback).catch(fallback)
  } else {
    fallback()
  }
}

function copyToClipboardFallback(text) {
  robustCopy(text, null)
}

function normalizeFr(text) {
  return (text || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^\p{L}\p{N}\s]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function isVagueQuestion(q) {
  const norm = normalizeFr(q)
  if (!norm) return true
  // Pays / marque liés au réseau interurbain : on envoie une question ciblée (pas de menu)
  if (norm === 'senegal' || norm === 'gambie' || norm === 'gambia'
      || norm === 'afrique dem dikk' || norm === 'afrique demdikk'
      || norm === 'senegal dem dikk' || norm === 'sengal dem dikk') return false
  const words = norm.split(' ').filter(Boolean)
  if (words.length <= 1) {
    // Un seul mot : envoyer au backend par défaut (villes, « horaires », « touba », etc.).
    // Menu seulement pour des termes vraiment ambigus sans intention.
    const ambiguousSingles = new Set([
      'dakar', 'ddd', 'demdikk', 'dem', 'dikk',
      'info', 'informations',
      'transport', 'bus', 'voyage', 'voyager'
    ])
    return ambiguousSingles.has(norm)
  }
  if (words.length === 2 && (norm === 'dakar dem' || norm === 'dakar demdikk')) return true
  // "dem dikk" seul → question de présentation, envoyer au backend
  if (norm === 'dem dikk' || norm === 'dakar dem dikk') return false
  const vague = new Set([
    'dakar', 'ddd', 'demdikk', 'dem dikk',
    'voyager', 'voyage', 'transport', 'bus', 'application', 'appli', 'info', 'informations', 'aide'
  ])
  if (vague.has(norm)) return true
  if (norm === 'je veux voyager' || norm === 'je veux voyager comment faire' || norm === 'comment faire') return true

  // Requêtes "marque/pays" sans intention (ex: "senegal dem dikk", "dakar dem dikk")
  const intentWords = [
    'horaire', 'horaires', 'prix', 'tarif', 'tarifs', 'reservation', 'réservation', 'reserver', 'réserver',
    'billet', 'ticket', 'ligne', 'lignes', 'abonnement', 'abonnements', 'tek', 'colis', 'messagerie', 'contact',
    'adresse', 'localisation', 'localise', 'situe', 'trouve', 'siege', 'bureau',
    'emploi', 'recrutement', 'postuler', 'stage',
    'afrique', 'gambie', 'banjul', 'international'
  ]
  const hasIntent = intentWords.some(w => norm.includes(w))
  if (!hasIntent) {
    const brandWords = ['dakar', 'senegal', 'dem', 'dikk', 'demdikk', 'ddd']
    const brandCount = words.filter(w => brandWords.includes(w)).length
    // si la majorité des mots sont juste la marque/pays → vague
    if (brandCount >= Math.max(2, Math.ceil(words.length * 0.6))) return true
  }
  return false
}

function formatInterurbanCityLabel(slug) {
  const s = (slug || '').trim()
  if (!s) return ''
  return s
    .split(/[-\s]+/)
    .filter(Boolean)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(' ')
}

/**
 * Remplace le contenu de la bulle par la grille des destinations interurbaines (/cities).
 */
async function showInterurbanDestinationPicker(bubble) {
  if (!bubble) return
  bubble.innerHTML = `<div class="clarification-box"><div class="loading">Chargement des destinations…</div></div>`
  let cities = await ensureCitiesLoaded()
  if (!cities.length) {
    cities = [
      'fatick', 'thies', 'touba', 'saint-louis', 'mbour', 'kaolack', 'ziguinchor',
      'louga', 'podor', 'tambacounda', 'kedougou', 'bakel', 'kidira',
    ]
  }
  const uniq = [...new Set(cities.map((c) => String(c).trim()).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b, 'fr', { sensitivity: 'base' }),
  )
  const chips = uniq
    .map(
      (slug) =>
        `<button type="button" class="interurbain-city-chip" data-city="${escapeHtml(slug)}">${escapeHtml(
          formatInterurbanCityLabel(slug),
        )}</button>`,
    )
    .join('')
  bubble.innerHTML = `
    <div class="clarification-box interurbain-picker">
      <strong>🚌 Destinations interurbaines</strong>
      <p class="interurbain-picker-hint">Choisissez une ville pour afficher horaires, tarifs et contacts (réseau Sénégal Dem Dikk).</p>
      <div class="interurbain-city-grid">${chips}</div>
    </div>
  `
  bubble.querySelectorAll('.interurbain-city-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      const city = chip.getAttribute('data-city') || ''
      if (!city) return
      qin.value = city
      form.requestSubmit()
    })
  })
}

function isInterurbanOverviewQuery(text) {
  const n = normalizeFr(text || '')
  if (n.length < 12) return false
  const hasInter = n.includes('interurbain')
  const hasBrand = n.includes('senegal dem dikk') || n.includes('sénégal dem dikk')
  if (!hasInter || !hasBrand) return false
  return (
    n.includes('horaire') ||
    n.includes('prix') ||
    n.includes('tarif') ||
    n.includes('depart') ||
    n.includes('reservation') ||
    n.includes('point')
  )
}

function renderClarificationMenu(originalQuestion, placeholder, whyText, options) {
  const q = (originalQuestion || '').trim()
  const safeWhy = escapeHtml(whyText || "Votre question est un peu large. Que cherchez-vous exactement ?")
  const safeQ = encodeURIComponent(q)
  const opts = Array.isArray(options) ? options : []
  const buttonsHtml = opts
    .map((o) => {
      const menu = o.menu ? ` data-menu="${escapeHtml(o.menu)}"` : ''
      const lab = escapeHtml(o.label || 'Choisir')
      return `<button type="button" class="small-btn" data-action="clarify-pick"${menu} data-q="${safeQ}" data-next="${encodeURIComponent(
        o.next || '',
      )}"><span class="choice-label">${lab}</span></button>`
    })
    .join('')
  placeholder.querySelector('.bubble').innerHTML = `
    <div class="clarification-box">
      <strong>🤔 ${safeWhy}</strong>
      <div class="clarification-box-actions">
        ${buttonsHtml || `<button type="button" class="small-btn" data-action="clarify-pick" data-q="${safeQ}" data-next="${encodeURIComponent('Contact service client Dakar Dem Dikk')}"><span class="choice-label">Contact</span></button>`}
      </div>
    </div>
  `

  const bubble = placeholder.querySelector('.bubble')
  bubble.querySelectorAll('button[data-action="clarify-pick"]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (btn.getAttribute('data-menu') === 'interurban') {
        await showInterurbanDestinationPicker(bubble)
        return
      }
      const nextQ = decodeURIComponent(btn.dataset.next || '') || decodeURIComponent(btn.dataset.q || '')
      qin.value = nextQ
      form.requestSubmit()
    })
  })
}

async function getAvailableCities() {
  const apiCandidates = getChatbotApiBases('/cities')
  for (const url of apiCandidates) {
    try {
      const r = await fetch(url, { method: 'GET' })
      if (!r.ok) continue
      const j = await r.json()
      if (j && Array.isArray(j.cities)) return j.cities
    } catch (e) {}
  }
  return []
}

let _citiesCache = null
async function ensureCitiesLoaded() {
  if (Array.isArray(_citiesCache)) return _citiesCache
  _citiesCache = await getAvailableCities()
  return _citiesCache
}

function detectCityInQuestion(q, cities) {
  const norm = normalizeFr(q)
  if (!norm || !Array.isArray(cities) || !cities.length) return null
  const set = new Set(cities.map(c => normalizeFr(c)))
  // match direct city
  for (const c of set) {
    if (c && norm.includes(c)) return c
  }
  return null
}

function levenshtein(a, b) {
  a = a || ''
  b = b || ''
  if (a === b) return 0
  const alen = a.length
  const blen = b.length
  if (!alen) return blen
  if (!blen) return alen
  const v0 = new Array(blen + 1)
  const v1 = new Array(blen + 1)
  for (let i = 0; i <= blen; i++) v0[i] = i
  for (let i = 0; i < alen; i++) {
    v1[0] = i + 1
    for (let j = 0; j < blen; j++) {
      const cost = a[i] === b[j] ? 0 : 1
      v1[j + 1] = Math.min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost)
    }
    for (let j = 0; j <= blen; j++) v0[j] = v1[j]
  }
  return v0[blen]
}

function suggestClosestCities(qNorm, cities, limit = 6) {
  if (!qNorm || !Array.isArray(cities) || !cities.length) return []
  const q = normalizeFr(qNorm)
  if (!q) return []
  const scored = cities.map((c) => {
    const cn = normalizeFr(c)
    const dist = levenshtein(q, cn)
    const rel = dist / Math.max(1, Math.max(q.length, cn.length))
    return { city: c, rel }
  })
  scored.sort((a, b) => a.rel - b.rel)
  return scored.slice(0, limit).map(s => s.city)
}

async function buildDynamicClarification(q, whyText) {
  const norm = normalizeFr(q)
  const cities = await ensureCitiesLoaded()
  const foundCity = detectCityInQuestion(q, cities)

  const outside = new Set(['senegal', 'gambie', 'gambia', 'mali', 'mauritanie', 'guinee', 'guinée'])
  const isOutside = outside.has(norm)

  const opts = []

  // Si c’est un pays/terme hors périmètre → proposer des exemples concrets sur villes couvertes
  if (isOutside && cities.length) {
    // Pour des pays (ex: Sénégal / Gambie), orienter vers le service interurbain (Sénégal Dem Dikk)
    opts.push({
      label: 'Sénégal Dem Dikk (interurbain)',
      next: 'Réseau Sénégal Dem Dikk (interurbain) : horaires, points de départ et réservation',
      menu: 'interurban',
    })
    opts.push({ label: 'Prix (interurbain)', next: 'Tarifs du réseau Sénégal Dem Dikk (interurbain)' })
    opts.push({ label: 'Réserver (interurbain)', next: 'Comment réserver un billet sur le réseau Sénégal Dem Dikk (interurbain) ?' })
    opts.push({ label: 'Réseau urbain (lignes Dakar)', next: 'Liste des lignes urbaines Dakar' })
    opts.push({ label: 'Contact', next: 'Contact service client Dakar Dem Dikk' })
    return { why: "Ce sujet est très large. Voulez-vous parler du réseau interurbain (Sénégal Dem Dikk) ou du réseau urbain de Dakar ?", options: opts }
  }

  // Mots-clés → menu “contextuel”
  const hasLine = /\bligne(s)?\b/.test(norm)
  const hasHoraire = norm.includes('horaire') || norm.includes('horaires')
  const hasPrix = norm.includes('prix') || norm.includes('tarif') || norm.includes('tarifs')
  const hasReserve = norm.includes('reserver') || norm.includes('réserver') || norm.includes('reservation') || norm.includes('réservation') || norm.includes('billet') || norm.includes('ticket')
  const hasAbn = norm.includes('abonnement') || norm.includes('abonnements') || norm.includes('tek dem') || norm.includes('carte') || norm.includes('pass')
  const hasColis = norm.includes('colis') || norm.includes('messagerie') || norm.includes('courrier')

  if (hasLine) {
    opts.push({ label: 'Réseau urbain : liste des lignes', next: 'Liste des lignes urbaines Dakar' })
    opts.push({ label: 'Détails d’une ligne (ex: “ligne 1”)', next: 'Ligne 1' })
  }
  if (hasHoraire || foundCity) {
    opts.push({ label: foundCity ? `Horaires Dakar–${foundCity}` : 'Horaires (Dakar–ville)', next: foundCity ? `Horaires Dakar–${foundCity}` : 'Horaires Dakar–Thiès' })
  }
  if (hasPrix || foundCity) {
    opts.push({ label: foundCity ? `Prix pour ${foundCity}` : 'Prix (pour une destination)', next: foundCity ? `Prix pour ${foundCity}` : 'Prix pour Touba' })
  }
  if (hasReserve) opts.push({ label: 'Réserver / acheter', next: 'Comment réserver un billet ?' })
  if (hasAbn) opts.push({ label: 'Abonnement / Tek Dem', next: 'Abonnement mensuel et carte Tek Dem' })
  if (hasColis) opts.push({ label: 'Colis / Messagerie', next: 'Service Messagerie Express (colis)' })

  // Si ça ressemble à un lieu (1-2 mots) mais pas une ville couverte → suggérer des villes proches
  const words = norm.split(' ').filter(Boolean)
  const looksLikePlace = (words.length <= 2) && !foundCity
  if (looksLikePlace && cities.length) {
    const close2 = suggestClosestCities(norm, cities, 5)
    for (const c of close2) {
      opts.push({ label: `Horaires Dakar–${c}`, next: `Horaires Dakar–${c}` })
    }
  }

  // Si on n’a rien d’évident → menu “général”, mais seulement quand vague
  if (!opts.length) {
    opts.push({ label: 'Réseau urbain (lignes Dakar)', next: 'Liste des lignes urbaines Dakar' })
    opts.push({
      label: 'Voyage interurbain (horaires/prix)',
      next: 'Horaires, points de départ et prix du réseau Sénégal Dem Dikk (interurbain)',
      menu: 'interurban',
    })
    opts.push({ label: 'Réserver / acheter', next: 'Comment réserver un billet ?' })
    opts.push({ label: 'Contact', next: 'Contact service client Dakar Dem Dikk' })
  }

  // Dédupliquer par label
  const seen = new Set()
  const dedup = opts.filter(o => (o && o.label && !seen.has(o.label) && seen.add(o.label)))
  return { why: whyText, options: dedup }
}

// Note: ancien mode modal supprimé à la demande :
// "Plus de détails" doit étendre la réponse dans la bulle.

/**
 * Détecte les lignes typiques de la page d'accueil DDD :
 *  - compteurs / odomètres : "00", "00 +", "00 %", "+", chiffres seuls
 *  - libellés KPI isolés : "Voyageurs annuel", "Destinations", "Clients satisfaits"
 *  - titres de cartes de service homepage : "Régie publicitaire", "Expédition de courriers",
 *    "Prestations mécaniques", "Abonnement" (quand seul), etc.
 */
function looksLikeHomepageNoise(line) {
  const s = (line || '').trim()
  if (!s) return true
  if (/^[0-9]{1,3}\s*[+%]?$/.test(s)) return true
  if (/^[+%]$/.test(s)) return true
  if (
    /^(voyageurs?\s+annuel|destinations?|clients?\s+satisfaits?|ann[ée]es?\s+d.exp[ée]rience)\s*$/i.test(
      s
    )
  )
    return true
  return false
}

/**
 * Ligne type mots-clés Yoast / meta (ex: "DemDikk Ligne01 TransportUrbain …")
 * sans connecteurs français usuels — à retirer de l'affichage.
 */
function looksLikeSeoKeywordLine(line) {
  const s = (line || '').trim()
  if (s.length < 55) return false
  const words = s.split(/\s+/).filter(Boolean)
  if (words.length < 8) return false
  if (/👉|[:«»]/.test(s)) return false
  if (/\d+\s*h\d*|\b(premier|dernier|départ|terminus|depuis)\b/i.test(s)) return false
  if (
    /\b(le|la|les|l'|de|des|du|d'|et|à|a|un|une|pour|avec|depuis|dans|sur|est|sans|plus|très|vous|nous|qui|chez|aux|son|leur|cette|ces|ont|sont|sera)\b/i.test(
      s
    )
  )
    return false
  const sluggy = words.filter(
    (w) => w.length > 11 || /[a-zà-ÿ][A-ZÀ-Ÿ]/.test(w) || /^[A-Za-zÀ-ÖØ-öø-ÿ]+\d+$/i.test(w)
  ).length
  return sluggy >= 5
}

/**
 * Retire le bloc de navigation/en-tête du site qui pollue les réponses.
 * Ex: "agent-ia – Dakar Dem Dikk Contactez-nous au: ... Plus de détails"
 */
function stripNavContent(text) {
  if (!text) return ''
  // Nettoyer les titres markdown ## / ### / #### avant tout
  let t = text.replace(/#{1,6}\s*\d*\.?\s*/g, '')
  // Enlever le bloc "slug – Dakar Dem Dikk ... Home slug"
  t = t.replace(/^[a-z0-9\-]+ \u2013 Dakar Dem Dikk\b[\s\S]*?(?:Home\s+[a-z0-9\-]+\s*)/i, '')
  // Enlever "Contactez-nous au: ... Plus de détails"
  t = t.replace(/Contactez-nous au\s*:[\s\S]*?(?:Plus de d\u00e9tails|Offres d.emplois)[^\n]*/gi, '')
  // Enlever les liens du menu
  t = t.replace(/\b(?:Accueil|Offre transport|Info voyageurs|Pr\u00e9sentation|Offres d.emplois)\b/g, '')
  // Enlever uniquement les URLs de navigation interne DDD (pages /chatbot, /reseau-interurbain…)
  t = t.replace(/https?:\/\/[^\s]*demdikk\.sn\/(?:chatbot|reseau|presentation|actualites|offres)[^\s]*/gi, '')
  // Lignes « widgets » / menu WordPress souvent collées au contenu (affichées comme cartes si on ne les enlève pas)
  const lines = t.split(/\r?\n/).map((raw) => {
    const line = (raw || '').trim()
    // Puces WordPress / tirets Unicode (– — - •) + espaces insécables
    const unbullet = line.replace(/^[\s\u00A0]*[–—\-•▸]\s*/, '').trim()
    if (/^plus\s+de\s+d[ée]tails\b/i.test(unbullet)) return ''
    if (/^alerte\s+info\b/i.test(unbullet)) return ''
    // Même sans puce en tête de ligne (texte collé sur une seule ligne)
    if (/^plus\s+de\s+d[ée]tails\b/i.test(line)) return ''
    if (/^alerte\s+info\b/i.test(line)) return ''
    if (/^info\s+voyageur\s*\|\s*/i.test(unbullet)) {
      return unbullet.replace(/^info\s+voyageur\s*\|\s*/i, '').trim()
    }
    if (looksLikeHomepageNoise(unbullet)) return ''
    // Description tronquée des cartes home (« … » U+2026 ou « ... » ASCII)
    if ((/[…]$/.test(unbullet) || /\.\.\.$/.test(unbullet)) && unbullet.length < 90) return ''
    if (looksLikeSeoKeywordLine(unbullet) || looksLikeSeoKeywordLine(line)) return ''
    return raw.trimEnd()
  })
  t = lines.filter((x) => (x || '').trim().length > 0).join('\n')
  // Nettoyer espaces multiples
  t = t.replace(/\n{3,}/g, '\n\n').replace(/[ \t]+/g, ' ')
  return t.trim()
}

function stripMarkdownHeadings(text) {
  // Enlever les ## et ### (titres markdown) et les numéros de section ex: "## 8. "
  return (text || '').replace(/#{1,6}\s*\d*\.?\s*/g, '').trim()
}

/**
 * Convertit les URLs brutes en liens <a> cliquables.
 * Doit être appelé APRÈS escapeHtml (les & sont déjà &amp; mais c'est OK dans href).
 */
function linkifyText(html) {
  return html.replace(/https?:\/\/[^\s<>"']+/g, function (url) {
    // Nettoyer la ponctuation finale parasite
    url = url.replace(/[.,;:!?\)\]>]+$/, '')
    // Afficher seulement le domaine (ex: play.google.com)
    const domain = url.replace(/^https?:\/\//, '').replace(/\/.*$/, '')
    return `<a href="${url}" target="_blank" rel="noopener noreferrer" class="answer-link">${domain} ↗</a>`
  })
}

/**
 * Regroupe « Fonctions : » + les puces suivantes en une seule ligne (virgules).
 * Évite une carte par fonction dans l’UI.
 */
function collapseFonctionsListLines(lines) {
  const result = []
  let i = 0
  const bulletRe = /^([–•\-▸])\s*(.+)$/
  while (i < lines.length) {
    const line = lines[i]
    const head = line.match(/^([–•\-▸])\s*Fonctions\s*:\s*(.*)$/i)
    if (head) {
      const mark = head[1]
      let first = (head[2] || '').trim().replace(/,\s*$/, '')
      const parts = []
      if (first) parts.push(first)
      let j = i + 1
      while (j < lines.length) {
        const sub = lines[j].match(bulletRe)
        if (!sub) break
        const t = sub[2].trim()
        if (/^(Fonctions|Programme|Télécharger|Google\s*Play|App\s*Store)\s*:/i.test(t)) break
        parts.push(t.replace(/,\s*$/, ''))
        j++
      }
      if (parts.length) {
        result.push(`${mark} Fonctions : ${parts.join(', ')}`)
        i = j
        continue
      }
    }
    result.push(line)
    i++
  }
  return result
}

/**
 * Réponse backend encore structurée (titres ###, puces ▸/–, titre **…**)
 * alors que results[] est vide : il faut quand même l’affichage « en blocs », pas le mode paragraphe.
 */
function answerHasStructuredBlockMarkers(text) {
  const t = (text || '').trim()
  if (!t) return false
  if (/^###\s/m.test(t) || /\n###\s/.test(t)) return true
  if (/^[–•\-▸]\s/m.test(t) || /\n[–•\-▸]\s/.test(t)) return true
  if (/^\*\*.+\*\*\s*$/m.test(t) || /^\*\*.+\*\*\s*\n/m.test(t)) return true
  return false
}

/**
 * Évite de traiter une phrase marketing / une ligne de prose comme un « titre » (bloc city-header).
 * L’heuristique historique (ligne courte commençant par une majuscule) capturait par ex.
 * « Plus de détails », « Alerte info », « Info voyageur | … ».
 */
function looksLikeProseNotSectionTitle(line) {
  const s = (line || '').trim()
  if (!s) return true
  if (/\|/.test(s)) return true
  if (/->|→/.test(s)) return true
  if (/^plus\s+de\s+d[ée]tails\b/i.test(s)) return true
  if (/^alerte\s+info\b/i.test(s)) return true
  if (/^info\s+voyageur\b/i.test(s)) return true
  if (/^application\b/i.test(s)) return true
  // Phrase complète plutôt qu’un intitulé court
  if (/[.!?]$/.test(s) && s.length > 28) return true
  const lower = (s.match(/[a-zàâäéèêëïîôùûüç]/g) || []).length
  const upper = (s.match(/[A-ZÀÂÄÉÈÊËÏÎÔÙÛÜÇ]/g) || []).length
  if (s.length > 45 && lower > Math.max(upper * 2, 8)) return true
  return false
}

/** Ajoute « … voir plus » à la fin du dernier bloc de texte (même ligne visuelle). */
function appendInlineSeeMore(html, encodedDetails) {
  const link =
    `<span class="answer-ellipsis">…</span> <button type="button" class="see-more-link" data-action="show-full-content" data-full="${encodedDetails}">voir plus</button>`
  if (/<\/p>\s*$/i.test(html)) {
    return html.replace(/<\/p>\s*$/i, `${link}</p>`)
  }
  if (/<\/div>\s*$/i.test(html)) {
    return html.replace(/<\/div>\s*$/i, `${link}</div>`)
  }
  return `${html}${link}`
}

function formatResponseText(text) {
  if (!text || !text.trim()) return ''

  // Nettoyer seulement les ## de section (## 8. → '') mais garder ###
  const cleaned = (text || '').replace(/#{1,2}\s*\d+\.\s*/g, '').trim()
  const safe = escapeHtml(cleaned)
  let lines = safe.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
  lines = collapseFonctionsListLines(lines)

  let html = ''
  let inList = false

  for (const line of lines) {
    // Titre principal **Texte** (entier en gras)
    const titleMatch = line.match(/^\*\*(.+?)\*\*$/)
    if (titleMatch) {
      if (inList) { html += '</div>'; inList = false }
      html += `<div class="city-header">${titleMatch[1]}</div>`
      continue
    }

    // Titre de section numéroté "17. Contact et assistance humaine" ou "Service AIBD (...)"
    // → pas de bullet, pas de ###, court, ressemble à un titre
    const sectionTitleMatch = line.match(/^(?:\d+\.\s+)?([A-ZÀÂÉÈÊËÎÏÔÙÛÜÇ][^–•\-\n]{4,70})$/)
    if (
      sectionTitleMatch
      && !line.match(/^[–•\-]/)
      && !line.match(/^###/)
      && !looksLikeProseNotSectionTitle(line)
    ) {
      const t = sectionTitleMatch[0].replace(/^\d+\.\s+/, '').trim()
      if (t.length > 5 && t.length < 80 && !t.match(/^(En |A |Dans |Pour |Par |Via |Deux |Des |Un |Une |La |Le |Les |Ou )/i)) {
        if (inList) { html += '</div>'; inList = false }
        html += `<div class="city-header">${escapeHtml(t)}</div>`
        continue
      }
    }

    // Sous-titre ### Texte → section-title (bloc visuel)
    const h3Match = line.match(/^###\s*(.+)$/)
    if (h3Match) {
      if (inList) { html += '</div>'; inList = false }
      html += `<div class="section-title" style="margin-top:.8rem;">${h3Match[1].replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')}</div>`
      continue
    }

    // Tiret – ou • ou - ou ▸ → item de liste
    const bulletMatch = line.match(/^[–•\-▸]\s*(.+)$/)
    if (bulletMatch) {
      if (!inList) { html += '<div class="list-container">'; inList = true }
      const bText = linkifyText(bulletMatch[1].replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'))
      html += `<div class="list-item">▸ ${bText}</div>`
      continue
    }

    if (inList) { html += '</div>'; inList = false }

    const para = linkifyText(line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'))
    html += `<p>${para}</p>`
  }

  if (inList) html += '</div>'

  return html || `<div class="preview">${safe.replace(/\n/g, '<br>')}</div>`
}
function askForLineDetails(lineNumber) {
    const qin = document.getElementById('q');
    if (!qin) return;
    
    // Nettoyer le numéro de ligne (enlever "LIGNE " si présent)
    // Garder le format complet (ex: 502A, 16A, etc.)
    let cleanNumber = lineNumber ? lineNumber.replace(/^LIGNE\s+/i, '').trim() : '';
    
    // Si c'est vide ou invalide, essayer d'extraire depuis le contexte (bouton cliqué)
    if (!cleanNumber || cleanNumber === '') {
        // Essayer de trouver le numéro dans le bouton ou l'élément parent
        const event = window.event || (arguments.length > 1 ? arguments[1] : null);
        if (event && event.target) {
            const btn = event.target.closest('.line-details-btn');
            if (btn) {
                // D'abord essayer l'attribut data-linenum du bouton
                const btnLineNum = btn.getAttribute('data-linenum');
                if (btnLineNum) {
                    cleanNumber = btnLineNum;
                } else {
                    // Sinon, chercher dans l'élément parent
                    const lineItem = btn.closest('.line-item');
                    if (lineItem) {
                        const lineNumAttr = lineItem.getAttribute('data-linenumber');
                        if (lineNumAttr) {
                            cleanNumber = lineNumAttr;
                        }
                    }
                }
            }
        }
    }
    
    if (cleanNumber && cleanNumber !== '') {
        qin.value = `ligne ${cleanNumber}`;
        
        // Déclencher la soumission du formulaire
        const form = document.getElementById('f');
        if (form) {
            form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        }
    } else {
        console.error('Impossible de déterminer le numéro de ligne pour:', lineNumber);
    }
}

function showAllLines() {
    const qin = document.getElementById('q');
    if (!qin) return;
    qin.value = 'toutes les lignes';
    document.getElementById('f').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

// Fonction pour obtenir l'emoji de catégorie
function getCategoryEmoji(category) {
    const emojis = {
        'ter': '🚆',
        'urbaine': '🏙️',
        'banlieue': '🏘️',
        'taf': '🛒'
    };
    return emojis[category] || '🚍';
}
function getLineEmoji(line) {
    if (!line) return '🚌';
    
    const lineLower = line.toLowerCase();
    if (lineLower.includes('ter') || lineLower.includes('gare')) return '🚆';
    if (lineLower.includes('aéroport') || lineLower.includes('aibd')) return '✈️';
    if (lineLower.includes('ucad') || lineLower.includes('université')) return '🎓';
    if (lineLower.includes('marché') || lineLower.includes('commercial')) return '🛒';
    if (lineLower.includes('hôpital') || lineLower.includes('santé')) return '🏥';
    if (lineLower.includes('administration') || lineLower.includes('ministère')) return '🏛️';
    if (lineLower.includes('plage') || lineLower.includes('mer')) return '🏖️';
    return '🚌';
}
  // Initialiser le chat
window.onload = function() {
  setTimeout(() => {
    append('bot', `
      <div style="margin-bottom: 1rem;">
        <div class="city-header" style="font-size: 1.3em; margin-bottom: 1rem;">
          <span style="font-size: 1.5em; display: inline-block; animation: wave 1s ease-in-out infinite;">👋</span>
          <span style="margin-left: 0.5rem;">Bonjour !</span>
        </div>
        <p style="font-size: 1.05em; line-height: 1.7;"> Je suis Maï, l'assistante de Dakar Dem Dikk 😊 Pose-moi tes questions sur le transport, les horaires ou les voyages.</p>
         </div>
      <p style="margin-top: 1.5rem; font-size: 1.1em;"><strong style="color: var(--bot);">Comment puis-je vous aider aujourd'hui ?</strong></p>
    `)
  }, 300)
}

function append(role, html){
  const wrapper = document.createElement('div')
  wrapper.className = role + ' clearfix'
  wrapper.style.opacity = '0'
  wrapper.style.transform = 'translateY(10px)'
  
  const b = document.createElement('div')
  b.className = 'bubble'
  b.innerHTML = html
  wrapper.appendChild(b)
  chat.appendChild(wrapper)
  
  // Animation d'apparition
  setTimeout(() => {
    wrapper.style.transition = 'all 0.3s ease-out'
    wrapper.style.opacity = '1'
    wrapper.style.transform = 'translateY(0)'
  }, 10)
  
  chat.scrollTop = chat.scrollHeight
  return wrapper
}

function setMicListeningUI(active) {
  if (!micBtn) return
  micBtn.classList.toggle('listening', !!active)
  micBtn.title = active ? "Arrêter la dictée vocale" : "Dicter votre message"
}

function setupVoiceInput() {
  if (!micBtn) return
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!SpeechRecognition) {
    micBtn.disabled = true
    micBtn.title = "Micro non supporté sur ce navigateur"
    return
  }

  recognition = new SpeechRecognition()
  recognition.lang = 'fr-FR'
  recognition.continuous = true
  recognition.interimResults = true

  recognition.onstart = () => {
    isListening = true
    setMicListeningUI(true)
  }

  recognition.onresult = (event) => {
    let finalText = ''
    let interimText = ''
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const txt = event.results[i][0].transcript || ''
      if (event.results[i].isFinal) finalText += txt + ' '
      else interimText += txt
    }
    const current = qin.value.trim()
    const committed = current.replace(/\s*\[[^\]]*\]\s*$/, '').trim()
    const nextBase = finalText ? (committed ? `${committed} ${finalText.trim()}` : finalText.trim()) : committed
    qin.value = interimText ? `${nextBase} [${interimText}]`.trim() : nextBase
  }

  recognition.onerror = () => {
    isListening = false
    setMicListeningUI(false)
    qin.value = qin.value.replace(/\s*\[[^\]]*\]\s*$/, '').trim()
  }

  recognition.onend = () => {
    isListening = false
    setMicListeningUI(false)
    qin.value = qin.value.replace(/\s*\[[^\]]*\]\s*$/, '').trim()
  }

  micBtn.addEventListener('click', () => {
    if (!recognition) return
    if (isListening) recognition.stop()
    else {
      qin.focus()
      recognition.start()
    }
  })
}

setupVoiceInput()

function expandShortQuery(q) {
  const raw = (q || '').trim()
  if (!raw) return raw
  const norm = raw
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^\p{L}\p{N}\s]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  // Normaliser les variantes fréquentes (pluriels, typos)
  let normFixed = norm
    .replace(/\bsengal\b/g, 'senegal')
    .replace(/\bcontacts\b/g, 'contact')
    .replace(/\bservices\b/g, 'service')
    .replace(/\bagences\b/g, 'agence')
    .replace(/\bhoraires\b/g, 'horaire')
    .replace(/\blignes\b(?!\s+\d)/g, 'lignes')  // garder "lignes" seul → liste
    .replace(/\btarifs\b/g, 'tarif')
    .replace(/\babonnements\b/g, 'abonnement')
    .replace(/\bremboursements\b/g, 'remboursement')
    .replace(/\bannulations\b/g, 'annulation')
    .replace(/\btekk\s+dem\b/g, 'tek dem')
    .replace(/\baeroport\b/g, 'aibd')

  // Requêtes "lignes" : ne jamais ajouter de bruit, sinon le backend extrait mal le numéro.
  // Exemples : "ligne", "lignes", "ligne 1", "LIGNE 502A"
  if (normFixed === 'ligne' || normFixed === 'lignes') return 'ligne'
  if (/^ligne\s+\d+[a-z&]*$/.test(normFixed)) return raw

  const afQ = "Afrique Dem Dikk : destinations (ex: Gambie/Banjul), horaires, points de départ, réservation et tarifs"
  if (normFixed === 'senegal' || normFixed === 'gambie' || normFixed === 'gambia') return afQ
  const sddQ = 'Réseau Sénégal Dem Dikk (interurbain) : horaires, points de départ et réservation'
  if (normFixed === 'senegal dem dikk' || normFixed.includes('senegal dem dikk')) return sddQ

  const words = normFixed.split(' ').filter(Boolean)
  if (words.length > 2) return raw

  const key = words.join(' ')
  const map = {
    'abonnement': "Comment faire un abonnement mensuel avec Dakar Dem Dikk ?",
    'abonnements': "Quels sont les abonnements mensuels Dakar Dem Dikk (étudiant, jeune actif, etc.) ?",
    'application': "Application Dem Dikk : comment télécharger (Google Play / App Store) et quelles fonctionnalités ?",
    'appli': "Application Dem Dikk : comment télécharger (Google Play / App Store) et quelles fonctionnalités ?",
    'carte': "Comment obtenir et recharger la carte Tek Dem Dakar Dem Dikk ?",
    'tek dem': "Comment obtenir et recharger la carte Tek Dem Dakar Dem Dikk ?",
    'ticket': "Comment acheter ou réserver un ticket Dakar Dem Dikk ?",
    'reservation': "Comment réserver un billet Dakar Dem Dikk ?",
    'réservation': "Comment réserver un billet Dakar Dem Dikk ?",
    'colis': "Comment fonctionne le service messagerie express (colis) Dakar Dem Dikk ?",
    'messagerie': "Comment fonctionne le service messagerie express (colis) Dakar Dem Dikk ?",
    'prix': "Quels sont les prix des tickets Dakar Dem Dikk ?",
    'tarif': "Quels sont les tarifs Dakar Dem Dikk ?"
  }
  if (map[key]) return map[key]

  // Renvoyer tel quel : le backend gère arrêts, villes, lignes, etc.
  return raw
}

form.addEventListener('submit', async (e)=>{
  e.preventDefault()
  const q = qin.value.trim()
  if(!q) return
  const qExpanded = expandShortQuery(q)
  
  // Stocker la question actuelle
  currentQuestion = qExpanded.toLowerCase()
  
  // Vérifier les phrases courantes
  const firstWordRaw = qExpanded.split(' ')[0].toLowerCase()
  const firstWord = firstWordRaw
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '')
  
  if(bonjour.includes(firstWord)) {
    append('user', q)
    qin.value = ''
    append('bot', `<div class="city-header">👋 Bonjour !</div><p>Comment puis-je vous aider avec vos déplacements aujourd'hui ?</p>`)
    return
  }
  
  if(bye.includes(firstWord)) {
    append('user', q)
    qin.value = ''
    append('bot', `<div class="city-header">👋 Au revoir !</div><p>Bonne journée et bon voyage avec Dakar Dem Dikk ! </p>`)
    return
  }
  
  if(merci.includes(firstWord)) {
    append('user', q)
    qin.value = ''
    append('bot', `<p>😊 <strong>Je vous en prie !</strong> N'hésitez pas si vous avez d'autres questions.</p>`)
    return
  }
  
  if(aide.includes(firstWord)) {
    append('user', q)
    qin.value = ''
    append('bot', `<div class="city-header">🔍 Aide</div>
      <p>Je peux vous aider avec :</p>
      <div class="list-item">✅ Horaires des bus pour toutes les villes</div>
      <div class="list-item">✅ Prix des trajets</div>
      <div class="list-item">✅ Contacts et informations pratiques</div>
      <div class="list-item">✅ Itinéraires et conditions de voyage</div>
      <p><strong>Posez-moi une question précise !</strong></p>`)
    return
  }
  
  if(qui.includes(firstWord)) {
    append('user', q)
    qin.value = ''
    append('bot', `<div class="city-header">🤖 À propos</div>
      <p>Je suis l'<strong>assistant virtuel intelligent</strong> de <strong>Dakar Dem Dikk</strong>, le réseau de bus rapide de Dakar.</p>
      <p>Je peux vous renseigner sur :</p>
      <div class="list-item">🎯 Les voyages partout au Sénégal</div>
      <div class="list-item">🎯 Les horaires et prix actualisés</div>
      <div class="list-item">🎯 Les contacts et services</div>
      <div class="list-item">🎯 Les informations pratiques</div>`)
    return
  }
  
  // Envoyer la question (afficher ce que l'utilisateur a tapé)
  append('user', q.replace(/\n/g,'<br>'))
  qin.value = ''
  const placeholder = append('bot', '<div class="loading">...</div>')

  // Clarification côté interface si la question est trop vague
  if (isVagueQuestion(q)) {
    const menu = await buildDynamicClarification(q, "Pour bien répondre, j’ai besoin de préciser votre demande.")
    renderClarificationMenu(q, placeholder, menu.why, menu.options)
    return
  }

  if (isInterurbanOverviewQuery(qExpanded)) {
    await showInterurbanDestinationPicker(placeholder.querySelector('.bubble'))
    chat.scrollTop = chat.scrollHeight
    return
  }

  try{
    const apiCandidates = getChatbotApiBases('/ask')

    let res = null
    let lastErr = null
    for (const apiUrl of apiCandidates) {
      try {
        const ctrl = new AbortController()
        const t = setTimeout(() => ctrl.abort(), 30000)
        const attempt = await fetch(apiUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: qExpanded, conversationHistory }),
          signal: ctrl.signal
        })
        clearTimeout(t)
        if (attempt && attempt.ok) {
          res = attempt
          break
        }
        lastErr = new Error(`HTTP ${attempt.status} on ${apiUrl}`)
      } catch (e) {
        lastErr = e
      }
    }
    if (!res) {
      throw lastErr || new Error('Aucun endpoint API disponible')
    }
    
    const json = await res.json()
    
    // Gérer le cas où le bot demande des précisions
    if (json.needs_clarification) {
      const prompt = json.answer || json.clarification_prompt || "Pourriez-vous préciser votre question ?"
      const suggestions = Array.isArray(json.suggestions) && json.suggestions.length
        ? json.suggestions
        : null

      if (suggestions) {
        // Utiliser les suggestions pré-définies du backend
        // Chaque suggestion peut être {label, query} ou une simple chaîne (rétrocompat)
        const btns = suggestions.map(s => {
          const label = (typeof s === 'object' && s.label) ? s.label : (typeof s === 'string' ? s : String(s))
          const query = (typeof s === 'object' && s.query) ? s.query : label
          const safeQuery = query.replace(/\\/g, '\\\\').replace(/'/g, "\\'")
          return `<button class="clarif-btn" onclick="sendQuickReply('${safeQuery}')">${escapeHtml(label)}</button>`
        }).join('')
        placeholder.querySelector('.bubble').innerHTML = `
          <div class="clarification-box">
            <p style="margin-bottom:10px">${escapeHtml(prompt)}</p>
            <div class="clarification-box-actions" style="display:flex;flex-direction:column;gap:6px">${btns}</div>
          </div>`
      } else {
        // Fallback : générer via DeepSeek
        const promptHtml = prompt.replace(/\n/g, '<br>')
        const menu = await buildDynamicClarification(q, promptHtml.replace(/<br>/g, ' '))
        renderClarificationMenu(q, placeholder, menu.why, menu.options)
      }

      chat.scrollTop = chat.scrollHeight
      pushConversationExchange(qExpanded, json)
      return
    }
    
    // SI C'EST UNE REQUÊTE DE LIGNE DU NOUVEAU BACKEND
    if (json.query_type === 'all_lines_summary' || json.query_type === 'line_details' || json.query_type === 'line_summary_only' || json.query_type === 'lines_to_stop') {
      // Afficher les résultats selon le type de requête
      if (json.query_type === 'lines_to_stop') {
        const stopRequested = json.stop_requested || '';
        const answerRaw = json.answer || json.summary || '';
        const rows = Array.isArray(json.results) ? json.results : [];
        const totalLines = rows.length > 0 ? rows.length : (json.total_lines != null ? json.total_lines : 0);
        let listHtml = '';
        if (rows.length > 0) {
          listHtml = '<ul class="lines-stop-list">' + rows.map((row) => {
            const num = (row.number || '').toString().replace(/"/g, '&quot;');
            return `<li><span class="line-chip" role="button" tabindex="0" data-linenum="${num}">Ligne ${escapeHtml(num)}</span></li>`;
          }).join('') + '</ul>';
        }
        const copyLinesText = rows.length
          ? `${totalLines} ligne(s) pour « ${stopRequested} » :\n` + rows.map((r) => `• Ligne ${r.number}`).join('\n')
          : answerRaw;
        let html = `<div class="line-details-container">
          <div class="city-header">📍 ${escapeHtml(stopRequested || 'Arrêt')}</div>
          <div class="lines-stop-block">
            <p class="lines-stop-intro">${totalLines} ligne(s) desservent cet arrêt ou ce lieu :</p>
            <div class="lines-stop-compact">${listHtml}</div>
          </div>
          <div class="copy-fab-row"><button type="button" class="copy-fab" id="copy-lines-to-stop-btn" title="Copier" aria-label="Copier">${COPY_FAB_SVG}</button></div>
        </div>
        ${formatMoreInfoMeta(json)}
        <div class="controls">
          <button class="small-btn" onclick="showAllLines()"><span>🚍 Retour aux lignes</span></button>
        </div>`;
        placeholder.querySelector('.bubble').innerHTML = html;
        const bubble = placeholder.querySelector('.bubble');
        if (bubble && !bubble.dataset.linesToStopClickable) {
          bubble.dataset.linesToStopClickable = 'true';
          bubble.addEventListener('click', (ev) => {
            const chip = ev.target && ev.target.closest ? ev.target.closest('.line-chip') : null;
            if (!chip) return;
            const lineNum = chip.getAttribute('data-linenum') || '';
            if (lineNum) askForLineDetails(lineNum);
          });
        }
        const copyBtn = placeholder.querySelector('#copy-lines-to-stop-btn');
        if (copyBtn) {
          copyBtn.addEventListener('click', () => robustCopy(copyLinesText, copyBtn));
        }
        chat.scrollTop = chat.scrollHeight;
        pushConversationExchange(qExpanded, json)
        return;
      }
      if (json.query_type === 'all_lines_summary') {
        // Afficher toutes les lignes
        placeholder.querySelector('.bubble').innerHTML = `
            <div class="bot-reply-block">
            ${formatAllLinesSummary(json)}
            ${formatMoreInfoMeta(json)}
            <div class="copy-fab-row"><button type="button" class="copy-fab" id="copy-lines-btn" title="Copier" aria-label="Copier">${COPY_FAB_SVG}</button></div>
            </div>
        `;

        // Rendre les lignes cliquables (clic + touche Entrée)
        const bubble = placeholder.querySelector('.bubble');
        if (bubble && !bubble.dataset.linesClickableBound) {
            bubble.dataset.linesClickableBound = 'true';
            bubble.addEventListener('click', (ev) => {
                const target = ev.target;
                if (!target || typeof target.closest !== 'function') return;
                const item = target.closest('.line-item-simple');
                if (!item) return;
                const lineNum = item.getAttribute('data-linenum') || '';
                if (lineNum) askForLineDetails(lineNum);
            });
            bubble.addEventListener('keydown', (ev) => {
                if (ev.key !== 'Enter') return;
                const target = ev.target;
                if (!target || !(target instanceof HTMLElement)) return;
                if (!target.classList.contains('line-item-simple')) return;
                const lineNum = target.getAttribute('data-linenum') || '';
                if (lineNum) {
                    ev.preventDefault();
                    askForLineDetails(lineNum);
                }
            });
        }
        
        // Gestionnaire pour Copier
        const copyBtn = placeholder.querySelector('#copy-lines-btn');
        if (copyBtn) {
            copyBtn.addEventListener('click', () => {
                const lines = json.lines_summary || [];
                const textToCopy = lines.map(line => `• ${line.summary}`).join('\n');
                robustCopy(textToCopy, copyBtn);
            });
        }
      } 
      else if (json.query_type === 'line_summary_only') {
        // Afficher nom de la ligne, nombre d'arrêts et liste des arrêts (comme line_details)
        const line = json.line_summary;
        const stopsList = json.bullets || [];
        const stopCount = json.stop_count != null ? json.stop_count : stopsList.length;
        let html = `
            <div class="line-details-container">
                <div class="city-header">${line.number}</div>
                <div class="line-main-route">
                    <span class="line-start-big">${line.start || '–'}</span>
                    <span class="line-arrow-big">↔</span>
                    <span class="line-end-big">${line.end || '–'}</span>
                </div>
                <div class="line-category-badge">
                    📍 Nombre d'arrêts : ${stopCount}
                </div>
        `;
        if (stopsList.length > 0) {
          html += `
                <div class="stops-container">
                    <div class="section-title">🛑 Liste des arrêts</div>
                    <div class="stops-timeline">
          `;
          stopsList.forEach((stop, index) => {
            const cleanStop = sanitizeStopDisplay(stop);
            if (!cleanStop) return;
            const isTerminus = (index === stopsList.length - 1);
            html += `
                    <div class="stop-item ${isTerminus ? 'stop-terminus' : ''}">
                        <div class="stop-marker">${isTerminus ? '🏁' : '●'}</div>
                        <div class="stop-content">
                            <div class="stop-name">${cleanStop}</div>
                            ${index === 0 ? '<div class="stop-label">Départ</div>' : index === stopsList.length - 1 ? '<div class="stop-label">Arrivée</div>' : ''}
                        </div>
                    </div>
            `;
          });
          html += `
                    </div>
                </div>
          `;
        } else {
          html += `
                <div class="clarification-box">
                    <p>Liste des arrêts : Aucun arrêt extrait pour cette ligne.</p>
                </div>
          `;
        }
        html += `
            <div class="copy-fab-row"><button type="button" class="copy-fab" id="copy-line-summary-btn" title="Copier" aria-label="Copier">${COPY_FAB_SVG}</button></div>
            </div>
            ${formatMoreInfoMeta(json)}
            <div class="controls">
                <button class="small-btn" onclick="showAllLines()"><span>🚍 Retour aux lignes</span></button>
            </div>
        `;
        placeholder.querySelector('.bubble').innerHTML = html;
        const copyBtn = placeholder.querySelector('#copy-line-summary-btn');
        if (copyBtn) {
          copyBtn.addEventListener('click', () => {
            let textToCopy = `${line.number} : ${line.start} ↔ ${line.end}\n\nNombre d'arrêts : ${stopCount}\n\nListe des arrêts :\n`;
            if (stopsList.length > 0) stopsList.forEach((s, i) => { textToCopy += `• ${s}\n`; });
            else textToCopy += 'Aucun arrêt extrait pour cette ligne.';
            robustCopy(textToCopy, copyBtn);
          });
        }
      }
      else if (json.query_type === 'line_details') {
        // Ligne demandée mais fiche non trouvée (ex. LIGNE TAF TAF générique) : afficher le message answer
        if (!json.line_details && json.answer) {
          const answerHtml = (json.answer || '').replace(/\n/g, '<br>').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
          placeholder.querySelector('.bubble').innerHTML = `
            <div class="line-details-container">
              <div class="city-header">${json.summary || 'LIGNE TAF TAF'}</div>
              <div class="clarification-box" style="margin-top: 1rem;">
                ${answerHtml}
              </div>
              ${formatMoreInfoMeta(json)}
              <div class="controls">
                <button class="small-btn" onclick="showAllLines()"><span>🚍 Retour aux lignes</span></button>
              </div>
            </div>
          `;
          chat.scrollTop = chat.scrollHeight;
          pushConversationExchange(qExpanded, json)
          return;
        }
        // Afficher nom de la ligne, nombre d'arrêts et liste des arrêts (toujours)
        const line = json.line_details;
        const stopsList = line.stops || [];
        const stopCount = line.stop_count != null ? line.stop_count : stopsList.length;
        const catLabel = (line.category || '').charAt(0).toUpperCase() + (line.category || '').slice(1).toLowerCase();

        let html = `<div style="margin:4px 0">`;
        html += `<strong style="color:var(--bot,#0d9488);font-size:1rem">🚌 Ligne ${escapeHtml(line.number || '')}</strong>`;
        html += `<div style="margin:4px 0;font-size:.9rem">${escapeHtml(line.start || '–')} ↔ ${escapeHtml(line.end || '–')}</div>`;
        if (catLabel || stopCount) {
          html += `<div style="font-size:.82rem;color:#666;margin-top:2px">`;
          if (catLabel) html += catLabel;
          if (catLabel && stopCount) html += ' · ';
          if (stopCount) html += `${stopCount} arrêt${stopCount > 1 ? 's' : ''}`;
          html += `</div>`;
        }
        html += `</div>`;

        if (stopsList.length > 0) {
          html += `<ul style="list-style:none;padding:0;margin:8px 0 0;display:flex;flex-direction:column;gap:2px;font-size:.88rem">`;
          stopsList.forEach((stop, index) => {
            const cleanStop = sanitizeStopDisplay(stop);
            if (!cleanStop) return;
            const isFirst = index === 0;
            const isLast  = index === stopsList.length - 1;
            const marker  = isFirst ? '🔵' : isLast ? '🔴' : '–';
            html += `<li style="padding:2px 6px">${marker} ${escapeHtml(cleanStop)}</li>`;
          });
          html += `</ul>`;
        } else {
          html += `<p style="font-size:.88rem;color:#888;margin-top:6px">Aucun arrêt extrait pour cette ligne.</p>`;
        }

        html += `
          <div class="copy-fab-row"><button type="button" class="copy-fab" id="copy-line-details-btn" title="Copier" aria-label="Copier">${COPY_FAB_SVG}</button></div>
          ${formatMoreInfoMeta(json)}
          <div class="controls">
            <button class="small-btn" onclick="showAllLines()"><span>🚍 Retour aux lignes</span></button>
          </div>
        `;
        
        placeholder.querySelector('.bubble').innerHTML = html;
        
        // Gestionnaire pour copier les détails (nom, nombre d'arrêts, liste)
        const copyBtn = placeholder.querySelector('#copy-line-details-btn');
        if (copyBtn) {
            copyBtn.addEventListener('click', () => {
                let textToCopy = `${line.number} : ${line.start} ↔ ${line.end}\n\nNombre d'arrêts : ${stopCount}\n\nListe des arrêts :\n`;
                if (stopsList.length > 0) {
                    stopsList.forEach((stop, index) => { textToCopy += `${index + 1}. ${stop}\n`; });
                } else {
                    textToCopy += 'Aucun arrêt extrait pour cette ligne.\n';
                }
                robustCopy(textToCopy, copyBtn);
            });
        }
      }
    }
    // SINON, UTILISER L'ANCIENNE LOGIQUE
    else if(json && (json.summary || (json.results && json.results.length))){
      const result = json.results[0] || {}
      const targetCity = result.target_city || ''
      // Priorité à la réponse backend complète pour un affichage plus propre
      const snippet = json.answer || result.snippet || json.summary || ''
      const fullText = json.answer || result.full_text || json.summary || ''
      const isCityQuery = json.is_city_query || false
      
      // Détecter si c'est une recherche de lignes (ancienne méthode) — mots entiers uniquement
      const _lineWords = /\b(ligne|lignes|bus|transport|réseau)\b/i
      const isLineQuery = json.is_line_query || _lineWords.test(currentQuestion)
      
      // Nettoyer le contenu parasite (navigation/header du site)
      const cleanAnswer = stripNavContent(fullText || snippet)
      const PREVIEW_MAX = 300

      // Contenu source brut (données d'origine du site, avant reformulation Gemini)
      const rawSourceContent = stripNavContent(
        result.full_text || result.content || json.summary || ''
      )
      // Source brute (optionnelle) : ne pas la coller directement dans la réponse,
      // sinon ça donne un gros bloc "── Source complète ──" peu lisible.
      const hasSource = rawSourceContent && rawSourceContent.length > 30
        && rawSourceContent !== cleanAnswer
      const detailsContent = cleanAnswer

      const hasIndexedSnippet = json.results && json.results.length > 0
      const structuredInAnswer =
        answerHasStructuredBlockMarkers(cleanAnswer)
      const useProseAnswer =
        json.query_type === 'city_info' ||
        (!isCityQuery && !isLineQuery && !hasIndexedSnippet && !structuredInAnswer)

      // Formater la réponse
      let responseHtml = ''
      
      if (isLineQuery) {
        responseHtml = formatBusLines(cleanAnswer, targetCity)
      } else if (isCityQuery) {
        const sd = result.structured_data || json.structured_data || {}
        const cityLabel = targetCity || sd.titre || (sd.villes && sd.villes[0]) || ''
        if (sd && Object.keys(sd).length > 0) {
          responseHtml += formatStructuredData(sd, cityLabel)
        } else {
          responseHtml += `<div class="answer-content answer-plain">${formatResponseText(cleanAnswer)}</div>`
        }
      } else if (useProseAnswer) {
        responseHtml += `<div class="answer-content answer-plain">${formatResponseText(cleanAnswer)}</div>`
      } else {
        // Afficher un extrait si la réponse est longue
        const isTruncated = cleanAnswer.length > PREVIEW_MAX
        const previewText = isTruncated
          ? cleanAnswer.substring(0, PREVIEW_MAX).trimEnd()
          : cleanAnswer
        const showExpand = !useProseAnswer && !isLineQuery && !isCityQuery && isTruncated
        const encodedDetails = encodeURIComponent(detailsContent)
        let previewHtml = formatResponseText(previewText)
        if (showExpand) {
          previewHtml = appendInlineSeeMore(previewHtml, encodedDetails)
        }
        responseHtml += `<div class="answer-content">${previewHtml}</div>`
      }
      
      if (shouldShowMoreInfoLink(json)) {
        responseHtml += formatMoreInfoMeta(json)
      }
      
      const encodedFull = encodeURIComponent(cleanAnswer)
      const copyFabBtn =
        `<div class="copy-fab-row"><button type="button" class="copy-fab" data-action="copy" data-copy="${encodedFull}" title="Copier" aria-label="Copier">${COPY_FAB_SVG}</button></div>`
      responseHtml =
        '<div class="bot-reply-block">' +
        responseHtml +
        copyFabBtn +
        '</div>'
      
      placeholder.querySelector('.bubble').innerHTML = responseHtml

      // « voir plus » : développer la réponse dans la bulle
      const expandBtn = placeholder.querySelector('button[data-action="show-full-content"]')
      if (expandBtn) {
        expandBtn.addEventListener('click', function() {
          const fullContent = decodeURIComponent(this.dataset.full || '')
          const bubble = this.closest('.bubble')
          const contentDiv = bubble ? bubble.querySelector('.answer-content') : null
          if (contentDiv && fullContent) {
            contentDiv.innerHTML = formatResponseText(fullContent)
          }
          chat.scrollTop = chat.scrollHeight
        })
      }
      
      // Gestionnaire pour le bouton "Copier"
      const copyBtn = placeholder.querySelector('button[data-action="copy"]')
      if (copyBtn) {
        copyBtn.addEventListener('click', () => {
          const textToCopy = decodeURIComponent(copyBtn.dataset.copy || '') || cleanAnswer
          robustCopy(textToCopy, copyBtn)
        })
      }
      
    } else {
      // Aucun résultat trouvé
      placeholder.querySelector('.bubble').innerHTML = `
        <div class="city-header">😕 Information non trouvée</div>
        <p>Je n'ai pas trouvé d'informations précises pour votre question.</p>
        <div class="list-container">
          <div class="section-title">💡 SUGGESTIONS</div>
          <div class="list-item">Essayez avec une question plus spécifique :</div>
          <div class="list-item">• "Horaires Dakar-Fatick"</div>
          <div class="list-item">• "Prix pour Touba"</div>
          <div class="list-item">• "Contact service client"</div>
          <div class="list-item">• "Lignes urbaines Dakar"</div>
        </div>
      `
    }
    pushConversationExchange(qExpanded, json)
  } catch(err){
    console.error('Erreur:', err)
    placeholder.querySelector('.bubble').innerHTML = `
      <div class="clarification-box">
        <strong>❌ Erreur de connexion</strong>
        <p>Impossible de se connecter au serveur backend.</p>
        <div class="list-item">1. Vérifiez que le serveur Flask répond (${escapeHtml((getChatbotApiBases('/ask')[0] || '').replace('/ask', '') || 'URL API')})</div>
        <div class="list-item">2. Vérifiez votre connexion internet</div>
        <div class="list-item">3. Réessayez dans quelques instants</div>
      </div>
    `
  }
})
    function formatCityInfo(text, city) {
    if (!text) return '';
    
    // Si le texte commence déjà par la ville en format structuré
    if (text.includes('📍')) {
        return text;
    }
    
    // Supprimer les parties dupliquées
    let cleanText = removeDuplicateSections(text, city);
    
    // Formater
    let html = `<div class="city-header">📍 ${city.toUpperCase()}</div>`;
    
    // Extraire les sections uniques
    const sections = extractUniqueSections(cleanText, city);
    
    // Construire l'affichage
    if (sections.prix && sections.prix.length > 0) {
        html += `<div class="section-title">💰 PRIX</div>`;
        sections.prix.forEach(prix => {
            html += `<div class="list-item">${prix}</div>`;
        });
    }
    
    if (sections.horaires && sections.horaires.length > 0) {
        html += `<div class="section-title">⏰ HORAIRES</div>`;
        html += `<div class="list-item">${sections.horaires.join(', ')}</div>`;
    }
    
    if (sections.depart && sections.depart.length > 0) {
        html += `<div class="section-title">🚀 DÉPART</div>`;
        html += `<div class="list-item">${sections.depart[0]}</div>`;
    }
    
    if (sections.arrivee && sections.arrivee.length > 0) {
        html += `<div class="section-title">🏁 ARRIVÉE</div>`;
        html += `<div class="list-item">${sections.arrivee[0]}</div>`;
    }
    
    if (sections.contact && sections.contact.length > 0) {
        html += `<div class="section-title">📞 CONTACTS</div>`;
        sections.contact.forEach(contact => {
            html += `<div class="list-item">${contact}</div>`;
        });
    }
    
    return html;
}

function sanitizeLineEndpointDisplay(value) {
    let x = (value || '').toString().trim();
    if (!x) return 'Non spécifié';
    x = x.replace(/<-->|<->|↔/g, ' ');
    x = x.replace(/\bLIGNE\s+\d+[A-Z&]*\b.*$/i, '');
    x = x.replace(/\bTO1\b.*$/i, '');
    x = x.replace(/\s+/g, ' ').trim();
    return x || 'Non spécifié';
}

function sanitizeStopDisplay(value) {
    let x = (value || '').toString().trim();
    if (!x) return '';
    x = x.replace(/\.\s*LIGNE\s+.*$/i, '');
    x = x.replace(/\bLIGNE\s+\d+[A-Z&]*\b.*$/i, '');
    x = x.replace(/\bTO1\b.*$/i, '');
    x = x.replace(/<-->|<->|↔/g, ' ');
    x = x.replace(/\s+/g, ' ').trim();
    if (!x || /\bLIGNE\b/i.test(x)) return '';
    return x;
}

function formatAllLinesSummary(json) {
    if (!json || !json.lines_summary) {
        return '<p>Informations sur les lignes non disponibles.</p>';
    }

    const lines = json.lines_summary;
    const totalLines = json.total_lines || lines.length;

    // Trier puis dédoublonner par numéro de ligne (garder la première occurrence)
    const sortedLines = [...lines].sort((a, b) => {
        const na = parseInt((extractLineNumberOnly(a.number).match(/\d+/) || [999])[0]);
        const nb = parseInt((extractLineNumberOnly(b.number).match(/\d+/) || [999])[0]);
        return na !== nb ? na - nb : (a.number || '').localeCompare(b.number || '');
    });
    const seenNums = new Set();
    const uniqueLines = sortedLines.filter(line => {
        const num = extractLineNumberOnly(line.number);
        if (seenNums.has(num)) return false;
        seenNums.add(num);
        return true;
    });

    let html = `<div style="margin:4px 0"><strong style="color:var(--bot,#0d9488)">🚍 Réseau Dakar Dem Dikk — ${uniqueLines.length} lignes</strong></div>`;
    html += `<ul style="list-style:none;padding:0;margin:8px 0 0;display:flex;flex-direction:column;gap:3px">`;

    uniqueLines.forEach(line => {
        const num = extractLineNumberOnly(line.number);
        let start = sanitizeLineEndpointDisplay(line.start);
        let end   = sanitizeLineEndpointDisplay(line.end);
        const label = `Ligne ${num} : ${start} ↔ ${end}`;
        html += `<li>
          <div class="line-item-simple" role="button" tabindex="0" data-linenum="${escapeHtml(num)}"
            style="padding:5px 8px;border-radius:6px;cursor:pointer;font-size:.9rem;color:#111;
                   transition:background .15s;display:block"
            onmouseover="this.style.background='#f0fdf9'"
            onmouseout="this.style.background='none'">
            ${escapeHtml(label)}
          </div>
        </li>`;
    });

    html += `</ul>`;
    return html;
}

function extractLineNumberOnly(lineStr) {
    if (!lineStr) return '';
    // Extraire le numéro complet incluant les lettres (ex: 502A, 16A, etc.)
    const match = lineStr.match(/(\d+[A-Z&]*)/);
    return match ? match[1] : '';
}

function truncateText(text, maxLength) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength - 3) + '...';
}

// Ajouter les fonctions manquantes
function extractSectionsFromText(text, city) {
  const sections = {
    prix: '',
    horaires: '',
    depart: '',
    arrivee: '',
    contact: ''
  }
  
  // Logique simplifiée d'extraction
  const lines = text.split('\n')
  
  for (const line of lines) {
    const cleanLine = line.trim()
    if (cleanLine.includes('PRIX') || cleanLine.includes('FCFA')) {
      sections.prix = cleanLine
    } else if (cleanLine.includes('HORAIRE') || cleanLine.includes('HEURE')) {
      sections.horaires = cleanLine
    } else if (cleanLine.includes('DÉPART') || cleanLine.includes('DAKAR')) {
      sections.depart = cleanLine
    } else if (cleanLine.includes('ARRIVÉE') || (city && cleanLine.toLowerCase().includes(city.toLowerCase()))) {
      sections.arrivee = cleanLine
    } else if (cleanLine.includes('CONTACT') || cleanLine.includes('TÉLÉPHONE')) {
      sections.contact = cleanLine
    }
  }
  
  return sections
}
function removeDuplicateSections(text, city) {
    if (!text) return '';
    
    // Diviser en paragraphes
    const paragraphs = text.split(/\n\n+/).filter(p => p.trim());
    
    // Garder les paragraphes uniques
    const uniqueParagraphs = [];
    const seenContent = new Set();
    
    for (const para of paragraphs) {
        // Normaliser pour comparaison
        const normalized = para.toLowerCase().replace(/\s+/g, ' ').trim();
        
        // Ignorer les doublons exacts
        if (!seenContent.has(normalized)) {
            seenContent.add(normalized);
            uniqueParagraphs.push(para);
        }
    }
    
    return uniqueParagraphs.join('\n\n');
}

// Fonction pour extraire les sections uniques
function extractUniqueSections(text, city) {
    const sections = {
        prix: [],
        horaires: [],
        depart: [],
        arrivee: [],
        contact: []
    };
    
    const seen = {
        prix: new Set(),
        horaires: new Set(),
        depart: new Set(),
        arrivee: new Set(),
        contact: new Set()
    };
    
    // Analyser le texte
    const lines = text.split('\n');
    
    for (const line of lines) {
        const cleanLine = line.trim();
        if (!cleanLine) continue;
        
        const lowerLine = cleanLine.toLowerCase();
        
        // Prix
        if (lowerLine.includes('prix') || lowerLine.includes('fcfa')) {
            const priceMatch = cleanLine.match(/(\d[\d\s,]*)\s*FCFA/i);
            if (priceMatch && !seen.prix.has(priceMatch[0])) {
                seen.prix.add(priceMatch[0]);
                sections.prix.push(priceMatch[0]);
            }
        }
        
        // Horaires
        else if (lowerLine.includes('heure') || lowerLine.includes('horaire')) {
            const times = cleanLine.match(/\b\d{1,2}h(?:\s*\d{0,2})?\b/g);
            if (times) {
                times.forEach(time => {
                    if (!seen.horaires.has(time)) {
                        seen.horaires.add(time);
                        sections.horaires.push(time);
                    }
                });
            }
        }
        
        // Contact
        else if (lowerLine.includes('contact') || lowerLine.includes('téléphone')) {
            const phones = cleanLine.match(/(?:\+221\s*)?\d{2}\s*\d{3}\s*\d{2}\s*\d{2}/g);
            if (phones) {
                phones.forEach(phone => {
                    if (!seen.contact.has(phone)) {
                        seen.contact.add(phone);
                        sections.contact.push(phone);
                    }
                });
            }
        }
    }
    
    // Ajouter les valeurs par défaut uniques
    if (sections.depart.length === 0) {
        sections.depart.push('Dakar Terminus Liberté 5');
    }
    
    if (sections.arrivee.length === 0 && city) {
        sections.arrivee.push(city.charAt(0).toUpperCase() + city.slice(1));
    }
    
    return sections;
}
function formatCompleteText(text) {
    if (!text) return '';
    
    // Diviser en paragraphes (par sauts de ligne doubles)
    const paragraphs = text.split(/\n\n+/).filter(p => p.trim())
    
    if (paragraphs.length === 1) {
        // Un seul paragraphe
        return `<p>${text.replace(/\n/g, ' ')}</p>`;
    } else {
        // Plusieurs paragraphes avec un espacement
        return paragraphs.map(p => 
            `<p class="complete-paragraph">${p.trim().replace(/\n/g, ' ')}</p>`
        ).join('');
    }
}

// Fonction pour formater le contenu complet des villes
function formatCompleteContent(text, city, isCityQuery) {
    if (!text) return '';
    
    if (isCityQuery) {
        // Format structuré pour les villes
        return formatCityInfo(text, city);
    } else {
        // Format texte pour les autres
        return formatCompleteText(text);
    }
}

function formatTextWithParagraphs(text) {
    // Diviser en paragraphes naturels
    const paragraphs = text.split(/\n\n+/).filter(p => p.trim());
    
    if (paragraphs.length === 1) {
        // Un seul paragraphe
        return `<p>${text.replace(/\n/g, ' ')}</p>`;
    } else {
        // Plusieurs paragraphes
        return paragraphs.map(p => 
            `<p>${p.trim().replace(/\n/g, ' ')}</p>`
        ).join('');
    }
}

function formatStructuredText(text, city) {
    // Pour les villes, utiliser le format existant
    if (text.includes('💰 PRIX') || text.includes('⏰ HORAIRES')) {
        // C'est déjà formaté
        return text;
    }
    
    // Sinon, formater proprement
    let html = `<div class="city-header">📍 ${city.toUpperCase()}</div>`;
    
    // Chercher les informations structurées
    const sections = extractSectionsFromText(text, city);
    
    // Construire l'affichage
    if (sections.prix) {
        html += `<div class="section-title">💰 PRIX</div>`;
        html += `<div class="list-item">${sections.prix}</div>`;
    }
    
    if (sections.horaires) {
        html += `<div class="section-title">⏰ HORAIRES</div>`;
        html += `<div class="list-item">${sections.horaires}</div>`;
    }
    
    if (sections.depart) {
        html += `<div class="section-title">🚀 DÉPART</div>`;
        html += `<div class="list-item">${sections.depart}</div>`;
    }
    
    if (sections.arrivee) {
        html += `<div class="section-title">🏁 ARRIVÉE</div>`;
        html += `<div class="list-item">${sections.arrivee}</div>`;
    }
    
    if (sections.contact) {
        html += `<div class="section-title">📞 CONTACT</div>`;
        html += `<div class="list-item">${sections.contact}</div>`;
    }
    
    return html;
}
function formatBusLines(text, city = '') {
    if (!text) return '';

    const hasLines = text.includes('LIGNE') || text.includes('ligne');
    if (!hasLines) {
        return `<div class="preview">${text.replace(/\n/g, '<br>')}</div>`;
    }

    // Extraire chaque ligne sous la forme "LIGNE X : Terminus A <-> Terminus B"
    const lines = extractAllLines(text);
    if (!lines.length) {
        return `<div class="preview">${text.replace(/\n/g, '<br>')}</div>`;
    }

    const title = city
        ? `Lignes de transport — ${city.toUpperCase()}`
        : `Réseau Dakar Dem Dikk — ${lines.length} lignes`;

    let html = `<div style="margin:4px 0"><strong style="color:var(--bot,#0d9488)">${title}</strong></div>`;
    html += `<ul style="list-style:none;padding:0;margin:8px 0 0;display:flex;flex-direction:column;gap:4px">`;
    lines.forEach(line => {
        const label = line.replace(/^LIGNE\s+/i, 'Ligne ').trim();
        html += `<li>
          <button onclick="sendQuickReply('${label.replace(/'/g, "\\'")}')"
            style="background:none;border:none;cursor:pointer;text-align:left;
                   padding:5px 8px;border-radius:6px;font-size:.9rem;color:#111;
                   width:100%;transition:background .15s"
            onmouseover="this.style.background='#f0fdf9'"
            onmouseout="this.style.background='none'">
            🚌 ${escapeHtml(label)}
          </button>
        </li>`;
    });
    html += `</ul>`;
    return html;
}

// Fonction pour extraire toutes les lignes du texte
function extractAllLines(text) {
    const lines = [];
    const rawLines = text.split('\n');
    
    rawLines.forEach(line => {
        const cleanLine = line.trim();
        if (!cleanLine) return;
        
        // Détecter les lignes de bus
        if (cleanLine.includes('LIGNE') || cleanLine.match(/LIGNE\s+\d+/i) || cleanLine.match(/ligne\s+\d+/i)) {
            // Nettoyer la ligne
            const formattedLine = formatSingleLine(cleanLine);
            if (formattedLine) {
                lines.push(formattedLine);
            }
        }
    });
    
    return lines;
}

// Formater une ligne individuelle
function formatSingleLine(line) {
    let cleanLine = line.trim();
    
    // Supprimer les mots superflus
    cleanLine = cleanLine.replace(/^[:\-—•\s]+/, '').trim();
    
    // Formater proprement
    if (cleanLine.includes('↔') || cleanLine.includes('→')) {
        const parts = cleanLine.split(/[↔→]/).map(p => p.trim());
        if (parts.length >= 2) {
            const lineNumberMatch = parts[0].match(/(LIGNE\s+\d+[A-Z]*)/i);
            if (lineNumberMatch) {
                const lineNumber = lineNumberMatch[1];
                const destinations = parts.slice(1).join(' ↔ ');
                return `${lineNumber.toUpperCase()}: ${destinations}`;
            }
        }
    }
    
    return cleanLine;
}

// Créer une carte pour chaque ligne
function formatLineCard(line) {
    if (!line) return '';
    
    // Extraire le numéro de ligne
    const lineMatch = line.match(/(LIGNE\s+\d+[A-Z]*)/i);
    const lineNumber = lineMatch ? lineMatch[1] : 'LIGNE';
    
    // Extraire les destinations
    const destinations = line.replace(lineNumber, '').replace(/[:—]/g, '').trim();
    
    // Couleur selon le type de ligne
    let lineColor = '#3b82f6'; // Bleu par défaut
    
    if (line.includes('TER') || line.includes('GARE')) {
        lineColor = '#ef4444'; // Rouge pour TER
    } else if (line.includes('URBAINE')) {
        lineColor = '#10b981'; // Vert pour urbaine
    } else if (line.includes('BANLIEUE')) {
        lineColor = '#f59e0b'; // Orange pour banlieue
    } else if (line.includes('TAF')) {
        lineColor = '#8b5cf6'; // Violet pour taf-taf
    }
    
    return `
        <div class="line-card-content" style="border-left: 4px solid ${lineColor}">
            <div class="line-number" style="color: ${lineColor}">${lineNumber}</div>
            <div class="line-destinations">${destinations}</div>
            <div class="line-emoji">${getLineEmoji(line)}</div>
        </div>
    `;
}

// Emoji selon le type de ligne
function getLineEmoji(line) {
    if (line.includes('TER') || line.includes('GARE')) return '🚆';
    if (line.includes('AÉROPORT') || line.includes('AIBD')) return '✈️';
    if (line.includes('UCAD') || line.includes('UNIVERSITÉ')) return '🎓';
    if (line.includes('MARCHÉ') || line.includes('COMMERCIAL')) return '🛒';
    if (line.includes('HÔPITAL') || line.includes('SANTÉ')) return '🏥';
    if (line.includes('ADMINISTRATION') || line.includes('MINISTÈRE')) return '🏛️';
    if (line.includes('PLAGE') || line.includes('MER')) return '🏖️';
    return '🚌';
}
/**
 * Affiche les données interurbaines (interurbain_data.py / API).
 * Gère prix string | objet Louga/Kébémer | tableau legacy, horaires en chaînes,
 * lieux_contact, etc.
 */
function formatStructuredData(structuredData, city) {
  if (!structuredData) return '';

  const sd = structuredData
  const title = (city || sd.titre || 'INFORMATIONS').toString()
  let html = `<div class="city-header">📍 ${escapeHtml(title)}</div>`

  // 1. PRIX — chaîne ("3000 FCFA"), objet multi-villes, ou tableau {valeur}
  const prixRaw = sd.prix
  if (prixRaw != null && prixRaw !== '') {
    html += `<div class="section-title">💰 PRIX</div>`
    if (Array.isArray(prixRaw)) {
      prixRaw.forEach((p) => {
        const text = typeof p === 'object' && p !== null && 'valeur' in p
          ? (p.valeur || '')
          : String(p)
        if (text) html += `<div class="list-item">${escapeHtml(text)}</div>`
      })
    } else if (typeof prixRaw === 'object' && !Array.isArray(prixRaw)) {
      Object.keys(prixRaw).forEach((k) => {
        html += `<div class="list-item"><strong>${escapeHtml(k)}</strong> : ${escapeHtml(String(prixRaw[k]))}</div>`
      })
    } else {
      html += `<div class="list-item">${escapeHtml(String(prixRaw))}</div>`
    }
  }

  // 2. HORAIRES — sd.horaires (API) ou sd.heures (legacy)
  const horairesRaw = sd.horaires || sd.heures
  if (horairesRaw && Array.isArray(horairesRaw) && horairesRaw.length > 0) {
    html += `<div class="section-title">⏰ HORAIRES</div>`
    const parts = horairesRaw.map((h) => {
      if (typeof h === 'object' && h !== null && h.heure != null) return String(h.heure)
      return String(h)
    }).filter(Boolean)
    if (parts.length) {
      html += `<div class="list-item">${parts.map(escapeHtml).join(', ')}</div>`
    }
  }

  // 3. JOURS
  if (sd.jours && Array.isArray(sd.jours) && sd.jours.length > 0) {
    html += `<div class="section-title">📅 JOURS DE DÉPART</div>`
    sd.jours.forEach((jour) => {
      const jt = typeof jour === 'object' && jour !== null && jour.jour != null
        ? jour.jour
        : String(jour)
      if (jt) html += `<div class="list-item">${escapeHtml(jt)}</div>`
    })
  }

  // 4. DÉPART — chaîne unique ou tableau de {lieu}
  if (sd.depart != null && sd.depart !== '') {
    html += `<div class="section-title">🚀 DÉPART</div>`
    const dep = sd.depart
    if (Array.isArray(dep)) {
      dep.forEach((d) => {
        const t = typeof d === 'object' && d !== null && d.lieu != null ? d.lieu : String(d)
        if (t) html += `<div class="list-item">${escapeHtml(t)}</div>`
      })
    } else {
      html += `<div class="list-item">${escapeHtml(String(dep))}</div>`
    }
  }

  // 5. ARRIVÉE — tableau legacy sd.arrivee + lieux issus de lieux_contact (avant les téléphones)
  const contactsRaw = sd.lieux_contact || sd.contact
  const arrivalItems = []
  if (sd.arrivee && Array.isArray(sd.arrivee) && sd.arrivee.length > 0) {
    sd.arrivee.forEach((arrivee) => {
      const t = typeof arrivee === 'object' && arrivee !== null && arrivee.lieu != null
        ? arrivee.lieu
        : String(arrivee)
      if (t) arrivalItems.push(t)
    })
  }
  if (contactsRaw && Array.isArray(contactsRaw)) {
    contactsRaw.forEach((c) => {
      if (typeof c === 'object' && c !== null && String(c.lieu || '').trim()) {
        arrivalItems.push(String(c.lieu).trim())
      }
    })
  }
  if (arrivalItems.length > 0) {
    html += `<div class="section-title">🏁 ARRIVÉE</div>`
    arrivalItems.forEach((t) => {
      html += `<div class="list-item">${escapeHtml(t)}</div>`
    })
  }

  // 6. CONTACTS — téléphones et entrées legacy (après ARRIVÉE)
  if (contactsRaw && Array.isArray(contactsRaw) && contactsRaw.length > 0) {
    const telSeen = new Set()
    let contactBody = ''
    contactsRaw.forEach((c) => {
      if (typeof c === 'object' && c !== null) {
        if (c.valeur != null && !c.lieu) {
          const typeIcon = c.type === 'email' ? '✉️' : '📱'
          contactBody += `<div class="list-item">${typeIcon} ${escapeHtml(String(c.valeur))}</div>`
        } else if (c.tel != null && String(c.tel).trim()) {
          const tel = String(c.tel).trim()
          if (!telSeen.has(tel)) {
            telSeen.add(tel)
            contactBody += `<div class="list-item">📱 ${escapeHtml(tel)}</div>`
          }
        }
      } else {
        contactBody += `<div class="list-item">${escapeHtml(String(c))}</div>`
      }
    })
    if (contactBody) {
      html += `<div class="section-title">📞 CONTACTS</div>${contactBody}`
    }
  }

  return html
}

function _formatAllLinesSummary_OLD_UNUSED(linesData) {
    if (!linesData || !linesData.lines_summary) {
        return '<p>Informations sur les lignes non disponibles.</p>';
    }
    
    const lines = linesData.lines_summary;
    const totalLines = linesData.total_lines || lines.length;
    
    let html = '<div class="lines-container">';
    html += `<div class="city-header">🚍 Réseau Dakar Dem Dikk</div>`;
    html += `<p style="margin-bottom: 20px;"><strong>${totalLines} lignes disponibles</strong></p>`;
    
    // Trier toutes les lignes par numéro (sans catégories)
    const sortedLines = lines.sort((a, b) => {
        const numA = extractLineNumberOnly(a.number);
        const numB = extractLineNumberOnly(b.number);
        const matchA = numA.match(/(\d+)/);
        const matchB = numB.match(/(\d+)/);
        const intA = matchA ? parseInt(matchA[1]) : 999;
        const intB = matchB ? parseInt(matchB[1]) : 999;
        // Si même numéro, comparer les lettres (ex: 502A avant 502B)
        if (intA === intB) {
            return numA.localeCompare(numB);
        }
        return intA - intB;
    });
    
    html += `<div class="lines-list">`;
    
    sortedLines.forEach(line => {
        const lineNumOnly = extractLineNumberOnly(line.number);
        // Nettoyer le départ et l'arrivée pour enlever le texte parasite
        let start = (line.start || 'Non spécifié').toString();
        let end = (line.end || 'Non spécifié').toString();
        
        // Enlever les textes parasites comme "Lignes Banlieue", "Lignes Urbaines", etc.
        start = start.replace(/\s*Lignes\s+(Banlieue|Urbaines|TER|TAF|TAF TAF)\s*/gi, '').trim();
        end = end.replace(/\s*Lignes\s+(Banlieue|Urbaines|TER|TAF|TAF TAF)\s*/gi, '').trim();
        
        // Enlever aussi les textes comme "Cliquez sur une ligne", "pour voir son itineraire", etc.
        start = start.replace(/Cliquez\s+sur\s+une\s+ligne.*/gi, '').trim();
        end = end.replace(/Cliquez\s+sur\s+une\s+ligne.*/gi, '').trim();
        start = start.replace(/pour\s+voir\s+son\s+itineraire.*/gi, '').trim();
        end = end.replace(/pour\s+voir\s+son\s+itineraire.*/gi, '').trim();
        
        // Enlever les textes parasites spécifiques
        start = start.replace(/\s*(TERMI|Dépôt|15B|16B|A|B|Baux)\s*$/gi, '').trim();
        end = end.replace(/\s*(TERMI|Dépôt|15B|16B|A|B|Baux)\s*$/gi, '').trim();
        start = start.replace(/\s*(15B|16B)\s+/gi, ' ').trim();
        end = end.replace(/\s*(15B|16B)\s+/gi, ' ').trim();
        
        // Reconstruire les noms composés si coupés
        // Si départ se termine par "KEUR" et arrivée commence par "MASSAR"
        if (start.toUpperCase().endsWith('KEUR') && end.toUpperCase().startsWith('MASSAR')) {
            start = start.replace(/\s*KEUR\s*$/i, '').trim() + ' KEUR MASSAR';
            end = end.replace(/^MASSAR\s*/i, '').trim();
        }
        // Si départ se termine par "BAUX" et arrivée commence par "MARAICHERS"
        if (start.toUpperCase().endsWith('BAUX') && end.toUpperCase().startsWith('MARAICHERS')) {
            start = start.replace(/\s*BAUX\s*$/i, '').trim() + ' BAUX MARAICHERS';
            end = end.replace(/^MARAICHERS\s*/i, '').trim();
        }
        // Si départ se termine par "SCAT" et arrivée commence par "URBAM"
        if (start.toUpperCase().endsWith('SCAT') && end.toUpperCase().startsWith('URBAM')) {
            start = start.replace(/\s*SCAT\s*$/i, '').trim() + ' SCAT URBAM';
            end = end.replace(/^URBAM\s*/i, '').trim();
        }
        
        // Corriger les fautes de frappe
        start = start.replace(/\bMALIK\b(?!A)/gi, 'MALIKA');
        end = end.replace(/\bMALIK\b(?!A)/gi, 'MALIKA');
        end = end.replace(/\bLECLERCL\b/gi, 'LECLERC');
        
        // Enlever les espaces multiples
        start = start.replace(/\s+/g, ' ').trim();
        end = end.replace(/\s+/g, ' ').trim();
        
        // Si vide après nettoyage, utiliser "Non spécifié"
        if (!start || start === '') start = 'Non spécifié';
        if (!end || end === '') end = 'Non spécifié';
        
        html += `
        <div class="line-item-simple" role="button" tabindex="0" data-linenum="${escapeHtml(lineNumOnly)}" aria-label="Voir les détails de la ligne ${escapeHtml(lineNumOnly)}">
            <div class="line-number">${line.number}</div>
            <div class="line-route">
                <span class="line-from">${start}</span>
                <span class="line-arrow">↔</span>
                <span class="line-to">${end}</span>
            </div>
        </div>`;
    });
    
    html += `</div>`;
    html += `<div class="meta" style="margin-top: 20px; text-align: center;">
        <strong>📊 Total : ${totalLines} lignes</strong>
    </div>`;
    html += '</div>';
    
    return html;
}

function formatLineDetails(lineData) {
    if (!lineData) {
        return '<p>Détails de la ligne non disponibles.</p>';
    }

    const num  = lineData.number || '';
    const start = lineData.start || '';
    const end   = lineData.end   || '';
    const cat   = (lineData.category || '').toLowerCase();
    const catLabel = { ter: 'TER', urbaine: 'Urbaine', banlieue: 'Banlieue', taf: 'TAF', autre: 'Autre' }[cat] || cat.toUpperCase();
    const stopCount = lineData.stop_count || (lineData.stops ? lineData.stops.length : 0);

    let html = `<div style="margin:4px 0">`;
    html += `<strong style="color:var(--bot,#0d9488);font-size:1rem"> Ligne ${escapeHtml(num)}</strong>`;
    html += `<div style="margin:4px 0;font-size:.9rem">${escapeHtml(start)} ↔ ${escapeHtml(end)}</div>`;
    if (catLabel || stopCount) {
        html += `<div style="font-size:.82rem;color:#666;margin-top:2px">`;
        if (catLabel) html += `${catLabel}`;
        if (catLabel && stopCount) html += ` · `;
        if (stopCount) html += `${stopCount} arrêt${stopCount > 1 ? 's' : ''}`;
        html += `</div>`;
    }
    html += `</div>`;

    if (lineData.stops && lineData.stops.length > 0) {
        html += `<ul style="list-style:none;padding:0;margin:8px 0 0;display:flex;flex-direction:column;gap:2px;font-size:.88rem">`;
        lineData.stops.forEach((stop, i) => {
            const isFirst = i === 0;
            const isLast  = i === lineData.stops.length - 1;
            const marker  = isFirst ? '🔵' : isLast ? '🔴' : '–';
            html += `<li style="padding:2px 6px">${marker} ${escapeHtml(stop)}</li>`;
        });
        html += `</ul>`;
    }

    return html;
}

function cleanDescription(description) {
  if (!description) return '';
  
  // Supprimer les numéros de téléphone
  let clean = description.replace(/(?:\+221\s*)?\d{2}\s*\d{3}\s*\d{2}\s*\d{2}/g, '');
  
  // Supprimer les prix FCFA
  clean = clean.replace(/\d[\d\s,]*\s*FCFA/gi, '');
  
  // Supprimer les espaces multiples
  clean = clean.replace(/\s+/g, ' ').trim();
  
  // Raccourcir si trop long
  if (clean.length > 60) {
    clean = clean.substring(0, 57) + '...';
  }
  
  return clean;
}
    // Fonction pour formater les listes de prix
    function formatPriceList(text, targetCity) {
      let formatted = ''
      
      if (targetCity) {
        formatted += `<div class="city-header">📍 ${targetCity.toUpperCase()}</div>`
      }
      
      formatted += `<div class="section-title">💰 LISTE DES PRIX</div>`
      
      // Diviser par ville
      const lines = text.split(/[\/]/).filter(line => line.trim())
      
      for (let line of lines) {
        line = line.trim()
        if (!line) continue
        
        // Extraire le prix
        const priceMatch = line.match(/(\d[\d\s,]*)\s*FCFA/i)
        if (priceMatch) {
          const price = priceMatch[0]
          let formattedLine = line.replace(price, `<span class="price-tag">${price}</span>`)
          
          // Mettre en évidence la ville cible
          if (targetCity && line.toLowerCase().includes(targetCity.toLowerCase())) {
            formattedLine = `<strong>${formattedLine}</strong>`
          }
          
          formatted += `<div class="list-item">${formattedLine}</div>`
        } else if (line.length > 20) {
          formatted += `<div class="list-item">${line}</div>`
        }
      }
      
      return formatted
    }


// Focus automatique sur le champ de saisie
qin.focus()

// Soumission avec Enter
qin.addEventListener('keypress', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    form.dispatchEvent(new Event('submit'))
  }
})

// Ajoutez ici toutes les autres fonctions (formatCityInfo, formatBusLines, etc.)
// qui sont déjà dans votre code mais doivent être déplacées dans ce fichier