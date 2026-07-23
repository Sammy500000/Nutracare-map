/* Nutracare India Map — frontend engine.
 * Loads district / hospital / constituency GeoJSON produced by pipeline/build_data.py
 * and powers choropleths, overlays, and the draw-to-aggregate feature.
 * Degrades gracefully when a data file is not yet present. */

'use strict';

// ---- Basemap: CARTO Voyager raster (free, reliable, no API key) + OpenMapTiles glyphs.
// Raster sources have no TileJSON/sprite dependency, so style.load fires fast and the app
// never stalls waiting on the basemap. Labels use the glyphs endpoint below. ----
const BASEMAP_STYLE = {
  version: 8,
  glyphs: 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf',
  sources: {
    carto: {
      type: 'raster',
      tiles: [
        'https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
        'https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
        'https://c.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
        'https://d.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
      ],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors, © CARTO',
    },
  },
  layers: [{ id: 'carto-base', type: 'raster', source: 'carto' }],
};
const MAHARASHTRA_CENTER = [76.5, 19.2];

const map = new maplibregl.Map({
  container: 'map',
  style: BASEMAP_STYLE,
  center: MAHARASHTRA_CENTER,
  zoom: 6,
  maxZoom: 18,
  attributionControl: true,
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'bottom-right');
map.addControl(new maplibregl.ScaleControl({ maxWidth: 140, unit: 'metric' }), 'bottom-left');

// ---- App state ----
const State = {
  districts: null,        // GeoJSON FeatureCollection
  hospitals: null,
  constituencies: null,
  manifest: null,
  metric: 'income_pci',
  drawing: false,
  drawPts: [],            // [lng,lat] vertices being drawn
  drawnPolygon: null,     // closed GeoJSON polygon
};

// ---- Metric definitions: property + label + color ramp ----
const METRICS = {
  income_pci:  { prop: 'pci', label: 'Est. Per-Capita Income (₹/yr)', fmt: fmtINR, ramp: 'sequential', stops: [50000,100000,150000,200000,300000] },
  income_ultra:{ prop: 'inc_hig_ultra', label: '% Households HIG + Ultra-HIG', fmt: v=>v+'%', ramp: 'sequential', stops: [5,10,15,25,40] },
  pop_2027:    { prop: 'pop_2027', label: 'Population (2027 est)', fmt: fmtNum, ramp: 'sequential', stops: [500000,1500000,3000000,6000000,9000000] },
  women_2035:  { prop: 'women_2035', label: 'Women 20–35 (est)', fmt: fmtNum, ramp: 'sequential', stops: [100000,300000,600000,1000000,2000000] },
  poshan_stunting: { prop: 'poshan_stunting', label: 'Stunting % (Poshan)', fmt: v=>v+'%', ramp: 'diverging', stops: [10,18,25,32,40] },
  poshan_beneficiaries: { prop: 'poshan_beneficiaries', label: 'Poshan Beneficiaries', fmt: fmtNum, ramp: 'sequential', stops: [80000,150000,250000,400000,600000] },
};
const STRATA_COLORS = { S1:'#1a9850', S2:'#91cf60', S3:'#fee08b', S4:'#fc8d59' };
const SEQ = ['#0d2f4f','#15527f','#1f77b4','#5fa8e0','#a8d4f5'];      // dark→light
const DIV = ['#1a9850','#91cf60','#fee08b','#fc8d59','#d73027'];      // good→bad

// ---- Helpers ----
function fmtNum(n){ if(n==null||isNaN(n)) return '—'; return Math.round(n).toLocaleString('en-IN'); }
function fmtINR(n){ if(n==null||isNaN(n)) return '—'; return '₹'+Math.round(n).toLocaleString('en-IN'); }
// The choropleth is painted at fill-opacity over the light basemap, so a district reads
// LIGHTER than its raw ramp colour. Legend chips must show that same composited colour or
// they look darker/more saturated than the map. Mix each ramp colour over the basemap land
// tone at the layer's actual fill-opacity so the chip == what the district shows on land.
const CHORO_OPACITY = 0.62;        // keep in sync with district-fill 'fill-opacity'
const LAND_RGB = [243, 239, 231];  // CARTO Voyager land tone (light cream)
function _hexRgb(h){ h=h.replace('#',''); return [parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)]; }
function swatchColor(hex){
  let a = CHORO_OPACITY;
  try{ if(map&&map.getLayer&&map.getLayer('district-fill')){ const o=map.getPaintProperty('district-fill','fill-opacity'); if(typeof o==='number') a=o; } }catch(e){}
  const [r,g,b]=_hexRgb(hex);
  const mix=(c,l)=>Math.round(a*c + (1-a)*l);
  return `rgb(${mix(r,LAND_RGB[0])}, ${mix(g,LAND_RGB[1])}, ${mix(b,LAND_RGB[2])})`;
}
function toast(msg, ms){ const t=document.getElementById('status-toast'); t.textContent=msg; t.classList.remove('hidden'); if(ms){ clearTimeout(t._t); t._t=setTimeout(()=>t.classList.add('hidden'),ms);} }
function hideToast(){ document.getElementById('status-toast').classList.add('hidden'); }
async function loadJSON(url){ try{ const r=await fetch(url); if(!r.ok) return null; return await r.json(); }catch(e){ return null; } }

