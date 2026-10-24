/* What Leaves With Them: the one-page demo over the honest-memory HTTP API.
 *
 * Flow: the company graph -> pick a person (what_leaves, depth 1-4) -> Remove (without, single_source, bridges)
 * -> Plan the handoff (handoff_plan, single_source before and after, promises) -> Show Cypher (trace of the last action).
 * Every number and name on screen comes from an API answer; the page never writes to the graph.
 */
'use strict';

(() => {
  // ---- configuration ------------------------------------------------------------------------------------------------
  // One-line switch: 'live' asks the API of this server; 'fixtures' reads answers recorded from it (web/fixtures/).
  const DATA = 'live';

  const query = new URLSearchParams(location.search);
  const SOURCE = ['live', 'fixtures'].includes(query.get('data')) ? query.get('data') : DATA;
  const TENANT = /^[A-Za-z0-9_-]{1,64}$/.test(query.get('tenant') || '') ? query.get('tenant') : 'quillfield';
  const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ms = (t) => (REDUCED ? 0 : t);
  const DAYS_PER_ANSWER = 3;        // what one answer costs on the countdown (a game rule, not data)
  const ASSUMED_NOTICE_DAYS = 14;   // when the graph records no last day for the person
  const STEP_MS = 170;              // one hop of the depth animation
  const MOVE_MS = 560;              // a lit node gliding into its step, or back home
  const ZONE_ORDER = ['Payments', 'Platform', 'Support', 'Growth'];
  const HIDDEN_EDGES = new Set(['MEMBER_OF']);  // membership is the zone itself
  const QUIET = new Set(['decision', 'project']);  // drawn only while they are part of a lit path or a result

  const $ = (id) => document.getElementById(id);
  const app = $('app');
  const css = getComputedStyle(document.documentElement);
  const token = (name) => css.getPropertyValue(name).trim();
  const C = {
    blue: token('--blue'), red: token('--red'), green: token('--green'), node: token('--node'),
    nodeInk: token('--node-ink'), ink: token('--ink'), muted: token('--muted'),
    zones: [token('--zone-1'), token('--zone-2'), token('--zone-3'), token('--zone-4')],
  };

  // ---- small helpers ------------------------------------------------------------------------------------------------
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const first = (name) => String(name).split(' ')[0];
  const initials = (name) => String(name).split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
  const plural = (n, one, many) => (n === 1 ? one : many);
  const day = (iso) => (iso ? String(iso).slice(0, 10) : '');
  const daysBetween = (a, b) => Math.round((Date.parse(b + 'T00:00:00Z') - Date.parse(a + 'T00:00:00Z')) / 86400000);
  const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const sleep = (t) => new Promise((r) => setTimeout(r, t));

  function mix(hexA, hexB, t) {
    const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const a = p(hexA), b = p(hexB);
    return '#' + a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, '0')).join('');
  }

  // ---- data: live API or recorded answers ---------------------------------------------------------------------------
  const api = {
    source: SOURCE,
    cache: new Map(),
    files: new Map(),
    schemas: null,

    key(name, args) {
      return [name, ...Object.keys(args).sort().map((k) => `${k}=${args[k]}`)].join('|');
    },

    async http(url, opts) {
      const t0 = performance.now();
      const res = await fetch(url, opts);
      let body = {};
      try { body = await res.json(); } catch (_) { body = {}; }
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      return { body, roundtrip: Math.round(performance.now() - t0) };
    },

    file(name) {
      if (!this.files.has(name)) {
        this.files.set(name, fetch(`fixtures/${name}`).then((r) => (r.ok ? r.json() : {})).catch(() => ({})));
      }
      return this.files.get(name);
    },

    async recorded(key, person) {
      // the recorded answers are Quillfield's (scripts/capture_panel_fixtures.py); another tenant has none
      if (TENANT !== 'quillfield') throw new Error(`no recorded answers for tenant ${TENANT}`);
      const book = await this.file(person ? `people/${slug(person)}.json` : 'common.json');
      if (!(key in book)) throw new Error(`no recorded answer for ${key}`);
      return { body: book[key], roundtrip: null };
    },

    // GET or POST against the API; on failure (or in fixtures mode) the recorded answer, if there is one.
    async get(key, person, live) {
      if (this.cache.has(key)) return this.cache.get(key);
      const p = (async () => {
        if (this.source === 'live') {
          try { return await live(); } catch (err) {
            const rec = await this.recorded(key, person).catch(() => null);
            if (!rec) throw err;
            this.fellBack = true;
            setStatus('The live API did not answer; showing recorded answers.');
            return rec;
          }
        }
        return this.recorded(key, person);
      })();
      this.cache.set(key, p);
      p.catch(() => this.cache.delete(key));
      return p;
    },

    graph() {
      return this.get('graph', null, () => this.http(`v1/${TENANT}/graph`));
    },

    async loadSchemas() {
      try {
        const { body } = await this.get('tools', null, () => this.http(`v1/${TENANT}/tools`));
        this.schemas = Object.fromEntries((body.tools || []).map((t) => [t.name, t]));
      } catch (_) { this.schemas = {}; }
    },

    accepts(tool, arg) {
      const t = this.schemas && this.schemas[tool];
      return Boolean(t && t.parameters && t.parameters.properties && arg in t.parameters.properties);
    },

    tool(name, args) {
      return this.get(this.key(name, args), args.person, () => this.http(`v1/${TENANT}/tools/${name}`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(args),
      }));
    },

    episode(id) {
      return this.get(`episode|${id}`, null, () => this.http(`v1/${TENANT}/episodes/${encodeURIComponent(id)}`));
    },

    confirmReceipts() {
      return this.get('receipts|confirm', null, () => this.http(`v1/${TENANT}/receipts?outcome=confirm&limit=200`));
    },
  };

  function setStatus(text) { $('status').textContent = text || ''; }

  // ---- the model: nodes, edges, zones -------------------------------------------------------------------------------
  const model = { nodes: new Map(), edges: [], teams: [], zoneOf: new Map(), home: new Map(), leaving: new Map(), graph: null };

  function buildModel(g) {
    model.graph = g;
    for (const n of g.nodes) model.nodes.set(n.id, { ...n });
    const teams = g.nodes.filter((n) => n.kind === 'team').map((n) => n.id);
    teams.sort((a, b) => {
      const ia = ZONE_ORDER.indexOf(a), ib = ZONE_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
    });
    model.teams = teams;
    teams.forEach((t, i) => model.zoneOf.set(t, i));
    model.edges = [...g.edges.map((e) => ({ ...e, ended: false })), ...g.ended.map((e) => ({ ...e, ended: true }))];
    for (const r of g.leaving || []) model.leaving.set(r.person, r);
  }

  const drawnNode = (n) => n.kind !== 'team';
  const drawnEdge = (e) => !HIDDEN_EDGES.has(e.type) && !(e.type === 'LEADS' && model.nodes.get(e.target)?.kind === 'team')
    && model.nodes.has(e.source) && model.nodes.has(e.target);
  const edgeId = (e) => `${e.type}|${e.source}|${e.target}`;

  // ---- layout: every team owns a corner, its nodes on a tidy grid ----------------------------------------------------
  // Payments top-left, Platform top-right, Support bottom-left, Growth bottom-right (the zone order). Inside a zone three
  // lanes: people, then decisions and projects (drawn only when lit), then systems, each ordered so an owner sits above
  // what they own. A decision that governs a system of another team sits in the gap between the two zones. The margins
  // outside the zones stay free for the systems that drift off when a person is removed. A narrow portrait screen gets
  // the same corners with the lanes running down instead of across. Nothing here is random: every load is the same.
  let W = 2000, H = 860, PORTRAIT = false;
  const regions = new Map();
  const extent = { x0: 0, y0: 0, x1: W, y1: H };

  function planRegions() {
    PORTRAIT = innerWidth <= 760 && innerHeight > innerWidth;
    [W, H] = PORTRAIT ? [1000, 1640] : [2000, 830];
    const MX = PORTRAIT ? 24 : 150, MY = PORTRAIT ? 10 : 4;
    const GX = PORTRAIT ? 90 : 170, GY = PORTRAIT ? 250 : 96;
    const L = MX, R = W - MX, T = MY, B = H - MY;
    const mid = (L + R) / 2, split = (T + B) / 2;
    const rects = [
      [L, T, mid - GX / 2, split - GY / 2], [mid + GX / 2, T, R, split - GY / 2],
      [L, split + GY / 2, mid - GX / 2, B], [mid + GX / 2, split + GY / 2, R, B],
    ];
    model.teams.forEach((t, i) => {
      if (i >= 4) return;
      const [x0, y0, x1, y1] = rects[i];
      regions.set(t, { x0, y0, x1, y1, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, col: i % 2, row: i >> 1 });
    });
    regions.set('', { cx: mid, cy: split, mx: MX, my: MY, gy: GY });
  }

  // Where a node belongs: in its team's zone, or between two zones when it is a decision that governs a system of
  // another team (that is the decision a person leaving takes across a team line).
  const places = new Map();

  function placeOf(n) {
    if (places.has(n.id)) return places.get(n.id);
    let place = { team: n.team, between: null };
    if (n.kind === 'decision' && n.team) {
      const other = model.edges.filter((e) => e.type === 'CONSTRAINS' && e.source === n.id)
        .map((e) => model.nodes.get(e.target)?.team).find((t) => t && t !== n.team);
      if (other) place = { team: null, between: [n.team, other] };
    }
    if (place.team && !regions.has(place.team)) place = { team: null, between: null };
    places.set(n.id, place);
    return place;
  }

  function zoneCenter(team) {
    const r = regions.get(team) || regions.get('');
    return [r.cx, r.cy];
  }

  // a lane of n nodes centred on `mid` along the main axis, at most `step` apart and inside [lo, hi]
  function spread(n, lo, hi, step) {
    const s = n > 1 ? Math.min(step, (hi - lo) / (n - 1)) : 0;
    const first = (lo + hi) / 2 - (s * (n - 1)) / 2;
    return Array.from({ length: n }, (_, i) => first + i * s);
  }

  function layout() {
    planRegions();
    const nodes = [...model.nodes.values()].filter(drawnNode);
    const firstOf = (type) => {
      const m = new Map();
      for (const e of model.edges) if (e.type === type && !m.has(e.target)) m.set(e.target, e.source);
      return m;
    };
    const owner = firstOf('OWNS'), decider = firstOf('DECIDED'), lead = firstOf('LEADS');
    const LABEL = PORTRAIT ? 70 : 62, IN = PORTRAIT ? 70 : 92;
    for (const team of model.teams) {
      const r = regions.get(team);
      if (!r) continue;
      const mine = nodes.filter((n) => placeOf(n).team === team);
      const people = mine.filter((n) => n.kind === 'person').sort((a, b) => a.id.localeCompare(b.id));
      const rank = new Map(people.map((p, i) => [p.id, i]));
      const by = (m) => (a, b) => (rank.get(m.get(a.id)) ?? 99) - (rank.get(m.get(b.id)) ?? 99) || a.id.localeCompare(b.id);
      const systems = mine.filter((n) => n.kind === 'system').sort(by(owner));
      const middle = [...mine.filter((n) => n.kind === 'decision').sort(by(decider)),
        ...mine.filter((n) => n.kind === 'project').sort(by(lead))];
      const lanes = [people, middle, systems];
      lanes.forEach((lane, k) => {
        // across: the lane's position in the zone; along: the nodes spread over the zone, a light zigzag for rhythm
        const across = PORTRAIT ? r.x0 + ((k + 0.5) * (r.x1 - r.x0)) / 3 : r.y0 + LABEL + ((k + 0.5) * (r.y1 - r.y0 - LABEL - 34)) / 3;
        const [lo, hi] = PORTRAIT ? [r.y0 + LABEL + IN, r.y1 - IN] : [r.x0 + IN, r.x1 - IN];
        const step = k === 1 ? 150 : PORTRAIT ? 150 : 190;
        const along = spread(lane.length, lo, hi, step);
        const zig = k === 1 && lane.length > 4 ? 26 : 9;
        lane.forEach((n, i) => {
          const a = along[i], b = across + (i % 2 ? zig : -zig);
          model.home.set(n.id, PORTRAIT ? { x: Math.round(b), y: Math.round(a) } : { x: Math.round(a), y: Math.round(b) });
        });
      });
      r.label = PORTRAIT ? { x: r.x0 + 34, y: r.y0 + 40, anchor: 'start' } : { x: r.x0 + (r.x1 - r.x0) * 0.2, y: r.y0 + 36, anchor: 'middle' };
    }
    // decisions between two zones: in the gap that separates them, side by side
    const gap = regions.get('');
    const groups = new Map();
    for (const n of nodes.filter((m) => !placeOf(m).team)) {
      const pair = placeOf(n).between;
      const key = pair ? [...pair].sort().join('|') : '';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(n);
    }
    for (const [key, list] of groups) {
      const [a, b] = key ? key.split('|').map((t) => regions.get(t)) : [];
      list.sort((p, q) => p.id.localeCompare(q.id));
      let cx = gap.cx, cy = gap.cy, dx = 160, dy = 0;
      if (a && b && a.row === b.row) {          // side by side: the vertical gap, at the height of their middle lanes
        cy = PORTRAIT ? (a.y0 + a.y1) / 2 : a.y0 + LABEL + (a.y1 - a.y0 - LABEL - 34) / 2;
        dx = 0; dy = PORTRAIT ? 150 : 96;
      } else if (a && b && a.col === b.col) {   // one above the other: the horizontal gap, under their middle
        cx = (a.x0 + a.x1) / 2;
        cy -= PORTRAIT ? 0 : 14;
        dx = PORTRAIT ? 130 : 180;
      }
      list.forEach((n, i) => {
        const o = i - (list.length - 1) / 2;
        model.home.set(n.id, { x: Math.round(cx + o * dx), y: Math.round(cy + o * dy) });
      });
    }
    // zones: a soft blob the size of the region (a superellipse with a slight wobble), fixed for the whole visit
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    model.teams.forEach((team, i) => {
      const r = regions.get(team);
      if (!r) return;
      r.ring = blob(r, i);
      r.hull = 'M' + r.ring.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L') + 'Z';
      for (const [x, y] of r.ring) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    });
    Object.assign(extent, { x0: Math.min(0, x0), y0: Math.min(0, y0 - 6), x1: Math.max(W, x1), y1: Math.max(H, y1) });
  }

  function blob(r, seed) {
    const cx = (r.x0 + r.x1) / 2, cy = (r.y0 + r.y1) / 2, a = (r.x1 - r.x0) / 2, b = (r.y1 - r.y0) / 2;
    const p = 2 / 3.4;  // the superellipse exponent: between an ellipse and a rounded rectangle
    const pts = [];
    for (let k = 0; k < 96; k++) {
      const t = (k / 96) * 2 * Math.PI, c = Math.cos(t), s = Math.sin(t);
      const w = 1 + 0.022 * Math.sin(3 * t + seed * 1.7) + 0.014 * Math.sin(5 * t + seed * 2.9);
      pts.push([cx + a * w * Math.sign(c) * Math.abs(c) ** p, cy + b * w * Math.sign(s) * Math.abs(s) ** p]);
    }
    return pts;
  }

  // ---- cytoscape ----------------------------------------------------------------------------------------------------
  let cy = null;

  function nodeData(n) {
    const zi = model.zoneOf.get(n.team);
    const zone = zi === undefined ? '#6b7591' : C.zones[zi % C.zones.length];
    return { id: n.id, kind: n.kind, initials: n.kind === 'person' ? initials(n.id) : '', zone,
      fill: zi === undefined ? C.node : mix(C.node, zone, 0.12), dim: n.kind === 'decision' && n.status === 'superseded' ? 1 : 0 };
  }

  // the few edges drawn at rest: who owns what, and what a system needs inside its own team
  function baseEdge(e) {
    if (e.ended) return false;
    if (e.type === 'OWNS') return true;
    return e.type === 'DEPENDS_ON' && model.nodes.get(e.source)?.team === model.nodes.get(e.target)?.team;
  }

  function styles() {
    const T = 'background-color, border-color, border-width, border-opacity, opacity, underlay-opacity, underlay-padding, line-color, width, height, color';
    const shown = { opacity: 1, events: 'yes' };
    return [
      { selector: 'node', style: {
        'background-color': 'data(fill)', 'border-width': 1.6, 'border-color': 'data(zone)', 'border-opacity': 0.5,
        label: 'data(initials)', color: C.nodeInk, 'font-size': 15, 'font-weight': 600, 'text-valign': 'center',
        'text-halign': 'center', 'font-family': token('--font'), 'underlay-color': C.blue, 'underlay-opacity': 0,
        'underlay-padding': 10, 'transition-property': T, 'transition-duration': ms(300), 'transition-timing-function': 'ease-out',
      } },
      { selector: 'node[kind = "person"]', style: { shape: 'ellipse', width: 56, height: 56, 'underlay-shape': 'ellipse' } },
      { selector: 'node[kind = "system"]', style: { shape: 'round-rectangle', width: 50, height: 50, 'underlay-shape': 'round-rectangle' } },
      { selector: 'node[kind = "decision"]', style: { shape: 'round-diamond', width: 56, height: 56, 'underlay-shape': 'ellipse' } },
      { selector: 'node[kind = "project"]', style: { shape: 'round-hexagon', width: 32, height: 32, 'underlay-shape': 'ellipse' } },
      { selector: 'node[dim = 1]', style: { 'border-style': 'dashed' } },
      { selector: 'node.soft', style: { opacity: 0.72 } },
      { selector: 'node.faded', style: { opacity: 0.2 } },
      { selector: 'node.under', style: { opacity: 0.03, events: 'no' } },
      { selector: 'node.quiet', style: { opacity: 0, events: 'no' } },
      { selector: 'node.tail', style: { ...shown, opacity: 0.6, 'border-color': C.blue, 'border-opacity': 0.8, 'border-width': 2 } },
      { selector: 'node.lit', style: { ...shown, 'border-color': '#5aaee6', 'border-opacity': 1, 'border-width': 2.8, 'background-color': '#103558',
        'underlay-color': C.blue, 'underlay-opacity': 0.26, 'underlay-padding': 12, color: '#d6e9f8' } },
      { selector: 'node.pick', style: { ...shown, width: 64, height: 64, 'border-color': '#7cc4f2', 'border-width': 3, 'background-color': '#0f2c4f',
        'underlay-color': C.blue, 'underlay-opacity': 0.34, 'underlay-padding': 15, color: '#ffffff', 'font-size': 17 } },
      { selector: 'node.ghost', style: { ...shown, 'background-opacity': 0, 'border-style': 'dashed', 'border-color': '#9aa4bd',
        'border-opacity': 0.9, 'border-width': 2, label: '', 'underlay-opacity': 0 } },
      { selector: 'node.island', style: { ...shown, 'border-color': C.red, 'border-opacity': 1, 'border-width': 2.6, 'background-color': '#3a1219',
        'underlay-color': C.red, 'underlay-opacity': 0.3, 'underlay-padding': 14, color: '#ffd1d2' } },
      { selector: 'node.single', style: { ...shown, 'border-color': C.red, 'border-opacity': 1, 'border-width': 2.6, 'background-color': '#2e1218',
        'underlay-color': C.red, 'underlay-opacity': 0.2 } },
      { selector: 'node.saved', style: { ...shown, 'border-color': C.green, 'border-opacity': 1, 'border-width': 2.6, 'background-color': '#0f3326',
        'underlay-color': C.green, 'underlay-opacity': 0.28, 'underlay-padding': 13, color: '#d9f7e8' } },
      { selector: 'node.succ', style: { ...shown, 'border-color': C.green, 'border-opacity': 1, 'border-width': 2.6,
        'underlay-color': C.green, 'underlay-opacity': 0.22, color: '#d9f7e8' } },
      { selector: 'edge', style: {
        width: 1.2, 'line-color': '#8f9cc0', opacity: 0, events: 'no', 'curve-style': 'straight', 'arrow-scale': 0.9,
        'target-arrow-color': C.blue, 'source-arrow-color': C.blue,
        'transition-property': T + ', source-distance-from-node, target-distance-from-node', 'transition-duration': ms(300),
      } },
      { selector: 'edge[base = 1]', style: { opacity: 0.17, events: 'yes' } },
      { selector: 'edge[base = 1].soft', style: { opacity: 0.13 } },
      { selector: 'edge[base = 1].faded', style: { opacity: 0.035, events: 'no' } },
      { selector: 'edge.snap', style: { 'line-color': C.red, 'line-style': 'dashed', 'line-dash-pattern': [8, 6], width: 2.2,
        opacity: 0.9, events: 'yes', 'z-index': 10 } },
      { selector: 'edge.snap.after', style: { opacity: 0.25 } },
      { selector: 'edge.snap-s', style: { 'source-distance-from-node': 18 } },
      { selector: 'edge.snap-t', style: { 'target-distance-from-node': 18 } },
      { selector: 'edge.handover', style: { 'line-color': C.green, 'line-style': 'dashed', 'line-dash-pattern': [8, 6], width: 2.2,
        opacity: 0.95, events: 'yes', 'z-index': 11 } },
    ];
  }

  function initCy() {
    const elements = [];
    for (const n of model.nodes.values()) {
      if (!drawnNode(n)) continue;
      elements.push({ group: 'nodes', data: nodeData(n), position: { ...model.home.get(n.id) }, classes: QUIET.has(n.kind) ? 'quiet' : '' });
    }
    for (const e of model.edges.filter(drawnEdge)) {
      elements.push({ group: 'edges', data: { id: edgeId(e), source: e.source, target: e.target, type: e.type, base: baseEdge(e) ? 1 : 0 } });
    }
    cy = cytoscape({
      container: $('cy'), elements, layout: { name: 'preset' }, style: styles(),
      userZoomingEnabled: false, userPanningEnabled: false, boxSelectionEnabled: false,
      autoungrabify: true, autounselectify: true, minZoom: 0.05, maxZoom: 4,
    });
    cy.on('render', scheduleSync);
    cy.on('tap', 'node', (ev) => onTapNode(ev.target));
    cy.on('tap', (ev) => { if (ev.target === cy) onTapBackground(); });
    cy.on('mouseover', 'node, edge', (ev) => { showTip(ev.target, ev.originalEvent); hoverName(ev.target, true); });
    cy.on('mousemove', 'node, edge', (ev) => moveTip(ev.originalEvent));
    cy.on('mouseout', 'node, edge', (ev) => { hideTip(); hoverName(ev.target, false); });
  }

  // a person's full name under the circle while the pointer is on it
  let hovered = null;
  function hoverName(ele, on) {
    if (!ele.isNode() || model.nodes.get(ele.id())?.kind !== 'person') return;
    hovered = on ? ele.id() : hovered === ele.id() ? null : hovered;
    scheduleSync();
  }

  // ---- fitting the drawing into the free part of the screen ---------------------------------------------------------
  function freeRect() {
    const w = innerWidth, h = innerHeight;
    const narrow = w <= 760;
    const top = narrow ? (['removing', 'removed', 'done'].includes(state.phase) ? 178 : 110) : Math.max(96, h * 0.115);
    let bottom = narrow ? h - 120 : h - Math.max(120, h * 0.16);
    let right = w - 16;
    if (state.phase === 'game') {
      if (narrow) bottom = h * 0.48 - 8; else right = w - $('game').getBoundingClientRect().width - 24;
    }
    return { x: 16, y: top, w: Math.max(100, right - 16), h: Math.max(100, bottom - top) };
  }

  function fit(animate, focus) {
    // the whole drawing (zones and the free margins), or (in the game) the part round the points in play
    let b = { ...extent };
    let full = null;
    if (focus && focus.length) {
      const pts = focus;
      const m = 130;
      b = { x0: Math.min(...pts.map((p) => p.x)) - m, x1: Math.max(...pts.map((p) => p.x)) + m,
        y0: Math.min(...pts.map((p) => p.y)) - m, y1: Math.max(...pts.map((p) => p.y)) + m - 10 };
      full = extent;
    }
    hovered = null;  // the camera moves the nodes away from a resting pointer: no name or tooltip left behind
    hideTip();
    const r = freeRect();
    const whole = full ? Math.min(r.w / (full.x1 - full.x0), r.h / (full.y1 - full.y0)) : Infinity;
    const zoom = Math.min(r.w / (b.x1 - b.x0), r.h / (b.y1 - b.y0), whole * 1.6);
    const pan = { x: r.x + r.w / 2 - zoom * (b.x0 + b.x1) / 2, y: r.y + r.h / 2 - zoom * (b.y0 + b.y1) / 2 };
    if (animate && !REDUCED) cy.animate({ zoom, pan }, { duration: 500, easing: 'ease-in-out-cubic' });
    else cy.viewport({ zoom, pan });
  }

  // ---- overlay: zones, labels, the Remove button, links -------------------------------------------------------------
  let syncQueued = false;
  function scheduleSync() {
    if (syncQueued) return;
    syncQueued = true;
    requestAnimationFrame(() => { syncQueued = false; sync(); });
  }

  function hull(points) {
    const p = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    if (p.length < 3) return p;
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lower = [], upper = [];
    for (const q of p) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop(); lower.push(q); }
    for (const q of p.reverse()) { while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop(); upper.push(q); }
    return lower.slice(0, -1).concat(upper.slice(0, -1));
  }

  function softHull(points, r, round = 0.2) {
    // the hull of the points grown by r, corners smoothed and pulled a little towards an ellipse: a soft blob
    const ring = [];
    for (const q of points) for (let k = 0; k < 20; k++) ring.push([q[0] + Math.cos(k * Math.PI / 10) * r * 1.08, q[1] + Math.sin(k * Math.PI / 10) * r]);
    let h = hull(ring);
    for (let it = 0; it < 3; it++) {
      const next = [];
      for (let i = 0; i < h.length; i++) {
        const a = h[i], b = h[(i + 1) % h.length];
        next.push([a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25], [a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75]);
      }
      h = next;
    }
    const xs = h.map((q) => q[0]), ys = h.map((q) => q[1]);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const rx = (Math.max(...xs) - Math.min(...xs)) / 2, ry = (Math.max(...ys) - Math.min(...ys)) / 2;
    h = h.map(([x, y]) => {
      const a = Math.atan2((y - cy) / (ry || 1), (x - cx) / (rx || 1));
      const ex = cx + Math.cos(a) * rx, ey = cy + Math.sin(a) * ry;
      return [x * (1 - round) + ex * round, y * (1 - round) + ey * round];
    });
    return 'M' + h.map((q) => `${q[0].toFixed(1)},${q[1].toFixed(1)}`).join(' L') + 'Z';
  }

  const NS = 'http://www.w3.org/2000/svg';
  const zoneEls = new Map();
  const zoneAnchor = new Map();

  function syncZones() {
    // the hulls are fixed in layout space; only the viewport transform follows the camera
    const svg = $('zones');
    const z = cy.zoom(), pan = cy.pan();
    const tf = `translate(${pan.x.toFixed(2)} ${pan.y.toFixed(2)}) scale(${z.toFixed(5)})`;
    for (const team of model.teams) {
      const r = regions.get(team);
      if (!r || !r.hull) continue;
      const color = C.zones[model.zoneOf.get(team) % C.zones.length];
      let g = zoneEls.get(team);
      if (!g) {
        g = document.createElementNS(NS, 'g');
        g.setAttribute('class', 'zone');
        g.innerHTML = '<path vector-effect="non-scaling-stroke"/><text/>';
        g.querySelector('path').setAttribute('d', r.hull);
        g.querySelector('text').textContent = team;
        svg.appendChild(g);
        zoneEls.set(team, g);
      }
      const path = g.querySelector('path'), text = g.querySelector('text');
      path.setAttribute('transform', tf);
      const lit = g.classList.contains('lit'), hint = g.classList.contains('reached'), red = g.classList.contains('red');
      path.setAttribute('fill', color);
      path.setAttribute('fill-opacity', lit ? '0.17' : '0.085');
      path.setAttribute('stroke', red ? C.red : lit || hint ? C.blue : color);
      path.setAttribute('stroke-opacity', red ? '0.75' : lit ? '0.65' : hint ? '0.42' : '0.3');
      text.setAttribute('x', (pan.x + z * r.label.x).toFixed(1));
      text.setAttribute('y', (pan.y + z * r.label.y + 6).toFixed(1));
      text.setAttribute('text-anchor', r.label.anchor);
      text.setAttribute('fill', color);
      const edge = r.ring.map(([x, y]) => [pan.x + z * x, pan.y + z * y]);
      zoneAnchor.set(team, { x: pan.x + z * r.cx, y: pan.y + z * r.cy, edge });
    }
  }

  const labelEls = new Map();

  function wantedLabels() {
    // node id -> class ('', 'red', 'green', 'hover'); people show initials inside, the full name below when relevant
    const out = new Map();
    cy.nodes().forEach((n) => {
      let cls = null;
      if (n.hasClass('island') || n.hasClass('single')) cls = 'red';
      else if (n.hasClass('saved') || n.hasClass('succ')) cls = 'green';
      else if (n.hasClass('lit') || n.hasClass('pick')) cls = '';
      if (n.hasClass('ghost')) cls = null;
      if (cls !== null) out.set(n.id(), cls);
    });
    if (hovered && !out.has(hovered) && !cy.getElementById(hovered).hasClass('ghost')) out.set(hovered, 'hover');
    return out;
  }

  function syncLabels() {
    const box = $('labels');
    const want = wantedLabels();
    for (const [id, el] of labelEls) {
      if (!want.has(id)) { el.remove(); labelEls.delete(id); }
    }
    const z = cy.zoom();
    for (const [id, cls] of want) {
      const n = cy.getElementById(id);
      let el = labelEls.get(id);
      if (!el) {
        el = document.createElement('div');
        el.textContent = id;
        box.appendChild(el);
        labelEls.set(id, el);
      }
      el.className = 'nlabel' + (cls ? ' ' + cls : '') + (n.hasClass('pick') ? ' pick' : '');
      const p = n.renderedPosition();
      el.style.left = `${p.x.toFixed(1)}px`;
      el.style.top = `${(p.y + (n.height() / 2) * z + 7 + (n.hasClass('pick') ? 4 : 0)).toFixed(1)}px`;
    }
  }

  function syncPicked() {
    // below the name as in the frames; beside or above the person when a node sits there
    const box = $('picked');
    if (box.hidden || !state.person) return;
    const n = cy.getElementById(state.person);
    const p = n.renderedPosition(), r = (n.height() / 2) * cy.zoom();
    const bw = box.offsetWidth || 90, bh = box.offsetHeight || 28;
    const spots = [[p.x, p.y + r + 40], [p.x - r - 16 - bw / 2, p.y - bh / 2], [p.x, p.y - r - bh - 12], [p.x + r + 16 + bw / 2, p.y - bh / 2]];
    const others = cy.nodes().not(n).filter((m) => m.style('opacity') > 0.1).map((m) => m.renderedBoundingBox());
    const clear = ([x, y]) => others.every((b) => x + bw / 2 < b.x1 - 2 || x - bw / 2 > b.x2 + 2 || y + bh < b.y1 - 2 || y > b.y2 + 2);
    const [x, y] = spots.find(clear) || spots[0];
    box.style.left = `${x}px`;
    box.style.top = `${y}px`;
  }

  function sync() {
    if (!cy) return;
    syncZones();
    syncLabels();
    syncPicked();
    syncFlows();
    syncLinks();
  }

  // the lit path, drawn in the overlay: smooth curves from step to step with an arrowhead, so the person's reach reads
  // left to right (top to bottom on a narrow portrait screen); each new hop draws itself in when the depth grows
  const flowEls = new Map();

  function overlayLayer(cls) {
    const svg = $('overlay');
    let g = svg.querySelector(`:scope > g.${cls}`);
    if (!g) { g = document.createElementNS(NS, 'g'); g.setAttribute('class', cls); svg.appendChild(g); }
    return g;
  }

  function flowGeometry(a, b) {
    const z = cy.zoom();
    const p = a.renderedPosition(), q = b.renderedPosition();
    const ra = (a.width() / 2 + 2) * z + 2, rb = (b.width() / 2 + 2) * z + 3;
    const AR = Math.max(8, 13 * Math.min(1.1, z + 0.15)), AW = AR * 0.48;
    const f = (v) => v.toFixed(1);
    const along = PORTRAIT ? q.y - p.y : q.x - p.x;
    if (along > ra + rb + AR + 12) {
      // leave the node towards the target (a fan, at most ~50 degrees off the reading direction), arrive level
      if (!PORTRAIT) {
        const t = Math.max(-0.85, Math.min(0.85, Math.atan2(q.y - p.y, q.x - p.x)));
        const sx = p.x + Math.cos(t) * ra, sy = p.y + Math.sin(t) * ra, ex = q.x - rb, lx = ex - AR, k = (lx - sx) * 0.5;
        return { line: `M${f(sx)},${f(sy)} C${f(sx + Math.cos(t) * k)},${f(sy + Math.sin(t) * k)} ${f(lx - k)},${f(q.y)} ${f(lx)},${f(q.y)}`,
          head: `M${f(ex)},${f(q.y)} L${f(lx)},${f(q.y - AW)} L${f(lx)},${f(q.y + AW)}Z` };
      }
      const t = Math.max(-0.85, Math.min(0.85, Math.atan2(q.x - p.x, q.y - p.y)));
      const sx = p.x + Math.sin(t) * ra, sy = p.y + Math.cos(t) * ra, ey = q.y - rb, ly = ey - AR, k = (ly - sy) * 0.5;
      return { line: `M${f(sx)},${f(sy)} C${f(sx + Math.sin(t) * k)},${f(sy + Math.cos(t) * k)} ${f(q.x)},${f(ly - k)} ${f(q.x)},${f(ly)}`,
        head: `M${f(q.x)},${f(ey)} L${f(q.x - AW)},${f(ly)} L${f(q.x + AW)},${f(ly)}Z` };
    }
    const dx = q.x - p.x, dy = q.y - p.y, L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L;
    const sx = p.x + ux * ra, sy = p.y + uy * ra, ex = q.x - ux * rb, ey = q.y - uy * rb, lx = ex - ux * AR, ly = ey - uy * AR;
    return { line: `M${f(sx)},${f(sy)} L${f(lx)},${f(ly)}`,
      head: `M${f(ex)},${f(ey)} L${f(lx - uy * AW)},${f(ly + ux * AW)} L${f(lx + uy * AW)},${f(ly - ux * AW)}Z` };
  }

  function syncFlows() {
    const host = overlayLayer('flows');
    const want = state.flows;
    for (const [k, el] of flowEls) if (!want.has(k)) { el.remove(); flowEls.delete(k); }
    for (const [k, fl] of want) {
      const a = cy.getElementById(fl.a), b = cy.getElementById(fl.b);
      if (!a.length || !b.length) continue;
      let el = flowEls.get(k);
      if (!el) {
        el = document.createElementNS(NS, 'g');
        el.setAttribute('class', 'flow');
        el.dataset.edge = fl.edge;
        el.style.setProperty('--delay', `${ms(fl.delay)}ms`);
        el.innerHTML = '<path class="glow"/><path class="line" pathLength="1"/><path class="head"/><path class="hit"/>';
        host.appendChild(el);
        flowEls.set(k, el);
      }
      const g = flowGeometry(a, b);
      for (const c of ['glow', 'line', 'hit']) el.querySelector(`.${c}`).setAttribute('d', g.line);
      el.querySelector('.head').setAttribute('d', g.head);
    }
  }

  // links drawn in the overlay: the bridge a person was the only one to hold, and the card-to-node line of the game
  const links = { bridges: [], card: null };

  function syncLinks() {
    const layer = overlayLayer('marks');
    let html = '';
    for (const [a, b] of links.bridges) {
      const za = zoneAnchor.get(a), zb = zoneAnchor.get(b);
      if (!za || !zb) continue;
      const near = (z, t) => z.edge.reduce((m, q) => (Math.hypot(q[0] - t.x, q[1] - t.y) < Math.hypot(m[0] - t.x, m[1] - t.y) ? q : m), z.edge[0]);
      const p = near(za, zb), q = near(zb, za);
      const mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2;
      const ux = (q[0] - p[0]) / (Math.hypot(q[0] - p[0], q[1] - p[1]) || 1), uy = (q[1] - p[1]) / (Math.hypot(q[0] - p[0], q[1] - p[1]) || 1);
      const g = 14;
      html += `<path d="M${p[0]},${p[1]} L${mx - ux * g},${my - uy * g} M${mx + ux * g},${my + uy * g} L${q[0]},${q[1]}" fill="none" stroke="${C.red}" stroke-width="2.2" stroke-dasharray="8 6" stroke-opacity="0.85"/>`
        + `<circle cx="${p[0]}" cy="${p[1]}" r="4.5" fill="${C.red}"/><circle cx="${q[0]}" cy="${q[1]}" r="4.5" fill="${C.red}"/>`
        + `<path d="M${mx - uy * 9 - ux * 4},${my + ux * 9 - uy * 4} L${mx + uy * 9 - ux * 4},${my - ux * 9 - uy * 4} M${mx - uy * 9 + ux * 4},${my + ux * 9 + uy * 4} L${mx + uy * 9 + ux * 4},${my - ux * 9 + uy * 4}" stroke="${C.red}" stroke-width="2.2" stroke-linecap="round"/>`;
    }
    if (links.card) {
      const card = links.card.el.getBoundingClientRect();
      const host = app.getBoundingClientRect();
      const n = cy.getElementById(links.card.node);
      if (card.width && n.length) {
        const p = n.renderedPosition();
        const x1 = card.left - host.left, y1 = card.top - host.top + card.height / 2;
        const r = (n.width() / 2) * cy.zoom() + 6;
        const x2 = p.x + r, y2 = p.y;
        const dx = Math.max(60, (x1 - x2) * 0.5);
        html += `<path class="draw" d="M${x2},${y2} C${x2 + dx},${y2} ${x1 - dx},${y1} ${x1},${y1}" fill="none" stroke="${C.green}" stroke-width="2.5"/>`
          + `<circle cx="${x2}" cy="${y2}" r="5" fill="${C.green}"/><circle cx="${x1}" cy="${y1}" r="5" fill="${C.green}"/>`;
      }
    }
    layer.innerHTML = html;
  }

  // ---- tooltip ------------------------------------------------------------------------------------------------------
  const hoverInfo = new Map();  // element id -> extra lines for the current state

  function describeNode(id) {
    const n = model.nodes.get(id);
    if (!n) return '';
    const lines = [];
    const team = n.team ? ` <span class="k">· ${esc(n.team)}</span>` : '';
    if (n.kind === 'person') {
      lines.push(`<div><b>${esc(id)}</b>${team}</div>`);
      const leave = model.leaving.get(id);
      if (leave) lines.push(`<div class="k">Resigned ${esc(day(leave.notice_at))}, last day ${esc(leave.last_day)} <span class="ids">${esc(leave.notice_source || '')}</span></div>`);
    } else if (n.kind === 'system') {
      const kind = n.system_kind && n.system_kind !== 'service' ? ` <span class="k">${esc(n.system_kind)}</span>` : '';
      lines.push(`<div><b>${esc(id)}</b>${kind}${team}</div>`);
      if (n.purpose) lines.push(`<div class="k">${esc(n.purpose)}</div>`);
    } else if (n.kind === 'decision') {
      lines.push(`<div><b>${esc(id)}</b> <span class="k">${esc(n.status || '')}</span></div>`);
      if (n.title) lines.push(`<div class="k">${esc(n.title)}</div>`);
    } else if (n.kind === 'project') {
      lines.push(`<div><b>${esc(id)}</b> <span class="k">project</span>${team}</div>`);
      if (n.goal) lines.push(`<div class="k">${esc(n.goal)}</div>`);
    }
    return lines.join('');
  }

  function describeEdge(id) {
    const [type, source, target] = id.split('|');
    const e = model.edges.find((x) => edgeId(x) === id);
    if (!e) return '';
    const ids = e.source_ids.slice(0, 5).join(', ') + (e.source_ids.length > 5 ? ` +${e.source_ids.length - 5}` : '');
    const ended = e.ended ? `<div class="k">ended ${esc(day(e.until))} <span class="ids">${esc((e.ended_by || []).join(', '))}</span></div>` : '';
    return `<div><b>${esc(source)}</b> <span class="k">${esc(type)}</span> <b>${esc(target)}</b></div>`
      + `<div class="k">since ${esc(day(e.since))}${e.count ? ` · ${e.count} records` : ''}</div>`
      + (ids ? `<div class="ids">${esc(ids)}</div>` : '') + ended;
  }

  function showTip(ele, ev) {
    const id = ele.id();
    const base = ele.isNode() ? describeNode(id) : describeEdge(id);
    const extra = hoverInfo.get(id) || '';
    if (!base && !extra) return;
    const tip = $('tip');
    tip.innerHTML = base + extra;
    tip.hidden = false;
    moveTip(ev);
  }

  function moveTip(ev) {
    const tip = $('tip');
    if (tip.hidden || !ev) return;
    const x = ev.clientX, y = ev.clientY;
    const r = tip.getBoundingClientRect();
    tip.style.left = `${Math.min(x + 14, innerWidth - r.width - 8)}px`;
    tip.style.top = `${y + 18 + r.height > innerHeight ? y - r.height - 12 : y + 18}px`;
  }

  function hideTip() { $('tip').hidden = true; }

  function htmlTip(target, html) {
    target.addEventListener('mouseenter', (ev) => { const tip = $('tip'); tip.innerHTML = html(); tip.hidden = false; moveTip(ev); });
    target.addEventListener('mousemove', moveTip);
    target.addEventListener('mouseleave', hideTip);
  }

  // ---- Show Cypher --------------------------------------------------------------------------------------------------
  let lastAction = { title: 'Loading the company graph', calls: [] };

  function record(title, calls) {
    lastAction = { title, calls };
    if (!$('cypher').hidden) renderCypher();
  }

  function renderCypher() {
    $('cypher-title').textContent = lastAction.title;
    const body = $('cypher-body');
    if (!lastAction.calls.length) { body.innerHTML = '<p class="call none">No query yet.</p>'; return; }
    body.innerHTML = lastAction.calls.map((c) => {
      const head = `<h3>${esc(c.name)}<small>${esc(c.meta)}</small></h3>`;
      const qs = c.queries.map((q) => `<pre>${q.ms !== undefined ? `<span class="ms">${esc(q.ms)} ms</span>` : ''}${esc(q.text)}</pre>`).join('');
      return `<div class="call">${head}${qs || '<p class="none">No query ran.</p>'}</div>`;
    }).join('');
  }

  function toolCall(name, args, res) {
    const a = res.body;
    const tr = (a.trace || [])[0] || {};
    const argText = Object.entries(args).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(', ');
    const meta = [tr.ms !== undefined ? `${tr.ms} ms in FalkorDB` : null, `${(a.rows || []).length} rows`,
      a.outcome, res.roundtrip !== null && res.roundtrip !== undefined ? `${res.roundtrip} ms round trip` : 'recorded'].filter(Boolean).join(' · ');
    return { name: `${name}(${argText})`, meta, queries: (a.trace || []).flatMap((t) => (t.queries || []).map((q) => ({ text: q }))) };
  }

  // ---- state --------------------------------------------------------------------------------------------------------
  const state = { phase: 'loading', person: null, depth: 1, shown: 0, seq: 0, lit: null, removed: null, game: null, flows: new Map() };

  function setPhase(p) { state.phase = p; app.dataset.phase = p; }

  function clearClasses() {
    cy.elements().removeClass('faded soft under lit tail pick ghost island single saved succ snap snap-s snap-t after');
    cy.edges('.handover').remove();
    $('zones').querySelectorAll('.zone').forEach((g) => g.classList.remove('lit', 'red', 'reached'));
    hoverInfo.clear();
    links.bridges = [];
    links.card = null;
    state.flows = new Map();
  }

  // glide a node to a spot (after `wait` ms), replacing any glide it is in; its own animation object, so the style
  // transitions running on the node (a glow coming on) are left alone
  function moveTo(n, to, { wait = 0, duration = MOVE_MS, easing = 'ease-in-out-cubic', animate = true } = {}) {
    const prev = n.scratch('glide');
    if (prev) { prev.stop(); n.removeScratch('glide'); }
    clearTimeout(n.scratch('glideT'));
    const go = () => {
      const p = n.position();
      if (Math.abs(p.x - to.x) < 0.5 && Math.abs(p.y - to.y) < 0.5) return;
      if (!animate || REDUCED) { n.position(to); return; }
      const a = n.animation({ position: { x: to.x, y: to.y }, duration, easing });
      n.scratch('glide', a);
      a.play();
    };
    if (wait && animate && !REDUCED) n.scratch('glideT', setTimeout(go, wait)); else go();
  }

  // send every node (but those in `keep`) back to where it lives
  function goHome(animate, keep = new Set()) {
    cy.nodes().forEach((n) => { if (!keep.has(n.id())) moveTo(n, model.home.get(n.id()), { animate }); });
  }

  function show(id, on) { $(id).hidden = !on; }

  function toLanding(animate = true) {
    state.seq++;
    state.person = null; state.lit = null; state.removed = null; state.game = null;
    clearClasses();
    cy.nodes().removeClass('drift');
    goHome(animate);
    setPhase('landing');
    $('headline').textContent = 'Pick someone. See what leaves with them.';
    for (const id of ['headline']) show(id, true);
    for (const id of ['counters', 'picked', 'depth', 'actions', 'game', 'handoff', 'promise', 'restart']) show(id, false);
    fit(animate);
    scheduleSync();
  }

  // ---- 1-2. pick a person, walk the depth -------------------------------------------------------------------------
  async function pick(person) {
    if (state.phase !== 'landing' && state.phase !== 'picked') { toLanding(false); }
    state.person = person; state.depth = 1; state.shown = 0; state.lit = null;
    setPhase('picked');
    clearClasses();
    goHome(true, new Set([person]));
    cy.elements().addClass('faded');
    cy.getElementById(person).removeClass('faded').addClass('pick');
    show('picked', true); show('depth', true);
    $('cap-1').textContent = `what ${first(person)} owns`;
    $('cap-4').textContent = `what depends on ${first(person)}`;
    setSlider(1);
    $('slider').classList.remove('hint');
    scheduleSync();
    await setDepth(1);
    if (state.person === person && state.depth === 1) $('slider').classList.add('hint');
  }

  function segmentsOf(answer, person) {
    // one entry per hop of every path: [from, edge type, to] with its step along the path and whether it is the tail
    const rows = (answer.rows || []).filter((r) => r.kind !== 'summary');
    const out = [];
    (answer.paths || []).forEach((path, i) => {
      const row = rows[i] || {};
      if (path[0] !== person) return;
      for (let k = 1; 2 * k < path.length; k++) {
        out.push({ a: path[2 * k - 2], rel: path[2 * k - 1], b: path[2 * k], step: k, tail: row.hops ? k > row.hops : false, row });
      }
    });
    return out;
  }

  function resolveSegment(s) {
    // the drawn edge for one hop, and the direction it is walked in; a team is a zone, not an edge
    const back = s.rel.startsWith('<-');
    const type = back ? s.rel.slice(2) : s.rel;
    const [from, to] = back ? [s.b, s.a] : [s.a, s.b];
    if (model.nodes.get(to)?.kind === 'team' && (type === 'MEMBER_OF' || type === 'LEADS' || type === 'WAS')) return { zone: to };
    let e = cy.getElementById(`${type}|${from}|${to}`);
    if (!e.length && type === 'WAS') {
      e = cy.edges().filter((x) => x.source().id() === from && x.target().id() === to);
    }
    if (!e.length) return null;
    return { edge: e[0], dir: back ? 'back' : 'fwd' };
  }

  function rowNote(row) {
    if (!row) return '';
    const ids = (row.source_ids || []).slice(0, 5);
    const what = row.why || (row.relation ? `${row.relation.toLowerCase()} ${row.item}${row.state === 'ended' ? ` (ended ${day(row.until)})` : ''}` : '');
    return `<div class="k">${esc(what)}${row.hops ? ` · ${row.hops} ${plural(row.hops, 'hop', 'hops')}` : ''}</div>`
      + (ids.length ? `<div class="ids">${esc(ids.join(', '))}</div>` : '');
  }

  async function setDepth(d) {
    const person = state.person;
    const my = ++state.seq;
    state.depth = d;
    setSlider(d);
    // the deepest answer fixes where every step goes, so moving the slider adds hops without reshuffling the drawn ones
    let res, frame;
    try {
      [res, frame] = await Promise.all([api.tool('what_leaves', { person, depth: d }),
        d === 4 ? null : api.tool('what_leaves', { person, depth: 4 }).catch(() => null)]);
    } catch (err) { setStatus(err.message); return; }
    if (my !== state.seq || state.person !== person) return;
    record(`what_leaves(${person}, depth ${d})`, [toolCall('what_leaves', { person, depth: d }, res)]);
    applyDepth(res.body, person, d, (frame || res).body);
  }

  // the hops of an answer: the nodes it lights (and the owners at its tail), the lines between them, the zones it reaches
  function collect(answer, person, prev) {
    const items = new Map();    // node id -> {ele, cls ('lit' or 'tail'), step, parents, delay}
    const flows = new Map();    // 'a>b' -> one hop of a lit path, drawn in the overlay
    const zones = new Set();
    const reached = new Set();
    const notes = new Map();
    for (const s of segmentsOf(answer, person)) {
      const r = resolveSegment(s);
      if (!r) continue;
      const delay = Math.max(0, s.step - prev - 1) * STEP_MS;
      if (r.zone) { (s.tail ? reached : zones).add(r.zone); continue; }
      const note = rowNote(s.row);
      for (const id of [r.edge.id(), s.b]) {
        if (!notes.has(id)) notes.set(id, new Set());
        notes.get(id).add(note);
      }
      if (s.b === person) continue;
      const it = items.get(s.b);
      if (s.tail) {  // the owners an affected system leans on: marked where they sit, no line
        if (!it) items.set(s.b, { ele: cy.getElementById(s.b), cls: 'tail', step: s.step, parents: new Set(), delay });
        continue;
      }
      if (!it || it.cls === 'tail') items.set(s.b, { ele: cy.getElementById(s.b), cls: 'lit', step: s.step, parents: new Set([s.a]), delay: delay + 60 });
      else if (it.step === s.step) it.parents.add(s.a);
      const key = `${s.a}>${s.b}`;
      if (!flows.has(key)) flows.set(key, { a: s.a, b: s.b, edge: r.edge.id(), step: s.step, delay });
    }
    return { items, flows, zones, reached, notes };
  }

  function applyDepth(answer, person, d, frameAnswer) {
    const prev = state.shown;
    const { items, flows, zones, reached, notes } = collect(answer, person, prev);
    const lit = new Map([...items].filter(([, w]) => w.cls === 'lit'));
    const frame = new Map([...collect(frameAnswer || answer, person, 0).items].filter(([, w]) => w.cls === 'lit'));
    for (const [id, w] of lit) if (!frame.has(id) || frame.get(id).step > w.step) frame.set(id, w);
    const all = stepTargets(person, frame);
    const targets = new Map([...all].filter(([id]) => id === person || lit.has(id)));
    const wasLit = new Set(cy.nodes('.lit').map((n) => n.id()));

    // drop what is no longer lit; dim the nodes a lit one would sit on
    cy.nodes('.lit, .tail').forEach((n) => {
      const w = items.get(n.id());
      if (!w || !n.hasClass(w.cls)) n.removeClass('lit tail').addClass('faded');
    });
    cy.nodes('.under').removeClass('under');
    const spots = [...targets.values()];
    cy.nodes().forEach((n) => {
      if (n.id() === person || lit.has(n.id()) || n.hasClass('quiet')) return;
      const h = model.home.get(n.id());
      if (spots.some((t) => Math.hypot(t.x - h.x, t.y - h.y) < 86)) n.addClass('under');
    });
    hoverInfo.clear();
    for (const [id, set] of notes) hoverInfo.set(id, [...set].slice(0, 3).join(''));
    const summary = (answer.rows || [])[0];
    if (answer.outcome !== 'answered_with_path') hoverInfo.set(person, `<div class="k">${esc(answer.note || answer.sentence || '')}</div>`);
    else if (summary) hoverInfo.set(person, `<div class="k">${esc(answer.sentence || '')}</div>`);

    // the lit part glides into its steps (new hops one after another); everything else stays where it lives
    cy.nodes().forEach((n) => {
      const id = n.id();
      const to = targets.get(id) || model.home.get(id);
      const w = lit.get(id);
      moveTo(n, to, { wait: w && !wasLit.has(id) ? w.delay : 0 });
    });
    for (const w of items.values()) {
      if (w.cls === 'tail' && w.ele.hasClass('under')) continue;  // an owner a lit node sits on stays out of the way
      const apply = () => {
        if (state.person !== person || state.phase !== 'picked') return;
        w.ele.removeClass('faded under lit tail').addClass(w.cls);
        scheduleSync();
      };
      if (w.delay && !REDUCED && !wasLit.has(w.ele.id())) setTimeout(apply, w.delay); else apply();
    }
    state.flows = flows;
    $('zones').querySelectorAll('.zone').forEach((g) => {
      const t = g.querySelector('text').textContent;
      g.classList.toggle('lit', zones.has(t));
      g.classList.toggle('reached', !zones.has(t) && reached.has(t));
    });
    state.shown = d;
    scheduleSync();
  }

  // Where the lit part goes: the person first, then one column per step (in a column the systems above the
  // decisions), each column centred on what leads to it and ordered the same way, so the lines do not cross much.
  // The columns start at the person and keep to the drawing; a node left at home under a lit one is dimmed out of the
  // way (applyDepth). On a narrow portrait screen the columns are rows, read top to bottom.
  function stepTargets(person, lit) {
    const home = model.home.get(person);
    const [A, B] = PORTRAIT ? ['y', 'x'] : ['x', 'y'];
    const isDecision = (id) => model.nodes.get(id)?.kind === 'decision';
    const steps = [...new Set([...lit.values()].map((w) => w.step))].sort((a, b) => a - b);
    const n = steps.length;
    const end = PORTRAIT ? H - 110 : W - 92;
    const dMax = PORTRAIT ? 200 : 236, dMin = PORTRAIT ? 150 : 166;
    let start = home[A];
    let d = n ? Math.min(dMax, (end - start) / n) : dMax;
    if (d < dMin) {
      start = Math.max(PORTRAIT ? 110 : 96, end - n * dMin);
      d = Math.min(dMin, (end - start) / n);
    }
    const out = new Map([[person, { [A]: start, [B]: home[B] }]]);
    const lo = PORTRAIT ? 80 : Math.min(...[...regions.values()].filter((r) => r.label).map((r) => r.y0)) + 84;
    const hi = PORTRAIT ? W - 80 : extent.y1 - 60;
    // one column per step: its systems first, then its decisions, so the column reads person -> what they own ->
    // what they decided -> what that touches
    steps.forEach((step, c) => {
      const from = (id) => {
        const ps = [...lit.get(id).parents].map((q) => out.get(q)).filter(Boolean);
        return ps.length ? ps.reduce((s, q) => s + q[B], 0) / ps.length : home[B];
      };
      const ids = [...lit.keys()].filter((id) => lit.get(id).step === step);
      const want = new Map(ids.map((id) => [id, from(id)]));
      const byWant = (a, b) => want.get(a) - want.get(b) || a.localeCompare(b);
      const list = [...ids.filter((id) => !isDecision(id)).sort(byWant), ...ids.filter(isDecision).sort(byWant)];
      const gap = Math.min(PORTRAIT ? 175 : 96, list.length > 1 ? (hi - lo) / (list.length - 1) : Infinity);
      const centre = list.reduce((s, id) => s + want.get(id), 0) / list.length;
      const top = Math.max(lo, Math.min(hi - (list.length - 1) * gap, centre - ((list.length - 1) * gap) / 2));
      list.forEach((id, i) => out.set(id, { [A]: start + (c + 1) * d, [B]: top + i * gap }));
    });
    return out;
  }

  // the depth slider: four stops, click, drag or arrow keys
  function setSlider(d) {
    const s = $('slider');
    s.setAttribute('aria-valuenow', String(d));
    s.querySelectorAll('.stop').forEach((b) => {
      const v = Number(b.dataset.d);
      b.classList.toggle('on', v <= d);
      b.classList.toggle('cur', v === d);
    });
    s.querySelector('.fill').style.width = `${((d - 1) / 3) * 100}%`;
  }

  function initSlider() {
    const s = $('slider');
    s.querySelectorAll('.stop').forEach((b, i) => { b.style.left = `${(i / 3) * 100}%`; });
    const at = (x) => {
      const r = s.getBoundingClientRect();
      return Math.max(1, Math.min(4, Math.round(((x - r.left) / r.width) * 3) + 1));
    };
    let dragging = false;
    const go = (d) => { s.classList.remove('hint'); if (d !== state.depth && state.phase === 'picked') setDepth(d); };
    s.addEventListener('pointerdown', (ev) => { dragging = true; s.setPointerCapture(ev.pointerId); go(at(ev.clientX)); });
    s.addEventListener('pointermove', (ev) => { if (dragging) go(at(ev.clientX)); });
    s.addEventListener('pointerup', () => { dragging = false; });
    s.addEventListener('pointercancel', () => { dragging = false; });
    s.addEventListener('keydown', (ev) => {
      const k = ev.key;
      if (k === 'ArrowRight' || k === 'ArrowUp') { go(Math.min(4, state.depth + 1)); ev.preventDefault(); }
      if (k === 'ArrowLeft' || k === 'ArrowDown') { go(Math.max(1, state.depth - 1)); ev.preventDefault(); }
      if (k === 'Home') go(1);
      if (k === 'End') go(4);
    });
  }

  // ---- 3. Remove ----------------------------------------------------------------------------------------------------
  function asOfFor(person, tool) {
    const leave = model.leaving.get(person);
    return leave && leave.notice_at && api.accepts(tool, 'as_of') ? day(leave.notice_at) : null;
  }

  function withAsOf(tool, args) {
    const asOf = asOfFor(args.person, tool);
    return asOf ? { ...args, as_of: asOf } : args;
  }

  async function remove() {
    const person = state.person;
    if (!person) return;
    const my = ++state.seq;
    setPhase('removing');
    show('picked', false); show('depth', false);
    const calls = [['without', withAsOf('without', { person })], ['single_source', withAsOf('single_source', { person })], ['bridges', { top: 3 }]];
    let results;
    try { results = await Promise.all(calls.map(([n, a]) => api.tool(n, a))); } catch (err) {
      setStatus(err.message); setPhase('picked'); show('picked', true); show('depth', true); return;
    }
    if (my !== state.seq) return;
    record(`Remove ${person}`, calls.map(([n, a], i) => toolCall(n, a, results[i])));
    const [wo, ss, br] = results.map((r) => r.body);
    const islands = (wo.rows || []).filter((r) => r.kind === 'island');
    const islandItems = islands.flatMap((i) => i.items.map((item) => ({ item, kind: i.kinds?.[item], through: i.connected_through || [] })));
    const singles = (ss.rows || []).filter((r) => r.kind !== 'summary' && r.single_source);
    const singleDecisions = singles.filter((r) => r.item_kind === 'decision');
    const onlyLinks = (br.rows || []).filter((r) => r.kind === 'only_link' && r.person === person).map((r) => r.teams);
    const summary = (wo.rows || []).find((r) => r.kind === 'summary') || {};
    state.removed = { person, wo, ss, br, islandItems, singles, singleDecisions, onlyLinks, summary, drift: new Map() };

    setPhase('removed');
    clearClasses();
    const me = cy.getElementById(person);
    const drifting = new Set(islandItems.map((it) => it.item));
    goHome(true, drifting);
    me.addClass('ghost');
    cy.elements().not(me).addClass('soft');

    // islands drift off to the free margin beside their zone and glow red
    const taken = [];
    for (const it of islandItems) {
      const n = cy.getElementById(it.item);
      if (!n.length) continue;
      n.removeClass('soft').addClass('island drift');
      const to = driftSpot(it.item, taken);
      taken.push(to);
      state.removed.drift.set(it.item, to);
      moveTo(n, to, { duration: 900, easing: 'ease-out-cubic' });
      const through = it.through.map((c) => `${c.relation}${c.state === 'ended' ? ' (ended)' : ''}${c.count ? `, ${c.count} records` : ''}`).join('; ');
      hoverInfo.set(it.item, `<div class="red">Without ${esc(first(person))}, nobody in the graph is connected to it.</div>`
        + (through ? `<div class="k">Connected only through ${esc(person)}: ${esc(through)}</div>` : '')
        + `<div class="ids">${esc(it.through.flatMap((c) => c.source_ids || []).slice(0, 6).join(', '))}</div>`);
    }
    for (const r of singles) {
      const n = cy.getElementById(r.item);
      if (!n.length || n.hasClass('island')) continue;
      if (r.item_kind === 'decision') n.removeClass('soft').addClass('single');
      hoverInfo.set(r.item, `<div class="red">Every record behind it was written by ${esc(person)}.</div>`
        + (r.title ? `<div class="k">${esc(r.title)}</div>` : '') + `<div class="ids">${esc((r.episodes || []).slice(0, 6).join(', '))}</div>`);
    }
    // the person's own lines snap, where the other end is on screen (decisions and projects show only when they matter)
    me.connectedEdges().forEach((e) => {
      const other = e.source().id() === person ? e.target() : e.source();
      const result = other.hasClass('island') || other.hasClass('single');
      if (!result && (other.hasClass('quiet') || !['OWNS', 'DECIDED', 'LEADS'].includes(e.data('type')))) return;
      e.removeClass('soft').addClass('snap ' + (e.source().id() === person ? 'snap-s' : 'snap-t'));
    });
    links.bridges = onlyLinks;
    $('zones').querySelectorAll('.zone').forEach((g) => g.classList.toggle('red', onlyLinks.some((p) => p.includes(g.querySelector('text').textContent))));

    // counters
    const allSystems = islandItems.every((i) => !i.kind || i.kind === 'system');
    setCounter('islands', islandItems.length, allSystems ? plural(islandItems.length, 'system nobody is connected to', 'systems nobody is connected to')
      : plural(islandItems.length, 'item nobody is connected to', 'items nobody is connected to'));
    setCounter('decisions', singleDecisions.length, `${plural(singleDecisions.length, 'decision', 'decisions')} only ${first(person)} wrote`);
    setCounter('bridges', onlyLinks.length, `${plural(onlyLinks.length, 'bridge', 'bridges')} between teams lost`);
    counterTips(person);
    $('wall').textContent = summary.wall_ms !== undefined ? `measured ${summary.wall_ms} ms` : '';
    show('headline', false); show('counters', true); show('actions', true);
    fit(true);  // the same view on a wide screen; on a phone the counters take more room at the top
    $('plan').focus({ preventScroll: true });
    scheduleSync();
  }

  function driftSpot(item, taken) {
    // straight out to the free margin beside its zone (into the gap between the rows on a portrait screen)
    const h = model.home.get(item), g = regions.get('');
    if (!PORTRAIT) {
      const x = h.x < W / 2 ? g.mx * 0.56 : W - g.mx * 0.56;
      for (const dy of [0, 86, -86, 172, -172, 258, -258]) {
        const y = Math.max(extent.y0 + 50, Math.min(extent.y1 - 60, h.y + dy));
        if (taken.every((t) => Math.hypot(t.x - x, t.y - y) > 80)) return { x, y };
      }
      return { x, y: h.y };
    }
    const y = g.cy + (h.y < g.cy ? -g.gy * 0.42 : g.gy * 0.42);
    for (const dx of [0, 180, -180, 360, -360]) {
      const x = Math.max(90, Math.min(W - 90, h.x + dx));
      if (taken.every((t) => Math.hypot(t.x - x, t.y - y) > 160)) return { x, y };
    }
    return { x: h.x, y };
  }

  function setCounter(k, n, label) {
    const el = app.querySelector(`.counter[data-k="${k}"]`);
    el.classList.toggle('zero', n === 0);
    el.classList.remove('good');
    el.querySelector('span').textContent = label;
    const b = el.querySelector('b');
    const from = Number(b.textContent) || 0;
    if (REDUCED || from === n) { b.textContent = String(n); return; }
    const t0 = performance.now();
    const tick = (t) => {
      const f = Math.min(1, (t - t0) / 600);
      b.textContent = String(Math.round(from + (n - from) * (1 - Math.pow(1 - f, 3))));
      if (f < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function counterTips(person) {
    const r = state.removed;
    const isl = r.islandItems.map((i) => i.item).join(', ') || 'none';
    const dec = r.singleDecisions.map((d) => d.item).join(', ') || 'none';
    const br = r.onlyLinks.map((p) => p.join(' and ')).join('; ') || 'none';
    tips.islands = () => `<div>Islands without ${esc(person)}: <b>${esc(isl)}</b></div><div class="k">algo.WCC on a temporary copy of the graph without them`
      + `${r.summary.nodes ? `: ${r.summary.nodes} nodes, ${r.summary.links} links, ${r.summary.components} components` : ''}.</div>`;
    tips.decisions = () => `<div>Live decisions where every source record was written by ${esc(person)}: <b>${esc(dec)}</b></div>`;
    tips.bridges = () => `<div>Teams only ${esc(person)} links: <b>${esc(br)}</b></div><div class="k">algo.betweenness over people, teams and systems.</div>`;
    tips.wall = () => `<div class="k">Wall time of without(): projection, algo.WCC and cleanup on FalkorDB.</div>`;
  }

  const tips = {};

  // ---- 5. the handoff game ------------------------------------------------------------------------------------------
  // Cards: one per item that rested only on the person's records before any handoff (single_source as of their
  // notice day), plus the plan's other open questions. The question text is the plan's own while it is still open;
  // for an item the handoff already answered, the plan no longer asks it, so the same question is put from the same
  // facts (what the decision governs and who owns that). Answering reveals what the graph holds: the CONFIRM receipts
  // about the item since the notice, the records they cite, and single_source as of the day of those records.

  function governsText(item) {
    const owner = new Map(model.edges.filter((e) => e.type === 'OWNS').map((e) => [e.target, e.source]));
    const sys = model.edges.filter((e) => e.type === 'CONSTRAINS' && e.source === item).map((e) => e.target);
    return sys.map((s) => (owner.get(s) ? `${s} (owner ${owner.get(s)})` : s)).join(', ') || 'the systems it governs';
  }

  function cardsFor(person, before, planA) {
    const questions = (planA.rows || []).filter((r) => r.kind === 'question');
    const open = new Map(questions.filter((q) => q.status === 'open').map((q) => [q.item, q]));
    const single = (before.rows || []).filter((r) => r.kind !== 'summary' && r.single_source);
    const cards = single.map((r) => ({
      item: r.item, item_kind: r.item_kind, title: r.title,
      question: open.get(r.item)?.question || (r.item_kind === 'decision'
        ? `Why was ${r.item} decided, and what breaks in ${governsText(r.item)} if it is undone?`
        : `How is ${r.item} run day to day, and where are the steps written down?`),
      from: open.has(r.item) ? 'handoff_plan' : 'single_source', why: open.get(r.item)?.why || `Every record behind ${r.item} was written by ${person}.`,
      sources: r.episodes || [],
    }));
    for (const q of questions) {
      if (q.item_kind === 'gap' || cards.some((c) => c.item === q.item)) continue;  // the phone question is the pill
      cards.push({ item: q.item, item_kind: q.item_kind, question: q.question, from: 'handoff_plan', why: q.why, sources: q.sources_so_far || [] });
    }
    const order = { system: 0, decision: 1 };
    return cards.sort((a, b) => (order[a.item_kind] ?? 2) - (order[b.item_kind] ?? 2) || a.item.localeCompare(b.item));
  }

  async function plan() {
    const person = state.person;
    const my = ++state.seq;
    const leave = model.leaving.get(person);
    const before = state.removed.ss;
    const calls = [['handoff_plan', { person }], ['promises', { person }]];
    let results;
    try { results = await Promise.all(calls.map(([n, a]) => api.tool(n, a))); } catch (err) { setStatus(err.message); return; }
    if (my !== state.seq) return;
    record(`Plan the handoff for ${person}`, calls.map(([n, a], i) => toolCall(n, a, results[i])));
    const planA = results[0].body, promisesA = results[1].body;
    const cards = cardsFor(person, before, planA);
    const total = leave && leave.last_day && leave.notice_at ? daysBetween(day(leave.notice_at), leave.last_day) : ASSUMED_NOTICE_DAYS;
    state.game = { person, cards, plan: planA, promises: promisesA, before, days: total, total, saved: 0,
      answered: new Set(), assumed: !(leave && leave.last_day), resolved: new Map(), notice: leave?.notice_at || null };
    setPhase('game');
    show('counters', false); show('actions', false);
    $('headline').textContent = `Ask before ${first(person)} leaves.`;
    show('headline', true);
    show('game', true);
    $('cards').innerHTML = '';
    $('saved-of').textContent = String(cards.length);
    renderGame();
    for (const c of cards) {
      const n = cy.getElementById(c.item);
      if (n.length && !n.hasClass('island') && !n.hasClass('single')) n.removeClass('soft faded').addClass('single');
    }
    // as in "Ask before ... leaves": what is in play gathers round the person, the rest of the company steps back
    const ids = cards.map((c) => c.item).filter((id) => cy.getElementById(id).length);
    const { centre, spots } = ringAround(person, ids);
    cy.nodes().forEach((n) => { if (n.id() !== person && !spots.has(n.id())) n.removeClass('soft').addClass('faded'); });
    if (ids.length) moveTo(cy.getElementById(person), centre);
    for (const [id, spot] of spots) moveTo(cy.getElementById(id), spot);
    const taken = [centre, ...spots.values()];
    cy.nodes('.faded').forEach((n) => {
      const h = n.position();
      if (taken.some((t) => Math.hypot(t.x - h.x, t.y - h.y) < 84)) n.addClass('under');
    });
    state.focus = taken;
    fit(true, state.focus);
    scheduleSync();
    if (!cards.length) { await sleep(ms(900)); if (state.game && state.game.person === person) finish(); }
  }

  // a ring (an ellipse on a wide screen) round the person: up to six items on the arc that faces the cards (the line
  // from a card to its item then never crosses the ring), more all round
  function ringAround(person, ids) {
    const home = model.home.get(person), n = ids.length;
    const rx = PORTRAIT ? 230 : 270, ry = PORTRAIT ? 260 : 196;
    const centre = { x: Math.max(extent.x0 + rx + 90, Math.min(extent.x1 - rx - 90, home.x)),
      y: Math.max(extent.y0 + ry + 50, Math.min(extent.y1 - ry - 50, home.y)) };
    const spots = new Map();
    ids.forEach((id, i) => {
      const arc = n <= 2 ? (2 * Math.PI) / 3 : n <= 6 ? (11 * Math.PI) / 9 : 2 * Math.PI;
      const a = n === 1 ? 0 : n <= 6 ? -arc / 2 + (i * arc) / (n - 1) : -Math.PI / 2 + (i * 2 * Math.PI) / n;
      spots.set(id, { x: centre.x + Math.cos(a) * rx, y: centre.y + Math.sin(a) * ry });
    });
    return { centre, spots };
  }

  function renderGame() {
    const g = state.game;
    $('days').textContent = String(g.days);
    $('days').parentElement.lastChild.textContent = ` ${plural(g.days, 'day', 'days')} left`;
    $('days').parentElement.classList.toggle('low', g.days < DAYS_PER_ANSWER);
    $('saved-n').textContent = String(g.saved);
    $('bar-fill').style.width = g.cards.length ? `${(g.saved / g.cards.length) * 100}%` : '0';
    const box = $('cards');
    if (!box.children.length) {
      if (!g.cards.length) {
        box.innerHTML = `<div class="card empty">Nothing rests only on ${esc(first(g.person))}'s records: no question to ask.</div>`;
      }
      g.cards.forEach((c, i) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'card';
        b.dataset.i = String(i);
        b.innerHTML = `<span class="q">${esc(c.question)}</span>`;
        b.addEventListener('click', () => answer(i, b));
        htmlTip(b, () => `<div><b>${esc(c.item)}</b> <span class="k">${esc(c.item_kind)}${c.title ? ' · ' + esc(c.title) : ''}</span></div>`
          + `<div class="k">${esc(c.why || '')}</div><div class="ids">${esc((c.sources || []).slice(0, 6).join(', '))}</div>`
          + (g.assumed ? `<div class="k">No last day is recorded for ${esc(g.person)}: two weeks assumed.</div>` : '')
          + `<div class="k">Each answer costs ${DAYS_PER_ANSWER} days.</div>`);
        box.appendChild(b);
      });
    }
    box.querySelectorAll('.card[data-i]').forEach((b) => {
      const i = Number(b.dataset.i);
      if (!g.answered.has(i) && g.days < DAYS_PER_ANSWER) { b.classList.add('lost'); b.disabled = true; }
    });
  }

  function linesAbout(text, item) {
    const re = new RegExp(`(^|[^\\w-])${item.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^\\w-]|$)`);
    return String(text || '').split('\n').map((s) => s.replace(/^[-*]\s*/, '').trim()).filter((s) => s && re.test(s));
  }

  async function writtenAnswer(item, g) {
    // CONFIRM receipts about the item since the notice, the records they cite, and the item's state as of then
    if (!g.notice) return null;
    const rec = await api.confirmReceipts();
    const receipts = (rec.body.receipts || []).filter((r) => r.outcome === 'CONFIRM' && r.ts > g.notice
      && (r.subject === item || r.object === item));
    if (!receipts.length) return { receipts: [], episodes: [], after: null };
    const ids = [...new Set(receipts.flatMap((r) => r.cites || []))].slice(0, 6);
    const episodes = (await Promise.all(ids.map((id) => api.episode(id)))).map((r) => r.body).filter((e) => e.ts > g.notice);
    if (!episodes.length) return { receipts: [], episodes: [], after: null };
    const asOf = episodes.map((e) => day(e.ts)).sort().pop();
    const args = { person: g.person, as_of: asOf };
    const res = await api.tool('single_source', args);
    record(`Answer about ${item}`, [
      { name: 'GET /receipts?outcome=confirm', meta: `${receipts.length} about ${item} since ${day(g.notice)}`, queries: [] },
      toolCall('single_source', args, res)]);
    const row = (res.body.rows || []).find((r) => r.item === item && r.kind !== 'summary');
    return { receipts, episodes, after: row || null, asOf };
  }

  // The Investigator <-> Verifier loop (POST /run, serve.py) on a question about the card's item: the evidence the
  // Verifier re-read at each record's source system goes into the same card. Servers without /run show nothing extra.
  function runQuestion(c) {
    if (c.item_kind === 'decision') return `Who decided ${c.item}?`;
    if (c.item_kind === 'system') return `Who owns ${c.item} now?`;
    return null;
  }

  async function verifiedEvidence(c, card, g) {
    const question = runQuestion(c);
    if (!question || api.source !== 'live' || api.noRun) return;
    let res;
    try {
      res = await api.http('run', { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ input: { query: question } }) });
    } catch (_) { api.noRun = true; return; }
    if (state.game !== g) return;
    const o = res.body.output || {};
    if (!o.status || o.status === 'error') return;
    const round = o.rounds || 1;
    const short = (t, n = 110) => (String(t).length > n ? String(t).slice(0, n - 3).replace(/\s+\S*$/, '') + '…' : String(t));
    const system = (s) => String(s || '').replace(/\s*\(.*\)$/, '');
    const verdict = (v) => (String(v || '').startsWith('skipped') ? 'quote found' : String(v || ''));
    const kept = (o.citations || []).filter((x) => x.accepted).map((x) => `<span class="cite ok">`
      + `<b>${esc(x.id)}</b> <span class="by">${esc(system(x.source))} · round ${round}</span> <q>${esc(short(x.quote))}</q> `
      + `<span class="verdict">${esc(verdict(x.verdict))}</span></span>`);
    const gone = (o.dropped_citations || []).map((x) => `<span class="cite bad">`
      + `<b>${esc(x.id)}</b> <span class="by">round ${esc(x.round)}</span> <span class="verdict">${esc(String(x.reason || '').replace(/_/g, ' '))}</span></span>`);
    const head = { verified: 'Verified at the source', absence_confirmed: 'Checked at the source: no record names it' }[o.status]
      || `Checked at the source: ${String(o.status).replace(/_/g, ' ')}`;
    card.insertAdjacentHTML('beforeend', `<span class="answer evidence"><span class="by">${esc(head)}: `
      + `<q>${esc(question)}</q> (${esc(o.endpoint === 'local-sample' ? 'sample copy of the three systems' : o.endpoint)},`
      + ` Verifier, ${round} ${plural(round, 'round', 'rounds')})</span>`
      + kept.join('') + gone.join('') + `</span>`);
    htmlTip(card.querySelector('.answer.evidence'), () => `<div><b>${esc(question)}</b></div><div class="k">${esc(short(o.answer, 240))}</div>`
      + `<div class="ids">trace ${esc(res.body.trace_id || '')}</div>`);
  }

  async function answer(i, card) {
    const g = state.game;
    if (!g || g.answered.has(i) || g.days < DAYS_PER_ANSWER) return;
    const c = g.cards[i];
    g.answered.add(i);
    g.days -= DAYS_PER_ANSWER;
    card.disabled = true;
    renderGame();
    let found = null;
    try { found = await writtenAnswer(c.item, g); } catch (err) { setStatus(err.message); }
    if (state.game !== g) return;
    const node = cy.getElementById(c.item);
    if (found && found.receipts.length) {
      const mine = found.episodes.filter((e) => e.author === g.person);
      const others = found.episodes.filter((e) => e.author !== g.person);
      const said = mine.flatMap((e) => linesAbout(e.text, c.item).map((l) => ({ l, e })))[0];
      const confirmed = others.filter((e) => linesAbout(e.text, c.item).length);
      const done = found.after && !found.after.single_source;
      const ids = found.receipts.map((r) => r.id);
      const chips = `<span class="receipt" title="${esc(found.receipts.map((r) => `${r.id}: ${r.subject} ${r.predicate} ${r.object}, cites ${r.cites.join(', ')}`).join('\n'))}">`
        + `${esc(found.receipts[0].outcome)} ${esc(ids[0])}${ids.length > 1 ? ` +${ids.length - 1}` : ''}</span>`;
      const short = (t) => (t.length > 150 ? t.slice(0, 147).replace(/\s+\S*$/, '') + '…' : t);
      card.classList.add(done ? 'saved' : 'half');
      card.insertAdjacentHTML('beforeend',
        (said ? `<span class="answer"><q>${esc(short(said.l))}</q> <span class="by">${esc(said.e.id)}, ${esc(day(said.e.ts))}</span></span>` : '')
        + (confirmed.length ? `<span class="answer by">Confirmed by ${esc(confirmed.map((e) => `${e.author} (${e.id})`).join(', '))}.</span>`
          : `<span class="answer by">Written down, but only by ${esc(first(g.person))}: it still rests on one person.</span>`)
        + chips);
      if (done) {
        g.saved++;
        g.resolved.set(c.item, { by: [...new Set(confirmed.map((e) => e.author))], episodes: found.episodes.map((e) => e.id), confirmIn: confirmed.map((e) => e.id) });
        if (node.length) node.removeClass('island single soft faded').addClass('saved');
        hoverInfo.set(c.item, `<div class="green">As of ${esc(found.asOf)}, records by ${esc((found.after.authors || []).join(', '))}</div>`
          + `<div class="ids">${esc((found.after.episodes || []).join(', '))}</div>`);
        links.card = { el: card, node: c.item };
      } else {
        links.card = null;
        hoverInfo.set(c.item, `<div class="red">As of ${esc(found.asOf)}, every record behind it is still by ${esc(g.person)}.</div>`
          + `<div class="ids">${esc((found.after?.episodes || []).join(', '))}</div>`);
      }
    } else {
      card.classList.add('empty');
      card.insertAdjacentHTML('beforeend', `<span class="answer">Not in the graph: no written answer from ${esc(first(g.person))} about ${esc(c.item)}${g.notice ? ` since ${esc(day(g.notice))}` : ''}.</span>`);
      links.card = null;
    }
    await verifiedEvidence(c, card, g);
    renderGame();
    card.scrollIntoView({ block: 'nearest', behavior: REDUCED ? 'auto' : 'smooth' });
    $('cards').addEventListener('scroll', scheduleSync, { once: true });
    setTimeout(scheduleSync, ms(400));
    scheduleSync();
    const left = g.cards.some((_, k) => !g.answered.has(k)) && g.days >= DAYS_PER_ANSWER;
    if (!left) { await sleep(ms(1600)); if (state.game === g) finish(); }
  }

  // ---- end state ----------------------------------------------------------------------------------------------------
  function finish() {
    const g = state.game;
    const r = state.removed;
    setPhase('done');
    show('game', false); show('headline', false); show('restart', true);
    links.card = null;
    // counters after the answers: an item counts as handed over only if the graph says it no longer rests on them
    const decisionsLeft = r.singleDecisions.filter((d) => !g.resolved.has(d.item)).length;
    const islandsLeft = r.islandItems.filter((it) => !g.resolved.has(it.item)).length;
    setCounter('islands', islandsLeft, app.querySelector('.counter[data-k="islands"] span').textContent);
    setCounter('decisions', decisionsLeft, app.querySelector('.counter[data-k="decisions"] span').textContent);
    setCounter('bridges', r.onlyLinks.length, app.querySelector('.counter[data-k="bridges"] span').textContent);
    for (const [k, n, was] of [['islands', islandsLeft, r.islandItems.length], ['decisions', decisionsLeft, r.singleDecisions.length]]) {
      app.querySelector(`.counter[data-k="${k}"]`).classList.toggle('good', n === 0 && was > 0);
    }
    show('counters', true);
    cy.edges('.snap').addClass('after');
    cy.nodes('.under').removeClass('under');
    cy.nodes('.faded').removeClass('faded').addClass('soft');

    // the items in play go back: what was handed over to its place, what still rests on the person to its margin
    moveTo(cy.getElementById(g.person), model.home.get(g.person));
    for (const c of g.cards) {
      const n = cy.getElementById(c.item);
      if (!n.length) continue;
      const resolved = g.resolved.has(c.item);
      if (resolved && n.hasClass('island')) n.removeClass('island drift').addClass('saved');
      moveTo(n, resolved ? model.home.get(c.item) : r.drift.get(c.item) || model.home.get(c.item), { duration: 800 });
    }
    // who takes what: the person who confirmed in writing, else the plan's first candidate (a proposal)
    const lines = new Map(((g.plan.rows || []).filter((x) => x.kind === 'line')).map((x) => [x.item, x]));
    const items = [...new Set([...g.cards.map((c) => c.item), ...lines.keys()])];
    const rows = [];
    for (const item of items) {
      const res = g.resolved.get(item);
      const to = res && res.by.length ? res.by[0] : (lines.get(item)?.candidates || [])[0];
      const how = res && res.by.length ? `confirmed in ${res.confirmIn.join(', ')}` : to ? 'candidate (proposed)' : '';
      rows.push(`<div class="hrow"><span>${esc(item)}</span><span class="arrow">&rarr;</span>`
        + `<span class="to${to ? '' : ' none'}">${esc(to || (['team', 'project'].includes(lines.get(item)?.item_kind) ? 'a new lead, to be named' : 'no one in the graph yet'))}${res && res.by.length ? ` <small>${esc(res.confirmIn.join(', '))}</small>` : ''}</span></div>`);
      if (to && cy.getElementById(to).length && cy.getElementById(item).length && !cy.getElementById(`handover|${item}|${to}`).length) {
        cy.add({ group: 'edges', data: { id: `handover|${item}|${to}`, source: item, target: to }, classes: 'handover' });
        cy.getElementById(to).removeClass('soft faded').addClass('succ');
        hoverInfo.set(to, (hoverInfo.get(to) || '') + `<div class="green">${esc(item)}: ${esc(how)}</div>`);
      }
    }
    $('handoff-rows').innerHTML = rows.join('');
    show('handoff', rows.length > 0);
    $('ask-promise').textContent = `What did ${first(g.person)} promise the partner on the phone?`;
    $('promise-reply').hidden = true;
    show('promise', true);
    fit(true);
    scheduleSync();
  }

  function askPromise() {
    const g = state.game;
    if (!g) return;
    const a = g.promises;
    const found = (a.rows || []).filter((r) => r.kind === 'promise');
    const reply = $('promise-reply');
    reply.textContent = a.outcome === 'not_in_graph' ? 'Not in the graph.'
      : `${found.length} written ${plural(found.length, 'promise', 'promises')}: ${found[0] ? `${found[0].episode}, "${found[0].line}"` : ''}`;
    reply.hidden = false;
    record(`promises(${g.person})`, [toolCall('promises', { person: g.person }, { body: a, roundtrip: null })]);
  }

  // ---- events -------------------------------------------------------------------------------------------------------
  function onTapNode(n) {
    const id = n.id();
    hideTip();
    const kind = model.nodes.get(id)?.kind;
    if (kind !== 'person') return;
    if (state.phase === 'landing' || state.phase === 'picked') { if (id !== state.person) pick(id); return; }
    if (state.phase === 'removed' || state.phase === 'done') { toLanding(false); pick(id); }
  }

  function onTapBackground() {
    if (state.phase === 'picked') toLanding(true);
  }

  function wire() {
    $('remove').addEventListener('click', remove);
    $('again').addEventListener('click', () => toLanding(true));
    $('restart').addEventListener('click', () => toLanding(true));
    $('plan').addEventListener('click', plan);
    $('ask-promise').addEventListener('click', askPromise);
    htmlTip($('promise-reply'), () => `<div class="k">${esc(state.game?.promises?.note || state.game?.promises?.sentence || '')}</div>`);
    $('show-cypher').addEventListener('click', () => {
      const c = $('cypher');
      c.hidden = !c.hidden;
      if (!c.hidden) renderCypher();
    });
    $('cypher-close').addEventListener('click', () => { $('cypher').hidden = true; });
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') {
        if (!$('cypher').hidden) $('cypher').hidden = true;
        else if (state.phase === 'picked') toLanding(true);
      }
    });
    for (const k of ['islands', 'decisions', 'bridges']) htmlTip(app.querySelector(`.counter[data-k="${k}"]`), () => (tips[k] ? tips[k]() : ''));
    htmlTip($('wall'), () => (tips.wall ? tips.wall() : ''));
    // a lit hop in the overlay shows the same note as its edge: the relation, since when, the records behind it
    const overlay = $('overlay');
    overlay.addEventListener('mouseover', (ev) => {
      const f = ev.target.closest && ev.target.closest('.flow');
      if (!f) return;
      const id = f.dataset.edge;
      const tip = $('tip');
      tip.innerHTML = describeEdge(id) + (hoverInfo.get(id) || '');
      tip.hidden = !tip.innerHTML;
      moveTip(ev);
    });
    overlay.addEventListener('mousemove', moveTip);
    overlay.addEventListener('mouseout', (ev) => { if (ev.target.closest && ev.target.closest('.flow')) hideTip(); });
    initSlider();
    let t = null;
    addEventListener('resize', () => { clearTimeout(t); t = setTimeout(() => { fit(false, state.phase === 'game' ? state.focus : null); scheduleSync(); }, 80); });
  }

  // ---- start --------------------------------------------------------------------------------------------------------
  async function start() {
    setStatus('Loading the company graph…');
    let res;
    try {
      [res] = await Promise.all([api.graph(), api.loadSchemas()]);
    } catch (err) {
      setStatus(`Could not load the graph: ${err.message}`);
      return;
    }
    buildModel(res.body);
    layout();
    initCy();
    wire();
    record('The company graph', [{
      name: `GET /v1/${TENANT}/graph`,
      meta: [`${res.body.nodes.length} nodes, ${res.body.edges.length + res.body.ended.length} facts`,
        res.roundtrip !== null && res.roundtrip !== undefined ? `${res.roundtrip} ms round trip` : 'recorded'].join(' · '),
      queries: (res.body.trace || []).map((t) => ({ text: t.query, ms: t.ms })),
    }]);
    if (!api.fellBack) setStatus(api.source === 'fixtures' ? 'recorded answers' : '');
    toLanding(false);
    // read-only hooks for scripted walkthroughs (screenshots, the demo video): where a node is, which phase is on
    window.panel = Object.freeze({
      phase: () => state.phase,
      at: (id) => {
        const n = cy.getElementById(id);
        if (!n.length) return null;
        const p = n.renderedPosition(), r = $('cy').getBoundingClientRect();
        return { x: r.left + p.x, y: r.top + p.y };
      },
      people: () => [...model.nodes.values()].filter((n) => n.kind === 'person').map((n) => n.id),
    });
    // a gentle settle: from slightly gathered towards each zone to the final, still layout
    if (!REDUCED) {
      cy.nodes().forEach((n) => {
        const h = model.home.get(n.id());
        const [cx, cyy] = zoneCenter(model.nodes.get(n.id()).team);
        n.position({ x: h.x + (cx - h.x) * 0.16, y: h.y + (cyy - h.y) * 0.16 });
        moveTo(n, h, { duration: 950, easing: 'ease-out-cubic' });
      });
    }
    scheduleSync();
  }

  start();
})();
