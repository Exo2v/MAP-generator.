/* mapgen terrain studio — viewport + control wiring.
 *
 * Talks to the local server: it generates a world from the panel settings, builds a 3D
 * scene from the mesh payload, lets you re-colour it live from the per-vertex fields,
 * inspect any column, view the 2D map rasters and export a real Minecraft world.
 */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  // ── app state ──────────────────────────────────────────────────────────────
  const state = {
    config: null,
    presets: [],
    biomes: [],
    mesh: null,
    colorMode: "biome",
    jobId: null,
    generating: false,
    lastHover: null,
  };

  // ── three.js scene ─────────────────────────────────────────────────────────
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0d12);
  scene.fog = new THREE.Fog(0x0a0d12, 2200, 9000);

  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = false;
  $("#viewport").appendChild(renderer.domElement);

  const camera = new THREE.PerspectiveCamera(52, 1, 1, 40000);
  camera.position.set(900, 700, 900);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.damping = 0.15;

  const hemi = new THREE.HemisphereLight(0x9fc4ff, 0x2a2f38, 0.55);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight(0xffe9cc, 1.15);
  sun.position.set(-1, 1.4, -0.6);
  scene.add(sun);
  const fill = new THREE.DirectionalLight(0x88aaff, 0.25);
  fill.position.set(1, 0.5, 1);
  scene.add(fill);

  const world = new THREE.Group();
  scene.add(world);

  let terrainMesh = null;
  let waterMesh = null;
  const overlayGroup = new THREE.Group();
  world.add(overlayGroup);
  let marker = null;
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();

  function resize() {
    const el = $("#viewport");
    const w = el.clientWidth, h = el.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  if (window.ResizeObserver) new ResizeObserver(resize).observe($("#viewport"));

  (function loop() {
    requestAnimationFrame(loop);
    controls.update();
    renderer.render(scene, camera);
  })();

  // ── helpers ────────────────────────────────────────────────────────────────
  function b64ToTyped(b64, Type) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Type(bytes.buffer);
  }

  function hexToRgb(arr) {
    return arr;
  }

  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  function ramp(t, stops) {
    t = clamp(t, 0, 1);
    for (let i = 0; i < stops.length - 1; i++) {
      const [p0, c0] = stops[i], [p1, c1] = stops[i + 1];
      if (t >= p0 && t <= p1) {
        const f = (t - p0) / (p1 - p0 || 1);
        return [
          c0[0] + (c1[0] - c0[0]) * f,
          c0[1] + (c1[1] - c0[1]) * f,
          c0[2] + (c1[2] - c0[2]) * f,
        ];
      }
    }
    return stops[stops.length - 1][1];
  }

  const RAMPS = {
    height: [[0, [12, 44, 92]], [0.28, [58, 122, 66]], [0.5, [126, 168, 96]],
             [0.68, [186, 168, 112]], [0.84, [150, 132, 120]], [1, [248, 250, 252]]],
    temperature: [[0, [58, 96, 190]], [0.35, [92, 178, 170]], [0.55, [126, 196, 96]],
                  [0.75, [226, 176, 78]], [1, [216, 74, 48]]],
    humidity: [[0, [176, 142, 88]], [0.4, [186, 190, 122]], [0.62, [104, 174, 116]],
               [0.82, [58, 140, 168]], [1, [36, 74, 168]]],
    population: [[0, [30, 34, 28]], [0.35, [72, 104, 54]], [0.7, [96, 176, 84]], [1, [186, 238, 138]]],
    fertility: [[0, [64, 48, 40]], [0.45, [140, 122, 70]], [0.75, [136, 190, 96]], [1, [206, 240, 132]]],
    discharge: [[0, [26, 32, 40]], [0.4, [46, 76, 106]], [0.7, [72, 148, 200]], [1, [198, 232, 255]]],
    slope: [[0, [42, 74, 58]], [0.4, [166, 156, 96]], [0.72, [176, 110, 74]], [1, [236, 236, 236]]],
    continentality: [[0, [46, 108, 150]], [0.5, [150, 178, 130]], [1, [196, 140, 92]]],
    soil: [[0, [58, 46, 38]], [0.5, [130, 96, 62]], [1, [214, 186, 140]]],
    shaded: [[0, [60, 66, 74]], [1, [226, 232, 238]]],
  };

  // ── mesh building ──────────────────────────────────────────────────────────
  function disposeMesh(m) {
    if (!m) return;
    world.remove(m);
    if (m.geometry) m.geometry.dispose();
    if (m.material) {
      if (m.material.map) m.material.map.dispose();
      m.material.dispose();
    }
  }

  function buildScene(payload) {
    state.mesh = payload;
    disposeMesh(terrainMesh); terrainMesh = null;
    disposeMesh(waterMesh); waterMesh = null;
    while (overlayGroup.children.length) overlayGroup.remove(overlayGroup.children[0]);

    const positions = b64ToTyped(payload.vertices, Float32Array);
    const colors = b64ToTyped(payload.colors, Uint8Array);
    const indices = b64ToTyped(payload.indices, Uint32Array);

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geom.setAttribute("color", new THREE.BufferAttribute(colors, 3, true));
    geom.setIndex(new THREE.BufferAttribute(indices, 1));
    geom.computeVertexNormals();
    geom.computeBoundingSphere();

    // stash the scalar fields for live re-colouring
    if (payload.fields) {
      state.fields = {};
      for (const [k, v] of Object.entries(payload.fields)) {
        state.fields[k] = b64ToTyped(v, Uint8Array);
      }
    } else {
      state.fields = null;
    }

    const mat = new THREE.MeshStandardMaterial({
      vertexColors: true, roughness: 0.94, metalness: 0.02, flatShading: false,
    });
    terrainMesh = new THREE.Mesh(geom, mat);
    terrainMesh.name = "terrain";
    world.add(terrainMesh);

    // ── water ──
    if (payload.water) {
      const wpos = b64ToTyped(payload.water.vertices, Float32Array);
      const wcol = b64ToTyped(payload.water.colors, Uint8Array);
      const wmask = b64ToTyped(payload.water.mask, Uint8Array);
      const wind = indices;
      // only emit quads where the grid actually has water
      const side = Math.round(Math.sqrt(wmask.length));
      const keep = [];
      for (let z = 0; z < side - 1; z++) {
        for (let x = 0; x < side - 1; x++) {
          const a = z * side + x, b = a + 1, c = a + side, d = c + 1;
          if (wmask[a] || wmask[b] || wmask[c] || wmask[d]) {
            keep.push(a, c, b, b, c, d);
          }
        }
      }
      const wgeom = new THREE.BufferGeometry();
      wgeom.setAttribute("position", new THREE.BufferAttribute(wpos, 3));
      wgeom.setAttribute("color", new THREE.BufferAttribute(wcol, 3, true));
      wgeom.setIndex(keep);
      wgeom.computeVertexNormals();
      const wmat = new THREE.MeshStandardMaterial({
        vertexColors: true, transparent: true, opacity: 0.82, roughness: 0.18,
        metalness: 0.35, side: THREE.DoubleSide, depthWrite: false,
      });
      waterMesh = new THREE.Mesh(wgeom, wmat);
      waterMesh.renderOrder = 2;
      world.add(waterMesh);
      waterMesh.visible = $("#show-water").checked;
    }

    // ── rivers ──
    if (payload.rivers && payload.rivers.length) {
      const group = new THREE.Group();
      group.name = "rivers";
      for (const r of payload.rivers) {
        const pts = b64ToTyped(r.pts, Float32Array);
        const vecs = [];
        for (let i = 0; i < pts.length; i += 3) {
          vecs.push(new THREE.Vector3(pts[i], pts[i + 1] + 0.6, pts[i + 2]));
        }
        const g = new THREE.BufferGeometry().setFromPoints(vecs);
        const m = new THREE.LineBasicMaterial({
          color: r.ends_in === "sea" ? 0x8fd3ff : 0xa8e0ff,
          transparent: true, opacity: 0.95,
        });
        group.add(new THREE.Line(g, m));
      }
      group.visible = $("#show-rivers").checked;
      overlayGroup.add(group);
    }

    // ── roads ──
    if (payload.roads && payload.roads.length) {
      const group = new THREE.Group();
      group.name = "roads";
      for (const r of payload.roads) {
        const pts = b64ToTyped(r, Float32Array);
        const vecs = [];
        for (let i = 0; i < pts.length; i += 3) vecs.push(new THREE.Vector3(pts[i], pts[i + 1], pts[i + 2]));
        const g = new THREE.BufferGeometry().setFromPoints(vecs);
        group.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0xd9b382, opacity: 0.9, transparent: true })));
      }
      group.visible = $("#show-roads").checked;
      overlayGroup.add(group);
    }

    // ── POIs ──
    if (payload.pois && payload.pois.length) {
      const group = new THREE.Group();
      group.name = "pois";
      const geo = new THREE.SphereGeometry(6, 10, 8);
      const colorsByKind = {
        village: 0xffd166, hamlet: 0xf4a261, ruin: 0xbdbdbd, outpost: 0x8ecae6,
        landmark: 0xc77dff, port: 0x4cc9f0,
      };
      for (const p of payload.pois) {
        const mat = new THREE.MeshBasicMaterial({ color: colorsByKind[p.kind] || 0xffffff });
        const m = new THREE.Mesh(geo, mat);
        m.position.set(p.x, p.y + 6, p.z);
        group.add(m);
      }
      group.visible = $("#show-pois").checked;
      overlayGroup.add(group);
    }

    // frame the camera on the new terrain
    const b = payload.bounds;
    const cx = (b.x0 + b.x1) / 2, cz = (b.z0 + b.z1) / 2;
    const radius = Math.max(b.x1 - b.x0, b.z1 - b.z0) * 0.95;
    controls.maxDistance = radius * 6;
    controls.frame(new THREE.Vector3(cx, (b.min_y + b.max_y) / 2, cz), radius, -35, 58);
    applyColorMode(state.colorMode);
    updateLegend(payload);
    $("#empty-state").classList.add("hidden");
  }

  function applyColorMode(mode) {
    if (!terrainMesh) return;
    const geom = terrainMesh.geometry;
    const attr = geom.getAttribute("color");
    const n = attr.count;
    const out = attr.array;

    if (mode === "biome" || !state.fields) {
      const base = b64ToTyped(state.mesh.colors, Uint8Array);
      out.set(base);
      attr.needsUpdate = true;
      terrainMesh.material.wireframe = $("#wireframe").checked;
      return;
    }

    const field = state.fields[mode];
    if (!field) return;
    const rampDef = RAMPS[mode] || RAMPS.shaded;
    const shadeField = state.fields.slope;
    for (let i = 0; i < n; i++) {
      const t = field[i] / 255;
      const c = ramp(t, rampDef);
      // a little slope shading so form still reads in the flat colour maps
      const sh = shadeField ? 1.0 - 0.35 * (shadeField[i] / 255) : 1.0;
      out[i * 3] = c[0] * sh;
      out[i * 3 + 1] = c[1] * sh;
      out[i * 3 + 2] = c[2] * sh;
    }
    attr.needsUpdate = true;
    terrainMesh.material.wireframe = $("#wireframe").checked;
  }

  function updateLegend(payload) {
    const el = $("#view-legend");
    if (!payload) { el.innerHTML = ""; return; }
    const s = payload.stats || {};
    el.innerHTML =
      `<strong>${payload.stats ? payload.count.toLocaleString() : ""}</strong> mesh vertices · ` +
      `height ${Math.round(s.min_height)}–${Math.round(s.max_height)} · ` +
      `water ${(100 * (s.water_fraction || 0)).toFixed(1)}% · ` +
      `rivers ${s.river_count} · lakes ${s.lake_count} · ` +
      `settlements ${s.poi_count} · plants ${(s.tree_count || 0).toLocaleString()}`;
  }

  // ── API ────────────────────────────────────────────────────────────────────
  async function api(path, opts) {
    const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  // ── config ⇄ UI ────────────────────────────────────────────────────────────
  const BINDINGS = [
    ["#seed", "seed", "int"],
    ["#world-name", "name", "str"],
    ["#blocks-x", "region.blocks_x", "int"],
    ["#blocks-z", "region.blocks_z", "int"],
    ["#cell-size", "region.cell_size", "int"],
    ["#temp-bias", "climate.temperature_bias", "float"],
    ["#hum-bias", "climate.humidity_bias", "float"],
    ["#rain-shadow", "climate.rain_shadow_strength", "float"],
    ["#wind-dir", "climate.wind_direction", "float"],
    ["#climate-scale", "climate.region_scale_factor", "float"],
    ["#land-fraction", "terrain.land_fraction", "float"],
    ["#mountain-height", "terrain.mountain_height", "float"],
    ["#mountain-threshold", "terrain.mountain_threshold", "float"],
    ["#plateau", "terrain.plateau_amount", "float"],
    ["#cliffs", "terrain.cliff_amount", "float"],
    ["#islands", "terrain.island_count", "int"],
    ["#terrace", "terrain.terrace", "float"],
    ["#diffusion", "erosion.diffusion", "float"],
    ["#thermal", "erosion.thermal_iterations", "int"],
    ["#fluvial-k", "erosion.fluvial_k", "float"],
    ["#fluvial-iters", "erosion.fluvial_iterations", "int"],
    ["#droplets", "erosion.droplets", "int"],
    ["#river-threshold", "water.river_threshold", "float"],
    ["#valley-depth", "water.valley_depth", "float"],
    ["#bank-flare", "water.bank_flare", "float"],
    ["#meander", "water.meander_strength", "float"],
    ["#carve-passes", "water.carve_passes", "int"],
    ["#lake-depth", "water.lake_min_depth", "float"],
    ["#poi-count", "population.poi_count", "int"],
    ["#tree-density", "population.tree_density", "float"],
    ["#shrub-density", "population.shrub_density", "float"],
    ["#ore-veins", "population.ore_veins", "int"],
    ["#output-dir", "export.output_dir", "str"],
    ["#output-dir-2", "export.output_dir", "str"],
  ];

  const READOUT = {
    "#cell-size": "#cell-val", "#temp-bias": "#tb-val", "#hum-bias": "#hb-val",
    "#rain-shadow": "#rs-val", "#wind-dir": "#wd-val", "#climate-scale": "#cs-val",
    "#land-fraction": "#lf-val", "#mountain-height": "#mh-val", "#mountain-threshold": "#md-val",
    "#plateau": "#pa-val", "#cliffs": "#cl-val", "#islands": "#is-val", "#terrace": "#tr-val",
    "#diffusion": "#df-val", "#thermal": "#th-val", "#fluvial-k": "#fk-val",
    "#fluvial-iters": "#fi-val", "#droplets": "#dr-val", "#river-threshold": "#rt-val",
    "#valley-depth": "#vd-val", "#bank-flare": "#bf-val", "#meander": "#mn-val",
    "#carve-passes": "#cp-val", "#lake-depth": "#lk-val", "#poi-count": "#pc-val",
    "#tree-density": "#td-val", "#shrub-density": "#sd-val", "#ore-veins": "#ov-val",
  };

  function getPath(obj, path) {
    return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
  }
  function setPath(obj, path, value) {
    const parts = path.split(".");
    let o = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      if (o[parts[i]] == null) o[parts[i]] = {};
      o = o[parts[i]];
    }
    o[parts[parts.length - 1]] = value;
  }

  function readForm() {
    const cfg = JSON.parse(JSON.stringify(state.config));
    for (const [sel, path, type] of BINDINGS) {
      const el = $(sel);
      if (!el) continue;
      let v = el.value;
      if (type === "int") v = parseInt(v, 10) || 0;
      else if (type === "float") v = parseFloat(v) || 0;
      setPath(cfg, path, v);
    }
    cfg.population.road_connect_distance_cells = $("#roads").checked ? 90 : 0;
    cfg.export.write_level_dat = $("#exp-level").checked;
    cfg.export.generate_png_maps = $("#exp-maps").checked;
    cfg.export.structures = $("#exp-structs").checked;
    cfg.export.vegetation = $("#exp-veg").checked;
    cfg.surface.caves = $("#exp-caves").checked ? 0.55 : 0.0;
    cfg.export.bundle_maps = ["height", "biome", "climate", "population", "flow", "water", "soil",
                              "temperature", "humidity", "discharge", "hillshade", "population"];
    const ver = $("#mc-version").value;
    cfg.export.version = ver;
    cfg.export.data_version = ver === "1.21" ? 3953 : (ver === "1.20" ? 3465 : 3337);
    return cfg;
  }

  function writeForm(cfg) {
    state.config = cfg;
    for (const [sel, path, type] of BINDINGS) {
      const el = $(sel);
      if (!el) continue;
      const v = getPath(cfg, path);
      if (v === undefined || v === null) continue;
      el.value = v;
    }
    $("#roads").checked = (getPath(cfg, "population.road_connect_distance_cells") || 0) > 0;
    $("#exp-level").checked = !!getPath(cfg, "export.write_level_dat");
    $("#exp-maps").checked = !!getPath(cfg, "export.generate_png_maps");
    $("#exp-structs").checked = !!getPath(cfg, "export.structures");
    $("#exp-veg").checked = !!getPath(cfg, "export.vegetation");
    $("#exp-caves").checked = (getPath(cfg, "surface.caves") || 0) > 0;
    updateReadouts();
    updateSizeHint();
  }

  function updateReadouts() {
    for (const [sel, out] of Object.entries(READOUT)) {
      const el = $(sel), o = $(out);
      if (!el || !o) continue;
      let v = el.value;
      if (sel === "#wind-dir") v = v + "°";
      else if (sel === "#droplets") v = Number(v).toLocaleString();
      else if (sel === "#islands" || sel === "#poi-count" || sel === "#ore-veins" ||
               sel === "#thermal" || sel === "#fluvial-iters" || sel === "#carve-passes") v = v;
      else v = Number(v).toFixed(2);
      o.textContent = v;
      if (sel === "#cell-size") o.textContent = v;
    }
  }

  function updateSizeHint() {
    const bx = parseInt($("#blocks-x").value, 10) || 0;
    const bz = parseInt($("#blocks-z").value, 10) || 0;
    const cs = parseInt($("#cell-size").value, 10) || 1;
    const cells = Math.ceil(bx / cs) * Math.ceil(bz / cs);
    const chunks = Math.round((bx * bz) / 256);
    const est = (cells / 65536) * 12 + chunks * 0.014;
    $("#size-hint").innerHTML =
      `${cells.toLocaleString()} simulation cells · ${chunks.toLocaleString()} chunks · ` +
      `roughly <strong>${est < 90 ? est.toFixed(0) + "s" : (est / 60).toFixed(1) + "min"}</strong> ` +
      `(generate + export)`;
  }

  // ── generate ───────────────────────────────────────────────────────────────
  async function generate() {
    if (state.generating) return;
    const cfg = readForm();
    state.generating = true;
    setBadge("running", "generating");
    $("#btn-generate").disabled = true;
    $("#btn-cancel").disabled = false;
    $("#loading").classList.remove("hidden");
    $("#loading-text").textContent = "generating world…";
    setProgress(0, "starting");
    try {
      const job = await api("/api/generate", { method: "POST", body: JSON.stringify({ config: cfg }) });
      state.jobId = job.id;
      await poll(job.id);
    } catch (err) {
      setBadge("error", "failed");
      setProgress(0, String(err.message || err));
    } finally {
      state.generating = false;
      $("#btn-generate").disabled = false;
      $("#btn-cancel").disabled = true;
      $("#loading").classList.add("hidden");
    }
  }

  async function poll(jobId) {
    for (;;) {
      const job = await api(`/api/job/${jobId}`);
      setProgress(job.progress, job.message);
      if (job.status === "done") {
        setBadge("done", "ready");
        await afterGenerate(job);
        return job;
      }
      if (job.status === "error") {
        setBadge("error", "error");
        $("#progress-text").textContent = job.error || "generation failed";
        throw new Error(job.error || "generation failed");
      }
      if (job.status === "cancelled") {
        setBadge("idle", "cancelled");
        return job;
      }
      await new Promise((r) => setTimeout(r, 320));
    }
  }

  async function afterGenerate(job) {
    if (job.result) renderStats(job.result);
    const mesh = await api(`/api/mesh?size=192&exaggeration=1`);
    buildScene(mesh);
    loadMap($("#map-kind").value || "biome");
  }

  function setProgress(frac, text) {
    $("#progress-bar").style.width = `${Math.round(frac * 100)}%`;
    $("#progress-pct").textContent = `${Math.round(frac * 100)}%`;
    if (text) $("#progress-text").textContent = text;
  }
  function setBadge(kind, text) {
    const el = $("#job-badge");
    el.className = `badge ${kind}`;
    el.textContent = text;
  }

  // ── stats ──────────────────────────────────────────────────────────────────
  function renderStats(result) {
    const s = result.stats || {};
    $("#stats-empty").classList.add("hidden");
    $("#stats-body").classList.remove("hidden");

    const items = [
      ["height min", Math.round(s.min_height)], ["height max", Math.round(s.max_height)],
      ["mean height", Math.round(s.mean_height)], ["relief", Math.round(s.relief)],
      ["water", `${(100 * (s.water_fraction || 0)).toFixed(1)}%`], ["rivers", s.river_count],
      ["lakes", s.lake_count], ["plants", (s.tree_count || 0).toLocaleString()],
      ["settlements", s.poi_count], ["roads", s.road_count],
      ["ore veins", s.ore_vein_count], ["river cells", (s.river_cells || 0).toLocaleString()],
    ];
    $("#stat-grid").innerHTML = items
      .map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`)
      .join("");

    const rows = (result.stages || []).map(
      (st) => `<tr><td>${st.stage}</td><td>${st.seconds.toFixed(2)}s</td></tr>`);
    const total = (result.stages || []).reduce((a, b) => a + b.seconds, 0);
    rows.push(`<tr><td><strong>total generate</strong></td><td><strong>${total.toFixed(2)}s</strong></td></tr>`);
    $("#stage-table").innerHTML = rows.join("");

    const hist = {};
    const jobs = result.diagnostics || {};
    $("#poi-table").innerHTML = "";
  }

  function renderBiomes(hist) {
    const rows = Object.entries(hist || {}).slice(0, 14);
    const max = rows.length ? rows[0][1] : 1;
    $("#biome-list").innerHTML = rows.map(([name, pct]) => {
      const b = state.biomes.find((x) => x.name === name);
      const col = b ? `rgb(${b.color.join(",")})` : "#888";
      return `<div class="biome-row">
        <span class="name">${name.replace(/_/g, " ")}</span>
        <span class="bar"><i style="width:${(100 * pct / max).toFixed(1)}%;background:${col}"></i></span>
        <span class="pct">${(100 * pct).toFixed(1)}%</span></div>`;
    }).join("");
  }

  function renderPois() {
    // settlements come with the mesh payload
    const pois = (state.mesh && state.mesh.pois) || [];
    if (!pois.length) {
      $("#poi-table").innerHTML = `<tr><td class="muted">none placed</td><td></td></tr>`;
      return;
    }
    $("#poi-table").innerHTML = pois.slice(0, 20).map(
      (p) => `<tr><td>${p.name || p.kind} <span class="muted">(${p.biome.replace(/_/g, " ")})</span></td>` +
             `<td>${Math.round(p.x)}, ${Math.round(p.z)}</td></tr>`).join("");
  }

  // ── maps ───────────────────────────────────────────────────────────────────
  function loadMap(kind) {
    if (!kind) return;
    const img = $("#map-img");
    img.onload = () => { $("#map-empty").classList.add("hidden"); };
    img.onerror = () => { $("#map-empty").classList.remove("hidden"); };
    const url = `/api/maps/${kind}.png?size=1024&t=${Date.now()}`;
    img.src = url;
    $("#map-download").href = url;
  }

  // ── inspector ──────────────────────────────────────────────────────────────
  async function inspect(x, z) {
    try {
      const info = await api(`/api/inspect?x=${x}&z=${z}`);
      $("#insp-x").value = Math.round(x);
      $("#insp-z").value = Math.round(z);
      const kv = [
        ["biome", info.biome.replace(/_/g, " ")],
        ["surface Y", info.surface_y],
        ["water", info.water_kind + (info.water_level != null ? ` @ ${Math.round(info.water_level)}` : "")],
        ["temperature", info.temperature.toFixed(3)],
        ["humidity", info.humidity.toFixed(3)],
        ["continentality", info.continentality.toFixed(3)],
        ["fertility", info.fertility.toFixed(3)],
        ["population", info.population.toFixed(3)],
        ["discharge", Math.round(info.discharge).toLocaleString()],
        ["flow dir", `(${info.flow[0].toFixed(2)}, ${info.flow[1].toFixed(2)})`],
        ["soil depth", `${Math.round(info.soil_depth)} blocks`],
        ["top block", info.profile.top],
        ["filler", info.profile.filler],
        ["stone", info.profile.stone],
      ];
      $("#inspect-body").innerHTML =
        `<div class="kv">${kv.map(([k, v]) => `<span class="k">${k}</span><span class="v">${v}</span>`).join("")}</div>`;
      placeMarker(x, info.surface_y, z, info.biome);
    } catch (err) {
      $("#inspect-body").innerHTML = `<span class="muted">${err.message}</span>`;
    }
  }

  function placeMarker(x, y, z, biome) {
    if (marker) {
      world.remove(marker);
      marker.geometry.dispose();
      marker.material.dispose();
    }
    const geo = new THREE.ConeGeometry(7, 22, 4);
    geo.rotateX(Math.PI);
    const mat = new THREE.MeshBasicMaterial({ color: 0xffe066 });
    marker = new THREE.Mesh(geo, mat);
    marker.position.set(x, y + 24, z);
    world.add(marker);
  }

  // ── pointer interaction ────────────────────────────────────────────────────
  const viewportEl = $("#viewport");
  viewportEl.addEventListener("pointerup", (e) => {
    if (!terrainMesh || e.button !== 0) return;
    if (!controls.wasClick()) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObject(terrainMesh, false);
    if (hits.length) {
      const p = hits[0].point;
      inspect(p.x, p.z);
      switchTab("inspect");
    }
  });

  viewportEl.addEventListener("pointermove", (e) => {
    const tip = $("#hover-tip");
    if (!terrainMesh) { tip.classList.add("hidden"); return; }
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObject(terrainMesh, false);
    if (!hits.length) { tip.classList.add("hidden"); return; }
    const p = hits[0].point;
    tip.classList.remove("hidden");
    tip.style.left = `${e.clientX - rect.left + 14}px`;
    tip.style.top = `${e.clientY - rect.top + 12}px`;
    tip.textContent = `x ${Math.round(p.x)}  z ${Math.round(p.z)}  y ${Math.round(p.y)}`;
  });

  // ── export ─────────────────────────────────────────────────────────────────
  async function doExport() {
    const dir = $("#output-dir-2").value || $("#output-dir").value;
    if (!dir) {
      setExportHint("Pick an output folder first (e.g. C:\\Users\\you\\.minecraft\\saves).", true);
      switchTab("export");
      return;
    }
    const cfg = readForm();
    const options = Object.assign({}, cfg.export, {
      world_name: cfg.name,
      output_dir: dir,
    });
    try {
      setExportHint("exporting…");
      setBadge("running", "exporting");
      const job = await api("/api/export", { method: "POST", body: JSON.stringify({ output_dir: dir, options }) });
      for (;;) {
        const j = await api(`/api/job/${job.id}`);
        setProgress(j.progress, j.message);
        setExportHint(`${j.message} (${Math.round(j.progress * 100)}%)`);
        if (j.status === "done") {
          const r = j.result || {};
          setExportHint(
            `✔ ${r.chunks} chunks, ${Number(r.blocks || 0).toLocaleString()} blocks → ${r.world_dir}` +
            ` (${r.seconds}s). Copy that folder into .minecraft/saves and open it.`);
          setBadge("done", "exported");
          break;
        }
        if (j.status === "error") {
          setExportHint(j.error || "export failed", true);
          setBadge("error", "error");
          break;
        }
        await new Promise((r) => setTimeout(r, 300));
      }
    } catch (err) {
      setExportHint(String(err.message || err), true);
      setBadge("error", "error");
    }
  }
  function setExportHint(text, bad) {
    const el = $("#export-hint");
    el.textContent = text;
    el.style.color = bad ? "var(--danger)" : "var(--muted)";
    $("#export-result").textContent = text;
  }

  // ── tabs ───────────────────────────────────────────────────────────────────
  function switchTab(name) {
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $$(".tab-body").forEach((b) => b.classList.toggle("hidden", b.id !== `tab-${name}`));
  }

  // ── wire everything up ─────────────────────────────────────────────────────
  async function init() {
    try {
      const data = await api("/api/config");
      state.presets = data.presets;
      state.biomes = data.biomes;
      writeForm(data.config);

      $("#preset").innerHTML = data.presets
        .map((p) => `<option value="${p.id}">${p.label}</option>`).join("");
      $("#preset").value = data.config.preset || "cinematic";
      showPresetDesc();

      $("#map-kind").innerHTML = (data.config.preview.maps || ["height", "biome"])
        .map((m) => `<option value="${m}">${m}</option>`).join("");
    } catch (err) {
      setProgress(0, `cannot reach the generator server: ${err.message}`);
    }

    $("#btn-generate").addEventListener("click", generate);
    $("#btn-cancel").addEventListener("click", async () => {
      if (state.jobId) await api(`/api/cancel/${state.jobId}`, { method: "POST" });
    });
    $("#btn-dice").addEventListener("click", () => {
      $("#seed").value = Math.floor(Math.random() * 2147483647);
    });
    $("#btn-export").addEventListener("click", doExport);
    $("#btn-export-2").addEventListener("click", doExport);

    $("#preset").addEventListener("change", async () => {
      const preset = $("#preset").value;
      showPresetDesc();
      // presets are applied server-side so the UI and the generator never disagree
      const res = await api(`/api/preset/${preset}`);
      const keepName = $("#world-name").value;
      const keepSeed = $("#seed").value;
      const keepDir = $("#output-dir").value;
      writeForm(res.config);
      $("#world-name").value = keepName;
      $("#seed").value = keepSeed;
      $("#output-dir").value = keepDir;
      $("#output-dir-2").value = keepDir;
      if (res.warnings && res.warnings.length) setProgress(0, res.warnings.join(" · "));
    });

    BINDINGS.forEach(([sel]) => {
      const el = $(sel);
      if (!el) return;
      el.addEventListener("input", () => { updateReadouts(); updateSizeHint(); syncOutputDirs(sel); });
      el.addEventListener("change", () => { updateReadouts(); updateSizeHint(); syncOutputDirs(sel); });
    });

    $("#show-water").addEventListener("change", (e) => { if (waterMesh) waterMesh.visible = e.target.checked; });
    $("#show-rivers").addEventListener("change", (e) => toggleOverlay("rivers", e.target.checked));
    $("#show-roads").addEventListener("change", (e) => toggleOverlay("roads", e.target.checked));
    $("#show-pois").addEventListener("change", (e) => toggleOverlay("pois", e.target.checked));
    $("#wireframe").addEventListener("change", (e) => {
      if (terrainMesh) terrainMesh.material.wireframe = e.target.checked;
    });

    $("#color-mode").addEventListener("change", async (e) => {
      state.colorMode = e.target.value;
      applyColorMode(state.colorMode);
    });

    $("#exaggeration").addEventListener("change", async (e) => {
      const v = parseFloat(e.target.value);
      $("#exag-val").textContent = `${v.toFixed(2)}×`;
      const mesh = await api(`/api/mesh?size=192&exaggeration=${v}`);
      buildScene(mesh);
    });

    const updateSun = () => {
      const az = (parseFloat($("#sun-azimuth").value) * Math.PI) / 180;
      const el = (parseFloat($("#sun-height").value) * Math.PI) / 180;
      sun.position.set(Math.cos(el) * Math.sin(az) * 100,
                       Math.sin(el) * 100,
                       Math.cos(el) * Math.cos(az) * 100);
    };
    $("#sun-azimuth").addEventListener("input", updateSun);
    $("#sun-height").addEventListener("input", updateSun);

    $("#btn-view-top").addEventListener("click", () => {
      const b = state.mesh && state.mesh.bounds;
      if (!b) return;
      controls.frame(new THREE.Vector3((b.x0 + b.x1) / 2, (b.min_y + b.max_y) / 2, (b.z0 + b.z1) / 2),
        Math.max(b.x1 - b.x0, b.z1 - b.z0) * 0.8, 0, 2);
    });
    $("#btn-view-iso").addEventListener("click", () => {
      const b = state.mesh && state.mesh.bounds;
      if (!b) return;
      controls.frame(new THREE.Vector3((b.x0 + b.x1) / 2, (b.min_y + b.max_y) / 2, (b.z0 + b.z1) / 2),
        Math.max(b.x1 - b.x0, b.z1 - b.z0) * 0.95, -35, 58);
    });
    $("#btn-view-seaside").addEventListener("click", () => {
      const b = state.mesh && state.mesh.bounds;
      if (!b) return;
      controls.frame(new THREE.Vector3((b.x0 + b.x1) / 2, state.mesh.sea_level, (b.z0 + b.z1) / 2),
        Math.max(b.x1 - b.x0, b.z1 - b.z0) * 0.6, 40, 78);
    });
    $("#btn-export-png").addEventListener("click", () => {
      renderer.render(scene, camera);
      const url = renderer.domElement.toDataURL("image/png");
      const a = document.createElement("a");
      a.href = url;
      a.download = `mapgen_view_${Date.now()}.png`;
      a.click();
    });

    $("#map-kind").addEventListener("change", (e) => loadMap(e.target.value));
    $("#btn-map-reload").addEventListener("click", () => loadMap($("#map-kind").value));

    $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
    $("#insp-x").addEventListener("change", () => inspect(+$("#insp-x").value, +$("#insp-z").value));
    $("#insp-z").addEventListener("change", () => inspect(+$("#insp-x").value, +$("#insp-z").value));

    resize();
    updateSun();
    updateReadouts();
    updateSizeHint();
  }

  function toggleOverlay(name, visible) {
    const g = overlayGroup.getObjectByName(name);
    if (g) g.visible = visible;
  }

  function syncOutputDirs(sel) {
    if (sel === "#output-dir") $("#output-dir-2").value = $("#output-dir").value;
    if (sel === "#output-dir-2") $("#output-dir").value = $("#output-dir-2").value;
  }

  function showPresetDesc() {
    const p = state.presets.find((x) => x.id === $("#preset").value);
    $("#preset-desc").textContent = p ? p.description : "";
  }

  init();
})();