// Build a MapLibre step expression for a numeric metric.
function colorExpr(metricKey){
  const m = METRICS[metricKey];
  const palette = m.ramp === 'diverging' ? DIV : SEQ;
  const expr = ['step', ['coalesce', ['get', m.prop], -1]];
  expr.push('#3a4450'); // value for -1 / missing
  m.stops.forEach((s,i)=>{ expr.push(s, palette[Math.min(i, palette.length-1)]); });
  return expr;
}

// ---- Load data + init layers ----
async function init(){
  toast('Loading data…');
  State.manifest = await loadJSON('data/manifest.json');
  State.districts = await loadJSON('data/districts.geojson');
  State.hospitals = await loadJSON('data/hospitals.geojson');
  State.constituencies = await loadJSON('data/constituencies.geojson');

  const disc = document.getElementById('data-disclaimer');
  if (State.manifest && State.manifest.disclaimer) disc.textContent = State.manifest.disclaimer;
  else disc.textContent = 'Income brackets are modeled estimates (Census 2011 + SECC + NFHS + tax brackets), not individual records.';

  buildSearchIndex();
  State._dataReady = true;
  addLayersWhenReady();
}

// Add map layers once BOTH the data is loaded and the basemap style is ready.
let _layersAdded = false;
function addLayersWhenReady(){
  if (_layersAdded || !State._dataReady) return;
  if (!map.isStyleLoaded()) { map.once('idle', addLayersWhenReady); return; }
  _layersAdded = true;
  if (State.districts) { addDistrictLayers(); setMetric(State.metric); fitToData(); }
  else toast('No district data yet — run pipeline/build_data.py to populate /data', 6000);
  if (State.hospitals) addHospitalLayers();
  if (State.constituencies) addPoliticalLayers();
  hideToast();
}

function fitToData(){
  try{
    const b = turf.bbox(State.districts);
    map.fitBounds([[b[0],b[1]],[b[2],b[3]]], { padding: 60, duration: 800 });
  }catch(e){}
}

// ---------- District choropleth ----------
function addDistrictLayers(){
  map.addSource('districts', { type:'geojson', data: State.districts });
  map.addLayer({ id:'district-fill', type:'fill', source:'districts',
    paint:{ 'fill-color':'#3a4450', 'fill-opacity':0.62 } });
  map.addLayer({ id:'district-line', type:'line', source:'districts',
    paint:{ 'line-color':'#ffffff', 'line-width':0.8, 'line-opacity':0.7 } });
  // selection highlight (used by the encircle tool)
  map.addLayer({ id:'district-selected', type:'line', source:'districts',
    paint:{ 'line-color':'#111', 'line-width':2.6 }, filter:['in',['get','name'],['literal',[]]] });
  map.addLayer({ id:'district-hover', type:'line', source:'districts',
    paint:{ 'line-color':'#1d4ed8', 'line-width':2.4 }, filter:['==','name',''] });
  map.addLayer({ id:'district-label', type:'symbol', source:'districts', minzoom:6.3,
    layout:{ 'text-field':['get','name'], 'text-size':['interpolate',['linear'],['zoom'],6,10,9,13],
             'text-font':['Noto Sans Regular'] },
    paint:{ 'text-color':'#10202e','text-halo-color':'#ffffff','text-halo-width':1.6 } });

  map.on('mousemove','district-fill',(e)=>{
    if(!e.features.length) return;
    map.getCanvas().style.cursor='pointer';
    map.setFilter('district-hover',['==','name', e.features[0].properties.name]);
  });
  map.on('mouseleave','district-fill',()=>{ map.getCanvas().style.cursor=''; map.setFilter('district-hover',['==','name','']); });
  map.on('click','district-fill',(e)=>{ if(State.drawing) return; districtPopup(e.features[0], e.lngLat); });
}

function setMetric(key){
  State.metric = key;
  if(!map.getLayer('district-fill')) return;
  if(key === 'none'){ map.setPaintProperty('district-fill','fill-color','#3a4450'); }
  else if(key === 'income_strata'){
    map.setPaintProperty('district-fill','fill-color',
      ['match',['get','strata'],'S1',STRATA_COLORS.S1,'S2',STRATA_COLORS.S2,'S3',STRATA_COLORS.S3,'S4',STRATA_COLORS.S4,'#3a4450']);
  } else if(METRICS[key]){
    map.setPaintProperty('district-fill','fill-color', colorExpr(key));
  }
  renderLegend(key);
}

function renderLegend(key){
  const el = document.getElementById('legend');
  if(key === 'none'){ el.innerHTML='<small class="disclaimer">Boundaries only.</small>'; return; }
  if(key === 'income_strata'){
    const labels={S1:'S1 · High income',S2:'S2 · Upper-mid',S3:'S3 · Mid',S4:'S4 · Lower'};
    el.innerHTML = Object.keys(STRATA_COLORS).map(s=>
      `<div class="legend-row"><span class="legend-swatch" style="background:${swatchColor(STRATA_COLORS[s])}"></span>${labels[s]}</div>`).join('');
    return;
  }
  const m = METRICS[key]; if(!m){ el.innerHTML=''; return; }
  const palette = m.ramp==='diverging'?DIV:SEQ;
  // Must mirror colorExpr() exactly: values below the first stop render in the
  // base gray (#3a4450); [stops[i], stops[i+1]) -> palette[i]; ≥ last stop -> last colour.
  let rows = `<div class="legend-row" style="margin-bottom:4px"><strong>${m.label}</strong></div>`;
  rows += `<div class="legend-row"><span class="legend-swatch" style="background:${swatchColor('#3a4450')}"></span>&lt; ${m.fmt(m.stops[0])}</div>`;
  for(let i=0;i<m.stops.length;i++){
    const c = palette[Math.min(i,palette.length-1)];
    const label = (i < m.stops.length-1)
      ? `${m.fmt(m.stops[i])} – ${m.fmt(m.stops[i+1])}`
      : `≥ ${m.fmt(m.stops[i])}`;
    rows += `<div class="legend-row"><span class="legend-swatch" style="background:${swatchColor(c)}"></span>${label}</div>`;
  }
  el.innerHTML = rows;
}

function districtPopup(f, lngLat){
  const p = f.properties;
  const row=(k,v)=>`<div class="popup-row"><span class="k">${k}</span><span>${v}</span></div>`;
  let html = `<h4>${p.name}</h4>`;
  if(p.division) html += row('Division', p.division);
  if(p.strata) html += row('Strata', `${p.strata} · ${p.strata_label||''}`);
  if(p.pci!=null) html += row('Est. PCI', fmtINR(p.pci));
  if(p.pop_2027!=null) html += row('Population (2027)', fmtNum(p.pop_2027));
  if(p.women_2035!=null) html += row('Women 20–35', fmtNum(p.women_2035));
  if(p.literacy!=null) html += row('Literacy', p.literacy+'%');
  if(p.inc_ews!=null){
    html += `<div style="margin-top:6px"><span class="k">Income brackets (% households)</span>${bracketBar(p)}</div>`;
  }
  if(p.poshan_beneficiaries!=null){
    html += `<div style="margin-top:6px"><span class="k">Poshan</span>`;
    html += row('Beneficiaries', fmtNum(p.poshan_beneficiaries));
    if(p.poshan_stunting!=null) html += row('Stunting', p.poshan_stunting+'%');
    html += `</div>`;
  }
  const hc = hospitalCountsForDistrict(p.name);
  if(hc && hc.total){
    html += `<div style="margin-top:6px"><span class="k">Hospitals (${hc.total})</span>`;
    html += row('Government', fmtNum(hc.Government));
    html += row('Private', fmtNum(hc.Private));
    html += row('Maternity / Neonatal', fmtNum(hc.Maternity));
    const ig=hc.income, igParts=[];
    if(ig.HIGH) igParts.push('High '+ig.HIGH);
    if(ig.UPPER_MID) igParts.push('Upper-mid '+ig.UPPER_MID);
    if(ig.MID) igParts.push('Mid '+ig.MID);
    if(ig.LOW) igParts.push('Low '+ig.LOW);
    if(igParts.length) html += row('By income', igParts.join(' · '));
    html += `</div>`;
  }
  new maplibregl.Popup({maxWidth:'300px'}).setLngLat(lngLat).setHTML(html).addTo(map);
}

// Count hospitals in a district (by the canonical district name, which the pipeline
// assigns via point-in-polygon so both the scraped layer and the research layer agree).
function hospitalCountsForDistrict(name){
  if(!State.hospitals) return null;
  const c={total:0,Government:0,Private:0,Maternity:0,income:{HIGH:0,UPPER_MID:0,MID:0,LOW:0}};
  State.hospitals.features.forEach(f=>{
    if(f.properties.district===name){
      c.total++; const t=f.properties.type; if(c[t]!=null) c[t]++;
      const ig=f.properties.income_group; if(ig&&c.income[ig]!=null) c.income[ig]++;
    }
  });
  return c;
}

const BRACKETS = [
  ['inc_ews','EWS','#d73027'],['inc_lig','LIG','#fc8d59'],['inc_miga','MIG-A','#fee08b'],
  ['inc_migb','MIG-B','#d9ef8b'],['inc_hig','HIG','#91cf60'],['inc_ultra','Ultra','#1a9850'],
];
function bracketBar(p){
  let bar='<div class="bracket-bar">';
  let legend='<div style="font-size:11px;margin-top:3px;display:flex;flex-wrap:wrap;gap:6px">';
  BRACKETS.forEach(([k,lab,c])=>{
    const v = p[k]||0;
    bar += `<span class="bracket-seg" style="width:${v}%;background:${c}" title="${lab} ${v}%"></span>`;
    legend += `<span style="white-space:nowrap"><span style="display:inline-block;width:9px;height:9px;background:${c};border-radius:2px"></span> ${lab} ${v}%</span>`;
  });
  return bar+'</div>'+legend+'</div>';
}

// ---------- Hospitals ----------
// No clustering — every hospital node is shown. Radius scales with zoom so the full
// distribution is visible state-wide and individual pins are clickable when zoomed in.
function hospitalMatch(p){
  const allowed = Array.from(document.querySelectorAll('.hfilter:checked')).map(c=>c.value);
  const inc = (document.getElementById('income-filter')||{}).value || 'all';
  const own = (document.getElementById('ownership-filter')||{}).value || 'all';
  if(!allowed.includes(p.type)) return false;
  if(inc!=='all' && p.income_group!==inc) return false;
  if(own!=='all' && p.ownership!==own) return false;
  return true;
}
function addHospitalLayers(){
  map.addSource('hospitals', { type:'geojson', data: State.hospitals });
  map.addLayer({ id:'hosp-point', type:'circle', source:'hospitals',
    paint:{
      'circle-radius':['interpolate',['linear'],['zoom'], 6,1.6, 9,3, 12,5, 15,7],
      'circle-color':['match',['get','type'],'Government','#1d4ed8','Maternity','#db2777','#ea8a1f'],
      'circle-stroke-color':'#ffffff','circle-stroke-width':['interpolate',['linear'],['zoom'],6,0.3,12,1],
      'circle-opacity':0.9 }, layout:{visibility:'none'} });

  map.on('click','hosp-point',(e)=>{
    if(State.drawing) return;
    const p=e.features[0].properties;
    const row=(k,v)=>v?`<div class="popup-row"><span class="k">${k}</span><span>${v}</span></div>`:'';
    const rating=p.rating?`★ ${p.rating}${p.reviews?` (${p.reviews})`:''}`:'';
    const site=p.website?`<a href="${p.website}" target="_blank" rel="noopener">website</a>`:'';
    const maps=p.maps_link?`<a href="${p.maps_link}" target="_blank" rel="noopener">Google Maps ↗</a>`:'';
    const incLbl={HIGH:'High',UPPER_MID:'Upper-mid',MID:'Mid',LOW:'Low'}[p.income_group]||p.income_group;
    const ownLbl=p.ownership?p.ownership.charAt(0)+p.ownership.slice(1).toLowerCase().replace('_','-'):'';
    const srcLbl=p.source==='research_sheet'?'research sheet'+((p.approx===true||p.approx==='true')?' · approx location':''):p.source;
    new maplibregl.Popup({maxWidth:'300px'}).setLngLat(e.lngLat).setHTML(
      `<h4>${p.name}</h4>`+
      row('Type',p.type)+row('Category',p.category)+row('District',p.district)+
      row('Income group',incLbl)+row('Ownership',ownLbl)+
      row('Rating',rating)+row('Phone',p.phone)+row('Address',p.location)+
      ((site||maps)?`<div class="popup-row" style="gap:10px;margin-top:4px">${site} ${maps}</div>`:'')+
      (srcLbl?`<div style="font-size:10.5px;color:var(--muted);margin-top:4px">source: ${srcLbl}</div>`:'')
    ).addTo(map);
  });
  map.on('mouseenter','hosp-point',()=>map.getCanvas().style.cursor='pointer');
  map.on('mouseleave','hosp-point',()=>map.getCanvas().style.cursor='');
}
function setHospitalsVisible(v){
  if(map.getLayer('hosp-point')) map.setLayoutProperty('hosp-point','visibility', v?'visible':'none');
  document.getElementById('hospital-filters').classList.toggle('hidden', !v);
  if(v) updateHospCount();
}
function updateHospCount(){
  if(!State.hospitals) return;
  const feats=State.hospitals.features.filter(f=>hospitalMatch(f.properties)), c={Government:0,Private:0,Maternity:0};
  feats.forEach(f=>{const t=f.properties.type; if(c[t]!=null)c[t]++;});
  const total=State.hospitals.features.length, shown=feats.length;
  const prefix = shown<total ? `${fmtNum(shown)} of ${fmtNum(total)} hospitals` : `${fmtNum(total)} hospitals`;
  document.getElementById('hosp-count').textContent =
    `${prefix} · ${fmtNum(c.Government)} govt · ${fmtNum(c.Private)} private · ${fmtNum(c.Maternity)} maternity`;
}
function applyHospitalFilter(){
  const allowed = Array.from(document.querySelectorAll('.hfilter:checked')).map(c=>c.value);
  const inc = (document.getElementById('income-filter')||{}).value || 'all';
  const own = (document.getElementById('ownership-filter')||{}).value || 'all';
  const preds = [['in',['get','type'],['literal',allowed]]];
  if(inc!=='all') preds.push(['==',['get','income_group'],inc]);
  if(own!=='all') preds.push(['==',['get','ownership'],own]);
  if(map.getLayer('hosp-point')) map.setFilter('hosp-point',['all',...preds]);
  updateHospCount();
}

// ---------- Political ----------
function addPoliticalLayers(){
  map.addSource('constituencies', { type:'geojson', data: State.constituencies });
  map.addLayer({ id:'ac-fill', type:'fill', source:'constituencies',
    paint:{ 'fill-color':['coalesce',['get','party_color'],'#888'],'fill-opacity':0.35 }, layout:{visibility:'none'} });
  map.addLayer({ id:'ac-line', type:'line', source:'constituencies',
    paint:{ 'line-color':'#475569','line-width':0.7,'line-opacity':0.8 }, layout:{visibility:'none'} });
  map.on('click','ac-fill',(e)=>{
    const p=e.features[0].properties;
    const row=(k,v)=>v?`<div class="popup-row"><span class="k">${k}</span><span>${v}</span></div>`:'';
    new maplibregl.Popup().setLngLat(e.lngLat).setHTML(
      `<h4>${p.ac_name} (AC ${p.ac_no||''})</h4>${row('District',p.district)}${row('MLA',p.mla)}${row('Party',p.party)}${p.is_minister?row('Minister',p.portfolio||'Yes'):''}`
    ).addTo(map);
  });
}
function setPoliticalVisible(v){
  ['ac-fill','ac-line'].forEach(id=>{ if(map.getLayer(id)) map.setLayoutProperty(id,'visibility', v?'visible':'none'); });
}

// ---------- Draw + aggregate ----------
function ensureDrawSource(){
  if(!map.getSource('draw')){
    map.addSource('draw',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id:'draw-fill',type:'fill',source:'draw',paint:{'fill-color':'#4ea1ff','fill-opacity':0.12}});
    map.addLayer({id:'draw-line',type:'line',source:'draw',paint:{'line-color':'#4ea1ff','line-width':2,'line-dasharray':[2,1]}});
    map.addLayer({id:'draw-vert',type:'circle',source:'draw',filter:['==','$type','Point'],paint:{'circle-radius':4,'circle-color':'#4ea1ff','circle-stroke-color':'#fff','circle-stroke-width':1}});
  }
}
function updateDrawPreview(){
  ensureDrawSource();
  const feats=[];
  State.drawPts.forEach(pt=>feats.push({type:'Feature',geometry:{type:'Point',coordinates:pt},properties:{}}));
  if(State.drawPts.length>=2) feats.push({type:'Feature',geometry:{type:'LineString',coordinates:State.drawPts},properties:{}});
  if(State.drawPts.length>=3) feats.push({type:'Feature',geometry:{type:'Polygon',coordinates:[[...State.drawPts,State.drawPts[0]]]},properties:{}});
  map.getSource('draw').setData({type:'FeatureCollection',features:feats});
}
function setDrawBtn(label, active){
  const b=document.getElementById('draw-polygon');
  b.textContent=label; b.classList.toggle('active', !!active);
}
function startDrawing(){
  State.drawing=true; State.drawPts=[]; State.drawnPolygon=null;
  setDrawBtn('✓ Finish', true);
  map.getCanvas().style.cursor='crosshair';
  toast('Tap the map to add points, then tap “Finish”');
  // on mobile, auto-collapse the controls drawer so the map is usable
  if(window.matchMedia('(max-width: 860px)').matches) document.getElementById('controls').classList.add('collapsed');
}
function stopDrawing(finish){
  State.drawing=false;
  setDrawBtn('▱ Draw area', false);
  map.getCanvas().style.cursor='';
  if(finish && State.drawPts.length>=3){
    State.drawnPolygon=turf.polygon([[...State.drawPts,State.drawPts[0]]]);
    updateDrawPreview();
    aggregate();
  } else if(finish){
    toast('Add at least 3 points to make an area', 2500);
  }
  hideToast();
}
function clearDraw(){
  State.drawing=false; State.drawPts=[]; State.drawnPolygon=null;
  setDrawBtn('▱ Draw area', false);
  if(map.getSource('draw')) map.getSource('draw').setData({type:'FeatureCollection',features:[]});
  if(map.getLayer('district-selected')) map.setFilter('district-selected',['in',['get','name'],['literal',[]]]);
  document.getElementById('stats-panel').classList.add('hidden');
  map.getCanvas().style.cursor='';
}

map.on('click',(e)=>{ if(!State.drawing) return; State.drawPts.push([e.lngLat.lng,e.lngLat.lat]); updateDrawPreview(); });
map.on('dblclick',(e)=>{ if(!State.drawing) return; e.preventDefault(); stopDrawing(true); });

// Core aggregation: area-weighted district sums + point-in-polygon hospitals + constituencies.
function aggregate(){
  const poly = State.drawnPolygon; if(!poly) return;
  const agg = { pop_2027:0, women_2035:0, households:0,
    poshan_beneficiaries:0, poshan_pregnant:0, poshan_lactating:0,
    brackets:{inc_ews:0,inc_lig:0,inc_miga:0,inc_migb:0,inc_hig:0,inc_ultra:0},
    stunting_w:0, lit_w:0, pop_for_w:0, districts:[] };

  // A district is SELECTED (counted in FULL — no fraction weighting) if the drawn shape
  // touches it AT ALL — any overlap, however small, pulls in the whole district.
  const selectedNames = new Set();
  if(State.districts){
    const selectedFills=[];
    turf.featureEach(State.districts,(f)=>{
      let selected=false;
      try{ selected = turf.booleanIntersects(poly,f); }catch(err){ return; }
      if(!selected) return;
      const p=f.properties;
      const pop=p.pop_2027||0;
      agg.pop_2027+=pop;
      agg.women_2035+=(p.women_2035||0);
      agg.households+=(p.households||0);
      agg.poshan_beneficiaries+=(p.poshan_beneficiaries||0);
      agg.poshan_pregnant+=(p.poshan_pregnant||0);
      agg.poshan_lactating+=(p.poshan_lactating||0);
      const hh=(p.households||0);
      ['inc_ews','inc_lig','inc_miga','inc_migb','inc_hig','inc_ultra'].forEach(k=>{ agg.brackets[k]+=hh*((p[k]||0)/100); });
      if(p.poshan_stunting!=null){ agg.stunting_w+=p.poshan_stunting*pop; }
      if(p.literacy!=null){ agg.lit_w+=p.literacy*pop; }
      agg.pop_for_w+=pop;
      agg.districts.push({name:p.name});
      selectedFills.push(p.name);
      selectedNames.add(p.name);
    });
    // highlight selected districts on the map
    if(map.getLayer('district-selected')) map.setFilter('district-selected',['in',['get','name'],['literal',selectedFills]]);
  }

  // Hospitals counted if they belong to a SELECTED Maharashtra district (counted in full,
  // consistent with the demographics) OR fall geographically inside the drawn shape. The
  // point-in-polygon arm makes the tool work ALL-INDIA — drawing over any state consolidates
  // its hospitals even where there is no district polygon/demographic layer.
  const hosp={Government:0,Private:0,Maternity:0,Other:0,total:0,categories:{},
    income:{HIGH:0,UPPER_MID:0,MID:0,LOW:0}, ownership:{}};
  if(State.hospitals){
    turf.featureEach(State.hospitals,(f)=>{
      try{
        const inSel = (selectedNames.size>0 && selectedNames.has(f.properties.district))
          || turf.booleanPointInPolygon(f,poly);
        if(inSel){
          hosp.total++; const t=f.properties.type; hosp[t!=null&&hosp[t]!=null?t:'Other']++;
          const cat=f.properties.category||'(uncategorised)'; hosp.categories[cat]=(hosp.categories[cat]||0)+1;
          const ig=f.properties.income_group; if(ig&&hosp.income[ig]!=null) hosp.income[ig]++;
          const ow=f.properties.ownership||'OTHER'; hosp.ownership[ow]=(hosp.ownership[ow]||0)+1;
        }
      }catch(e){}
    });
  }
  // Constituencies inside (centroid)
  const acs=[];
  if(State.constituencies){
    turf.featureEach(State.constituencies,(f)=>{
      try{ const c=turf.centroid(f); if(turf.booleanPointInPolygon(c,poly)) acs.push(f.properties); }catch(e){}
    });
  }
  renderStats(agg,hosp,acs);
}

function renderStats(agg,hosp,acs){
  const panel=document.getElementById('stats-panel');
  const body=document.getElementById('stats-body');
  const stunting = agg.pop_for_w? (agg.stunting_w/agg.pop_for_w):null;
  const lit = agg.pop_for_w? (agg.lit_w/agg.pop_for_w):null;

  const sr=(k,v)=>`<div class="stat-row"><span>${k}</span><span class="v">${v}</span></div>`;
  let html='';

  html+=`<div class="stat-group"><h4>Selected districts (${agg.districts.length})</h4>`;
  if(agg.districts.length) html+=`<div class="chip-row">${agg.districts.map(d=>`<span class="chip">${d.name}</span>`).join('')}</div>`;
  else html+=`<div style="font-size:12px;color:var(--muted)">No Maharashtra districts selected. Population / income / Poshan cover Maharashtra only; the hospital totals below still count every hospital inside your drawn shape, nationwide.</div>`;
  html+=`</div>`;

  html+=`<div class="stat-group"><h4>Population</h4>`;
  html+=sr('Population (2027 est)', fmtNum(agg.pop_2027));
  html+=sr('Women 20–35', fmtNum(agg.women_2035));
  html+=sr('Households (est)', fmtNum(agg.households));
  if(lit!=null) html+=sr('Literacy (pop-weighted)', lit.toFixed(1)+'%');
  html+=`</div>`;

  if(agg.households>0){
    html+=`<div class="stat-group"><h4>Income clustering (households)</h4>`;
    html+='<div class="bracket-bar">';
    BRACKETS.forEach(([k,lab,c])=>{ const share=agg.households? (agg.brackets[k]/agg.households*100):0; html+=`<span class="bracket-seg" style="width:${share}%;background:${c}" title="${lab}"></span>`; });
    html+='</div>';
    BRACKETS.forEach(([k,lab])=>{ const share=agg.households? (agg.brackets[k]/agg.households*100):0; html+=sr(lab, fmtNum(agg.brackets[k])+`  (${share.toFixed(1)}%)`); });
    html+=`</div>`;
  }

  html+=`<div class="stat-group"><h4>Poshan / maternal</h4>`;
  html+=sr('Beneficiaries', fmtNum(agg.poshan_beneficiaries));
  html+=sr('Pregnant women', fmtNum(agg.poshan_pregnant));
  html+=sr('Lactating women', fmtNum(agg.poshan_lactating));
  if(stunting!=null) html+=sr('Stunting (pop-weighted)', stunting.toFixed(1)+'%');
  html+=`</div>`;

  if(hosp.total>0 || State.hospitals){
    html+=`<div class="stat-group"><h4>Hospitals inside (${fmtNum(hosp.total)})</h4>`;
    html+=sr('Government', fmtNum(hosp.Government));
    html+=sr('Private', fmtNum(hosp.Private));
    html+=sr('Maternity / Neonatal', fmtNum(hosp.Maternity));
    const IG=[['HIGH','High'],['UPPER_MID','Upper-mid'],['MID','Mid'],['LOW','Low']];
    if(IG.some(([k])=>hosp.income[k]>0)){
      html+=`<div style="margin-top:8px"><span class="k" style="font-size:11px">By income group</span>`;
      IG.forEach(([k,lab])=>{ if(hosp.income[k]>0) html+=sr(lab, fmtNum(hosp.income[k])); });
      html+=`</div>`;
    }
    const own=Object.entries(hosp.ownership||{}).sort((a,b)=>b[1]-a[1]);
    if(own.length){
      html+=`<div style="margin-top:8px"><span class="k" style="font-size:11px">By ownership</span>`;
      own.forEach(([k,n])=>html+=sr(k.charAt(0)+k.slice(1).toLowerCase().replace('_','-'), fmtNum(n)));
      html+=`</div>`;
    }
    const cats=Object.entries(hosp.categories||{}).sort((a,b)=>b[1]-a[1]);
    if(cats.length){
      html+=`<div style="margin-top:8px"><span class="k" style="font-size:11px">By Google category</span>`;
      cats.slice(0,12).forEach(([c,n])=>html+=sr(c, fmtNum(n)));
      if(cats.length>12) html+=`<div style="font-size:11px;color:var(--muted)">+${cats.length-12} more categories</div>`;
      html+=`</div>`;
    }
    html+=`</div>`;
  }

  if(acs.length){
    const byParty={};
    acs.forEach(a=>{ byParty[a.party||'Unknown']=(byParty[a.party||'Unknown']||0)+1; });
    html+=`<div class="stat-group"><h4>Assembly constituencies (${acs.length})</h4>`;
    Object.entries(byParty).sort((a,b)=>b[1]-a[1]).forEach(([party,n])=>html+=sr(party, n));
    html+=`<div style="font-size:11px;color:var(--muted);margin-top:6px">${acs.map(a=>a.ac_name).join(', ')}</div>`;
    html+=`</div>`;
  }

  html+=`<div class="stat-group"><small class="disclaimer">Any Maharashtra district the drawn region touches is counted in full for population, income &amp; Poshan (modeled household-bracket estimates; MH-only). Hospitals are counted nationwide — Maharashtra by whole district, elsewhere by point-in-polygon of your shape — and include real Google Maps listings plus the all-India research layer (income group &amp; ownership are modeled classifications). Political by constituency centroid (MH).</small></div>`;

  body.innerHTML=html;
  panel.classList.remove('hidden');
}

// ---------- Search ----------
let searchIndex=[];
function buildSearchIndex(){
  searchIndex=[];
  if(State.districts) turf.featureEach(State.districts,(f)=>{
    searchIndex.push({name:f.properties.name, type:'District', center:turf.centroid(f).geometry.coordinates, zoom:9});
  });
}
const searchInput=document.getElementById('search');
const searchResults=document.getElementById('search-results');
searchInput.addEventListener('input',()=>{
  const q=searchInput.value.trim().toLowerCase();
  if(!q){ searchResults.style.display='none'; return; }
  const hits=searchIndex.filter(s=>s.name && s.name.toLowerCase().includes(q)).slice(0,8);
  if(!hits.length){ searchResults.style.display='none'; return; }
  searchResults.innerHTML=hits.map((h,i)=>`<div data-i="${i}">${h.name} <small style="color:var(--muted)">${h.type}</small></div>`).join('');
  searchResults.style.display='block';
  Array.from(searchResults.children).forEach((el,i)=>el.onclick=()=>{
    map.flyTo({center:hits[i].center,zoom:hits[i].zoom,duration:900}); searchResults.style.display='none'; searchInput.value=hits[i].name;
  });
});
document.addEventListener('click',(e)=>{ if(!e.target.closest('#search-wrap')) searchResults.style.display='none'; });

// ---------- UI wiring ----------
document.querySelectorAll('input[name=metric]').forEach(r=>r.addEventListener('change',e=>setMetric(e.target.value)));
document.getElementById('toggle-hospitals').addEventListener('change',e=>setHospitalsVisible(e.target.checked));
document.querySelectorAll('.hfilter').forEach(c=>c.addEventListener('change',applyHospitalFilter));
['income-filter','ownership-filter'].forEach(id=>{const el=document.getElementById(id); if(el) el.addEventListener('change',applyHospitalFilter);});
document.getElementById('toggle-political').addEventListener('change',e=>setPoliticalVisible(e.target.checked));
document.getElementById('draw-polygon').addEventListener('click',()=>{ if(State.drawing) stopDrawing(true); else startDrawing(); });
document.getElementById('draw-clear').addEventListener('click',clearDraw);
document.getElementById('stats-close').addEventListener('click',()=>document.getElementById('stats-panel').classList.add('hidden'));
document.getElementById('panel-toggle').addEventListener('click',()=>document.getElementById('controls').classList.toggle('collapsed'));
if(window.matchMedia('(max-width: 860px)').matches) document.getElementById('controls').classList.add('collapsed');
document.addEventListener('keydown',e=>{ if(e.key==='Escape'){ if(State.drawing) stopDrawing(false);} });

window.NCMAP = map; // expose for debugging/preview (avoid #map element name clash)
window.NCdraw = (pts)=>{ State.drawPts=pts.slice(); stopDrawing(true); }; // test hook: draw a polygon from [lng,lat] pts

// Load the data immediately (independent of the basemap). Layers attach once the style
// is ready — coordinated by addLayersWhenReady(). Several triggers + a watchdog make this
// robust even if a style/tile fetch is slow or an event is missed.
init();
map.on('style.load', addLayersWhenReady);
map.on('load', addLayersWhenReady);
map.on('idle', addLayersWhenReady);
let _bootTries = 0, _styleRetried = false;
const _watchdog = setInterval(() => {
  if (_layersAdded) { clearInterval(_watchdog); return; }
  if (map.isStyleLoaded()) { addLayersWhenReady(); return; }
  _bootTries++;
  if (_bootTries === 6 && !_styleRetried) { _styleRetried = true; try { map.setStyle(BASEMAP_STYLE); } catch (e) {} }
  if (_bootTries > 40) clearInterval(_watchdog); // give up after ~40s
}, 1000);
