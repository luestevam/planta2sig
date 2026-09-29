const $=s=>document.querySelector(s);
const NS='http://www.w3.org/2000/svg';
const BASE_CATS={perimetro:['Perímetro do projeto','#e53935'],quadra:['Quadras','#3f7fd6'],lote:['Lotes / divisões','#0aafc4'],via:['Vias','#7d8a96'],construcao:['Construções','#a4693a'],ponto:['Pontos / vértices','#a23bc0'],limite:['Linhas de limite','#555555'],hidrografia:['Hidrografia','#1769ef']};
let CATS={...BASE_CATS};
const ICONS={crs:'◎',grade:'▦',tabela:'▤',localizacao:'⌖',legenda:'☰',carimbo:'▣'};
const S={config:{},project:null,page:1,selected:null,hidden:new Set(),hiddenTypes:new Set(),draw:null,evBox:null,preview:null,basemap:'grid',pick:null,draft:null,agentJob:null};
const objectType=f=>f.object_type||({Polygon:'polygon',LineString:'line',Point:'point'}[f.geometry_type||'Polygon']);
const visible=f=>!S.hidden.has(f.category)&&!S.hiddenTypes.has(objectType(f));
const fmt=(v,d=2)=>v==null||!isFinite(v)?'—':Number(v).toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d});
const esc=t=>String(t??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const el=(name,attrs={},parent)=>{const e=document.createElementNS(NS,name);for(const k in attrs)e.setAttribute(k,attrs[k]);parent?.appendChild(e);return e};
const feats=()=>S.project?.features||[];
const current=()=>feats().find(f=>f.id===S.selected);
const pageInfo=()=>S.project?.pages.find(p=>p.number===S.page);
const geoOf=()=>S.project?.georeferencing?.[String(S.page)];

/* ---------- API ---------- */
async function api(url,opt={}){
  let r;
  try{r=await fetch(url,opt)}catch{throw new Error('Não foi possível falar com o servidor local.')}
  if(!r.ok){
    let d=r.statusText;
    try{const j=await r.json();d=Array.isArray(j.detail)?j.detail.map(x=>x.msg).join('; '):j.detail||d}catch{}
    const e=new Error(d);e.status=r.status;throw e;
  }
  return r;
}
const post=(url,body)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
async function busy(text,fn){
  $('#busyText').textContent=text;$('#busy').hidden=false;
  try{return await fn()}finally{$('#busy').hidden=true}
}
function notice(msg,kind='',action){
  const n=$('#notice');n.className='notice '+kind;n.textContent=msg;
  if(action){const b=document.createElement('button');b.textContent=action.label;b.className='primary';b.style.marginLeft='10px';b.onclick=action.run;n.appendChild(b)}
}
async function guard(text,fn){
  try{return await busy(text,fn)}catch(e){
    notice(e.message,'error');
    if(e.status===409&&S.project)await reload();
  }
}

/* ---------- views (viewBox pan / zoom) ---------- */
class View{
  constructor(svg,onChange,onClick){
    this.svg=svg;this.box={x:0,y:0,w:100,h:100};this.onChange=onChange;this.onClick=onClick;
    let down=null,moved=false;
    svg.addEventListener('wheel',e=>{e.preventDefault();const p=this.pt(e);this.zoom(e.deltaY<0?1.25:.8,p.x,p.y)},{passive:false});
    svg.addEventListener('pointerdown',e=>{if(e.button!==0)return;down={sx:e.clientX,sy:e.clientY,box:{...this.box},id:e.pointerId};moved=false});
    svg.addEventListener('pointermove',e=>{
      if(!down)return;
      const dx=e.clientX-down.sx,dy=e.clientY-down.sy;
      if(!moved&&Math.hypot(dx,dy)<5)return;
      if(!moved){moved=true;svg.setPointerCapture(down.id)}
      const k=down.box.w/svg.getBoundingClientRect().width;
      this.box={...down.box,x:down.box.x-dx*k,y:down.box.y-dy*k};this.apply();
    });
    const up=e=>{if(!down)return;if(!moved&&e.type==='pointerup'&&this.onClick&&!e.target.closest?.('.feature-poly'))this.onClick(this.pt(e),e);down=null};
    svg.addEventListener('pointerup',up);svg.addEventListener('pointercancel',up);
    svg.addEventListener('pointermove',e=>this.onMove?.(this.pt(e)));
  }
  pt(e){const p=this.svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const m=this.svg.getScreenCTM();return m?p.matrixTransform(m.inverse()):{x:0,y:0}}
  apply(){const {x,y,w,h}=this.box;this.svg.setAttribute('viewBox',`${x} ${y} ${w} ${h}`);this.onChange?.()}
  fit(b,pad=.05){
    const r=this.svg.getBoundingClientRect(),cw=Math.max(r.width,10),ch=Math.max(r.height,10);
    let w=Math.max(b.w,1e-9)*(1+2*pad),h=Math.max(b.h,1e-9)*(1+2*pad);
    if(w/h>cw/ch)h=w*ch/cw;else w=h*cw/ch;
    this.box={x:b.x+b.w/2-w/2,y:b.y+b.h/2-h/2,w,h};this.apply();
  }
  zoom(f,cx,cy){
    const {x,y,w,h}=this.box;
    if(cx==null){cx=x+w/2;cy=y+h/2}
    this.box={x:cx-(cx-x)/f,y:cy-(cy-y)/f,w:w/f,h:h/f};this.apply();
  }
  scale(){return this.box.w/Math.max(this.svg.getBoundingClientRect().width,1)}
  reveal(b){
    const v=this.box;
    if(b.x>=v.x&&b.y>=v.y&&b.x+b.w<=v.x+v.w&&b.y+b.h<=v.y+v.h)return;
    this.box={...v,x:b.x+b.w/2-v.w/2,y:b.y+b.h/2-v.h/2};this.apply();
  }
}
const pageView=new View($('#pageSvg'),()=>{scaleMarks();scheduleHires()},(p)=>pageClick(p));
const mapView=new View($('#mapSvg'),()=>{drawBase();scaleMarks()});
pageView.onMove=p=>{
  const g=geoOf();let t=`x ${p.x.toFixed(0)}  y ${p.y.toFixed(0)}`;
  if(g?.verified){const m=g.matrix;t+=`  •  E ${fmt(p.x*m[0][0]+p.y*m[1][0]+m[2][0],1)}  N ${fmt(p.x*m[0][1]+p.y*m[1][1]+m[2][1],1)}`}
  $('#cursorCoords').textContent=t;
};
function scaleMarks(){
  for(const [svg,v] of [[$('#pageSvg'),pageView],[$('#mapSvg'),mapView]]){
    const k=v.scale();
    svg.querySelectorAll('.lbl').forEach(t=>t.setAttribute('font-size',12*k));
    svg.querySelectorAll('.vtx').forEach(c=>c.setAttribute('r',5*k));
    svg.querySelectorAll('.annot-label').forEach(t=>t.setAttribute('font-size',10*k));
  }
}

/* ---------- sharp crop of the PDF for the current zoom ---------- */
let hiTimer=null,hiSeq=0;
function scheduleHires(){clearTimeout(hiTimer);hiTimer=setTimeout(loadHires,250)}
function loadHires(){
  const p=S.project,i=pageInfo(),svg=$('#pageSvg');
  if(!p||!i||!p.name.toLowerCase().endsWith('.pdf'))return;
  const b=pageView.box,k=pageView.scale(),dpr=window.devicePixelRatio||1,seq=++hiSeq;
  if(k>=dpr){svg.querySelector('#hires')?.remove();return}   // stored page image is already sharp enough
  const x0=Math.max(0,b.x),y0=Math.max(0,b.y),x1=Math.min(i.width,b.x+b.w),y1=Math.min(i.height,b.y+b.h);
  if(x1-x0<1||y1-y0<1)return;
  const w=Math.min(2400,Math.round((x1-x0)/k*dpr));
  const url=`/api/projects/${p.id}/render/${S.page}.png?x0=${x0}&y0=${y0}&x1=${x1}&y1=${y1}&w=${w}`;
  const img=new Image();
  img.onload=()=>{
    if(seq!==hiSeq)return;
    svg.querySelector('#hires')?.remove();
    const base=svg.querySelector('image');if(!base)return;
    const e=el('image',{id:'hires',href:url,x:x0,y:y0,width:x1-x0,height:y1-y0,'pointer-events':'none'});
    svg.insertBefore(e,base.nextSibling);
  };
  img.src=url;
}

/* ---------- geometry helpers ---------- */
const bbox=pts=>{const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);const x=Math.min(...xs),y=Math.min(...ys);return {x,y,w:Math.max(...xs)-x,h:Math.max(...ys)-y}};
const R=6378137;
const merc=([lon,lat])=>[R*lon*Math.PI/180,-R*Math.log(Math.tan(Math.PI/4+lat*Math.PI/360))];
const unmerc=(x,y)=>[x/R*180/Math.PI,(2*Math.atan(Math.exp(-y/R))-Math.PI/2)*180/Math.PI];
const mapRing=f=>f.geometry?(f.geometry.type==='Point'?[f.geometry.coordinates]:f.geometry.type==='Polygon'?f.geometry.coordinates[0]:f.geometry.coordinates).map(merc):null;
const ptsAttr=r=>r.map(p=>p.join(',')).join(' ');
const centroid=r=>{const a=r.length>1&&r[0][0]===r.at(-1)[0]&&r[0][1]===r.at(-1)[1]?r.slice(0,-1):r;return [a.reduce((s,p)=>s+p[0],0)/a.length,a.reduce((s,p)=>s+p[1],0)/a.length]};
const statusInfo=f=>f.status==='accepted'?['Aceita','green']:f.status==='rejected'?['Rejeitada','amber']:f.revision>0?['Corrigida • a revisar','blue']:['Candidato','blue'];

/* ---------- load / render ---------- */
async function reload(){setProject(await (await api('/api/projects/'+S.project.id)).json())}
function setProject(p,{fit=false}={}){
  const first=!S.project||S.project.id!==p.id;
  S.project=p;
  CATS={...BASE_CATS};
  for(const c of p.classes||[])CATS[c.id]=[c.name,/^#[0-9a-f]{6}$/i.test(c.color)?c.color:'#617888'];
  for(const f of p.features)CATS[f.category]??=[f.category,'#617888'];
  if(first){S.page=1;S.selected=null;S.hidden.clear();S.evBox=null;S.draw=null;fit=true}
  if(S.selected&&!feats().some(f=>f.id===S.selected))S.selected=null;
  renderAll();
  if(fit)requestAnimationFrame(()=>{fitPage();fitMap()});
}
function fitPage(){const i=pageInfo();if(i)pageView.fit({x:0,y:0,w:i.width,h:i.height},.02)}
function fitMap(){
  const rs=feats().filter(f=>f.geometry&&f.page===S.page&&visible(f)).map(mapRing);
  if(rs.length)mapView.fit(bbox(rs.flat()),.12);
  else{mapView.box={x:-4.2e6,y:-1e6,w:2e5,h:1.2e5};mapView.apply()}
}
function renderAll(){renderHeader();renderLayers();renderEvidence();renderPage();renderMap();renderReview();renderFooter()}

function renderHeader(){
  const p=S.project;
  $('#projectName').textContent=p?p.name:'Da planta à informação geográfica';
  const fs=feats(),reviewed=fs.filter(f=>f.status!=='candidate').length;
  let st='Pronto para começar',step=1;
  if(p){
    if(p.stage==='diagnosed'){st='Diagnosticado';step=2}
    else if(reviewed<fs.length||!fs.length){st='Em revisão';step=3}
    else{st='Revisão concluída';step=4}
    if(fs.length&&fs.every(f=>f.status==='candidate'||f.status==='rejected')===false&&fs.some(f=>f.status==='accepted'))step=Math.max(step,3);
  }
  $('#projectState').textContent=st;
  ['Upload','Extract','Review','Export'].forEach((n,i)=>$('#step'+n).classList.toggle('active',i+1===step));
  $('#aiMode').textContent=S.config.ai_configured?'IA multimodal configurada • motor SIG + revisão':'Sem chave de IA • motor SIG + revisão manual';
  $('#sourceType').textContent=p?'• '+p.name.split('.').pop().toUpperCase():'';
}

function renderLayers(){
  const box=$('#layers');box.replaceChildren();
  const d=S.project?.diagnostic||{};
  const declared={quadra:d.declared_blocks,lote:d.declared_lots};
  let shown=0;
  for(const [cat,[name,color]] of Object.entries(CATS)){
    const fs=feats().filter(f=>f.category===cat),n=fs.length;shown+=n;
    const acc=fs.filter(f=>f.status==='accepted').length,rej=fs.filter(f=>f.status==='rejected').length;
    const scores=fs.map(f=>f.quality?.classification).filter(x=>x!=null);
    const conf=scores.length?scores.reduce((a,b)=>a+b,0)/scores.length:null;
    const [lab,cls]=!n?['—','subtle']:conf==null?['A revisar','subtle']:conf>=.85?['Alta','green']:conf>=.6?['Média','amber']:['Baixa','amber'];
    const row=document.createElement('label');row.className='layer';row.style.setProperty('--color',color);
    row.innerHTML=`<input type="checkbox" ${S.hidden.has(cat)?'':'checked'} ${n?'':'disabled'} aria-label="Exibir ${esc(name)}"><span class="colorbox"></span><span class="layerinfo"><strong>${esc(name)} (${n})</strong><small>${n?`${acc} aceitas • ${rej} rejeitadas${conf==null?'':` • classificação ${Math.round(conf*100)}%`}`:'Nenhuma feição extraída'}${declared[cat]?` • ${declared[cat]} declaradas no documento`:''}</small></span><span class="pill ${cls}">${lab}</span>`;
    const definition=S.project?.classes?.find(c=>c.id===cat);
    if(definition?.evidence?.length){const b=document.createElement('button');b.className='class-evidence';b.textContent='Ver legenda';b.onclick=e=>{e.preventDefault();const t=definition.evidence.find(x=>x.bbox);if(t)showEvidence({page:t.page||S.page,bbox:t.bbox})};row.querySelector('.layerinfo').appendChild(b)}
    row.querySelector('input').onchange=e=>{e.target.checked?S.hidden.delete(cat):S.hidden.add(cat);renderPage();renderMap();renderFooter()};
    box.appendChild(row);
  }
  const fs=feats();
  $('#counts').textContent=`${fs.length} ${fs.length===1?"feição":"feições"}`;
  $('#reviewCount').textContent=`${fs.filter(f=>f.status!=='candidate').length} revisadas`;
  if(!S.project)box.innerHTML='<p class="muted intro">Abra um exemplo ou envie uma planta.</p>';
}

function renderEvidence(){
  const list=$('#evidenceList');list.replaceChildren();
  const p=S.project;if(!p){list.innerHTML='<p class="muted">Origem, localização e confiança aparecerão aqui.</p>';return}
  const f=current();
  const items=[...(f?f.evidence.map(e=>({...e,head:'Evidência da feição selecionada'})):[]),...p.evidence];
  for(const e of items){
    const b=document.createElement('button');b.className='evidenceitem';
    b.innerHTML=`<span class="evidenceicon">${ICONS[e.category]||'✧'}</span><span><strong>${esc(e.head?e.head+': ':'')}${esc(e.value)}</strong><small>${esc(e.origin)} • pág. ${e.page} • confiança ${Math.round(e.confidence*100)}%<br>${esc(e.reason)}</small></span>`;
    b.onclick=()=>showEvidence(e);list.appendChild(b);
  }
  const iss=[...(f?f.issues:[]),...p.issues];
  if(iss.length){
    const d=document.createElement('div');d.className='evidenceitem';
    d.innerHTML=`<span class="evidenceicon" style="color:#c25a33;background:#fdeee8">⚠</span><span><strong style="color:#b04a26">Inconsistências e pendências (${iss.length})</strong><small>${iss.map(esc).join('<br>')}</small></span>`;
    list.appendChild(d);
  }
  if(!items.length&&!iss.length)list.innerHTML='<p class="muted">Nenhuma evidência extraída ainda.</p>';
}
function showEvidence(e){
  if(e.page&&e.page!==S.page){S.page=e.page;renderPage();fitPage()}
  S.evBox=e.bbox;renderPage();
  const [x0,y0,x1,y1]=e.bbox;pageView.reveal({x:x0,y:y0,w:x1-x0,h:y1-y0});
}

function renderPage(){
  const svg=$('#pageSvg');svg.replaceChildren();
  const p=S.project,i=pageInfo();
  $('#pageEmpty').hidden=!!p;$('#pageTag').hidden=!p;
  $('#pageCounter').textContent=p?`${S.page} / ${p.pages.length}`:'— / —';
  $('#prevPage').disabled=!p||S.page<=1;$('#nextPage').disabled=!p||S.page>=p.pages.length;
  ['drawBtn'].forEach(id=>$('#'+id).disabled=!p);
  $('#drawBtn').hidden=!!S.draw;$('#finishDraw').hidden=!S.draw;$('#cancelDraw').hidden=!S.draw;
  if(!p||!i)return;
  const g=geoOf();
  $('#pageUnits').textContent=g?.verified?`Página → ${g.crs}`:'Coordenadas da página';
  el('image',{href:`/api/projects/${p.id}/pages/${S.page}.png`,x:0,y:0,width:i.width,height:i.height},svg);
  const gr=el('g',{},svg);
  for(const f of feats().filter(f=>f.page===S.page&&visible(f)))
    gr.appendChild(poly(f,f.page_ring));
  if(S.evBox){const [x0,y0,x1,y1]=S.evBox;el('rect',{class:'evidence-box',x:x0,y:y0,width:x1-x0,height:y1-y0},svg)}
  const f=current();
  if(f&&f.page===S.page&&visible(f)){
    const c=centroid(f.page_ring);
    const t=el('text',{class:'lbl',x:c[0],y:c[1],'text-anchor':'middle','font-weight':'700',fill:'#5c3d00',stroke:'#fff','stroke-width':'.35em','paint-order':'stroke','pointer-events':'none'},svg);
    t.textContent=f.label.split(' • ')[0];
  }
  scheduleHires();
  const line=S.draw||S.preview;
  if(line?.length){
    el(S.draw?'polyline':'polygon',{class:'drawline',points:ptsAttr(line)},svg);
    line.forEach(pt=>el('circle',{class:'vertex vtx',cx:pt[0],cy:pt[1],r:5},svg));
  }
  scaleMarks();
}
function poly(f,ring){
  const kind=f.geometry_type||f.geometry?.type||'Polygon';
  const annotation=objectType(f)==='annotation';
  const attrs=kind==='Point'?{cx:ring[0][0],cy:ring[0][1],r:5}: {points:ptsAttr(ring)};
  const holes=kind==='Polygon'?(ring===f.page_ring?(f.page_holes||[]):(f.geometry?.coordinates.slice(1)||[]).map(r=>r.map(merc))):[];
  const path=holes.length?[ring,...holes].map(r=>'M'+r.map(p=>p.join(',')).join('L')+'Z').join(' '):null;
  const e=el(annotation?'g':path?'path':kind==='Point'?'circle':kind==='LineString'?'polyline':'polygon',{...(path?{d:path,'fill-rule':'evenodd'}:attrs),class:`feature-poly cat-${f.category} ${kind==='Point'&&!annotation?'vtx':''} ${f.status==='candidate'?'':f.status} ${f.id===S.selected?'selected':''}`.replace(/\s+/g,' '),tabindex:0,'data-id':f.id,role:'button','aria-label':f.label});
  const color=f.id===S.selected?'#ef9900':f.status==='accepted'?'#16a387':f.status==='rejected'?'#aaa':CATS[f.category]?.[1]||'#555';
  if(kind==='LineString'){e.style.fill='none';e.style.stroke=color}
  if(kind==='Point'){e.style.fill=color;e.style.stroke='#fff'}
  if(kind==='Polygon'&&!BASE_CATS[f.category]){e.style.stroke=color;e.style.fill=color+'22'}
  if(annotation){
    e.style.stroke='none';e.style.fill=color;
    let angle=f.attributes?.orientation||0;
    if(ring!==f.page_ring){
      const g=S.project.georeferencing?.[f.page];
      if(g){const a=angle*Math.PI/180,m=g.matrix;const dx=Math.cos(a)*m[0][0]+Math.sin(a)*m[1][0],dy=Math.cos(a)*m[0][1]+Math.sin(a)*m[1][1];angle=Math.atan2(-dy,dx)*180/Math.PI}
    }
    const text=el('text',{x:ring[0][0],y:ring[0][1],class:'annot-label',transform:`rotate(${angle} ${ring[0][0]} ${ring[0][1]})`},e);
    text.textContent=f.attributes?.text||f.label;
    if(f.id===S.selected&&ring===f.page_ring&&f.attributes?.quad)
      el('polygon',{points:ptsAttr(f.attributes.quad),fill:'none',stroke:'#ef9900','stroke-width':1,'vector-effect':'non-scaling-stroke'},e);
  }
  e.addEventListener('click',()=>{if(!S.draw&&!S.pick)select(f.id)});
  e.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();select(f.id)}});
  const t=el('title',{},e);t.textContent=`${f.label} • ${statusInfo(f)[0]}`;
  return e;
}

function renderMap(){
  const svg=$('#mapSvg');
  let base=svg.querySelector('#baseG');
  svg.replaceChildren();base=el('g',{id:'baseG'},svg);
  const fs=feats().filter(f=>f.geometry&&f.page===S.page&&f.status!=='rejected'&&visible(f));
  const all=feats().filter(f=>f.geometry);
  $('#mapEmpty').hidden=all.length>0;
  const g=geoOf();
  $('#mapStatus').textContent=g?.verified?`● Georreferenciado • RMSE ${fmt(g.rmse,3)} m`:S.project?'○ Sem posição comprovada • coordenadas da página':'○ Aguardando documento';
  $('#mapStatus').style.color=g?.verified?'':'#b7791f';
  $('#attribution').textContent=S.basemap==='osm'?'© colaboradores do OpenStreetMap':'Grade Web Mercator • sem imagem de fundo';
  const gr=el('g',{},svg);
  for(const f of fs)gr.appendChild(poly(f,mapRing(f)));
  const f=current();
  if(f?.geometry&&fs.includes(f)){
    const c=centroid(mapRing(f));
    const t=el('text',{class:'lbl',x:c[0],y:c[1],'text-anchor':'middle','font-weight':'700',fill:'#5c3d00',stroke:'#fff','stroke-width':'.35em','paint-order':'stroke','pointer-events':'none'},svg);
    t.textContent=f.label.split(' • ')[0];
  }
  drawBase();scaleMarks();renderDetails();
}
const nice=x=>{const e=10**Math.floor(Math.log10(x)),m=x/e;return (m<1.5?1:m<3.5?2:m<7.5?5:10)*e};
function drawBase(){
  const b=$('#baseG');if(!b)return;b.replaceChildren();
  const {x,y,w,h}=mapView.box,k=mapView.scale();
  if(S.basemap==='osm'){
    const cw=$('#mapSvg').getBoundingClientRect().width,C=2*Math.PI*R;
    const z=Math.max(1,Math.min(19,Math.round(Math.log2(cw/256*C/w))));
    const size=C/2**z,n=2**z;
    const tx0=Math.max(0,Math.floor((x+C/2)/size)),tx1=Math.min(n-1,Math.floor((x+w+C/2)/size));
    const ty0=Math.max(0,Math.floor((y+C/2)/size)),ty1=Math.min(n-1,Math.floor((y+h+C/2)/size));
    if((tx1-tx0+1)*(ty1-ty0+1)<=64)
      for(let tx=tx0;tx<=tx1;tx++)for(let ty=ty0;ty<=ty1;ty++)
        el('image',{href:`https://tile.openstreetmap.org/${z}/${tx}/${ty}.png`,x:tx*size-C/2,y:ty*size-C/2,width:size,height:size,opacity:.9},b);
    return;
  }
  const [lon0,lat1]=unmerc(x,y),[lon1,lat0]=unmerc(x+w,y+h);
  const step=nice(Math.max(lon1-lon0,1e-9)/5);
  for(let lon=Math.ceil(lon0/step)*step;lon<=lon1;lon+=step){
    const px=merc([lon,0])[0];
    el('line',{x1:px,x2:px,y1:y,y2:y+h,stroke:'#b8c9c0','stroke-width':1,'vector-effect':'non-scaling-stroke'},b);
    const t=el('text',{x:px+4*k,y:y+12*k,'font-size':10*k,fill:'#6b8579'},b);t.textContent=lon.toFixed(step<.01?5:step<1?3:1)+'°';
  }
  const lstep=nice(Math.max(lat1-lat0,1e-9)/4);
  for(let lat=Math.ceil(lat0/lstep)*lstep;lat<=lat1;lat+=lstep){
    const py=merc([0,lat])[1];
    el('line',{x1:x,x2:x+w,y1:py,y2:py,stroke:'#b8c9c0','stroke-width':1,'vector-effect':'non-scaling-stroke'},b);
    const t=el('text',{x:x+4*k,y:py-3*k,'font-size':10*k,fill:'#6b8579'},b);t.textContent=lat.toFixed(lstep<.01?5:lstep<1?3:1)+'°';
  }
}

function renderDetails(){
  const d=$('#featureDetails'),f=current();
  d.hidden=!f;if(!f)return;
  const g=S.project.georeferencing?.[String(f.page)];
  let cent='—';
  if(f.world_ring){const c=centroid(f.world_ring);cent=`${fmt(c[0],2)} E<br>${fmt(c[1],2)} N`}
  d.innerHTML=`<strong>${esc(f.label)}</strong>
    <div class="detailrow">Área calculada<b>${f.area!=null?fmt(f.area)+' m²':'—'}</b></div>
    <div class="detailrow">Origem<b>página ${f.page} (${esc(S.project.name.split('.').pop().toUpperCase())})</b></div>
    <div class="detailrow">Erro do ajuste<b>${g?.verified?`RMSE ${fmt(g.rmse,3)} m`:'pendente'}</b></div>
    <div class="detailrow">Acurácia externa<b>não medida</b></div>
    <div class="detailrow">Qualidade da leitura<b>${f.quality?.reading==null?'não medida':fmt(f.quality.reading*100,0)+'%'}</b></div>
    <div class="detailrow">Classificação<b>${f.quality?.classification==null?'a revisar':fmt(f.quality.classification*100,0)+'%'}</b></div>
    <div class="detailrow">Centroide${f.source_crs?` (${esc(f.source_crs)})`:''}<b>${cent}</b></div>
    ${f.geometry?'':'<p>Sem posição comprovada: a feição permanece em coordenadas da página.</p>'}`;
}

function renderReview(){
  const f=current();
  ['acceptBtn','correctBtn','rejectBtn'].forEach(id=>$('#'+id).disabled=!f);
  if(!f){
    $('#selectedName').textContent='Selecione uma feição';
    const s=$('#selectedStatus');s.textContent='Revisão sincronizada';s.className='pill blue';
    $('#selectedHint').textContent='Clique no contorno na planta ou no mapa.';
    ['Area','Perimeter','Vertices'].forEach(k=>$('#selected'+k).textContent='—');
  }else{
    $('#selectedName').textContent=f.label;
    const [t,c]=statusInfo(f),s=$('#selectedStatus');s.textContent=t;s.className='pill '+c;
    $('#selectedHint').textContent=f.issues.length?'Pendências: '+f.issues.join(' • '):'Verifique a geometria, a área e a correspondência com a planta original.';
    $('#selectedArea').textContent=f.area!=null?fmt(f.area)+' m²':f.geometry?'não se aplica':'sem georreferenciamento';
    $('#selectedPerimeter').textContent=f.perimeter!=null?fmt(f.perimeter)+' m':'—';
    $('#selectedVertices').textContent=f.page_ring.length-((f.geometry_type||'Polygon')==='Polygon'?1:0);
  }
  $('#historyCount').textContent=S.project?.history.length||0;
}
function renderFooter(){
  const g=geoOf();
  $('#geoMethod').textContent=g?.verified?`${g.method} • ${g.control_count} controles • ${g.origin}`:'Sem transformação definida';
  $('#geoError').textContent=g?.verified?`RMSE ${fmt(g.rmse,3)} m`:'RMSE —';
  const has=feats().some(f=>f.status==='accepted'&&f.geometry);
  document.querySelectorAll('[data-export]').forEach(b=>b.disabled=!has);
  $('#georefBtn').disabled=$('#tableBtn').disabled=!S.project;
}

/* ---------- selection ---------- */
function select(id,{reveal=true}={}){
  S.selected=id;S.evBox=null;
  const f=current();
  if(f&&f.page!==S.page){S.page=f.page;renderPage();fitPage()}
  if(f&&reveal){
    pageView.reveal(bbox(f.page_ring));
    if(f.geometry)mapView.reveal(bbox(mapRing(f)));
    const ev=f.evidence[0];
    if(ev?.bbox?.length===4){const [x0,y0,x1,y1]=ev.bbox;S.evBox=ev.bbox}
  }
  renderEvidence();renderPage();renderMap();renderReview();
}

/* ---------- modal ---------- */
function modal(title,html,mount){
  $('#modalTitle').textContent=title;$('#modalBody').innerHTML=html;
  const d=$('#modal');if(!d.open)d.showModal();mount?.($('#modalBody'));
}
$('#closeModal').onclick=()=>{$('#modal').close()};
$('#modal').addEventListener('close',()=>{S.preview=null;if(!S.pick)renderPage()});

/* ---------- review actions ---------- */
const reviewUrl=f=>`/api/projects/${S.project.id}/features/${f.id}/review`;
$('#rejectBtn').onclick=()=>{
  const f=current();if(!f)return;
  modal('Rejeitar feição',`<p class="modaltext">Rejeitar <strong>${esc(f.label)}</strong> a remove da exportação. O registro permanece no histórico.</p><label class="full">Motivo<br><input id="rjNote" style="width:100%" maxlength="500"></label><div class="modalactions"><button id="rjNo">Cancelar</button><button class="reject" id="rjYes">× Rejeitar</button></div>`,b=>{
    b.querySelector('#rjNo').onclick=()=>$('#modal').close();
    b.querySelector('#rjYes').onclick=async()=>{const note=b.querySelector('#rjNote').value;$('#modal').close();
      const p=await guard('Registrando revisão…',()=>post(reviewUrl(f),{action:'reject',revision:f.revision,note}));
      if(p){setProject(p);notice('Feição rejeitada e registrada no histórico.')}};
  });
};
$('#acceptBtn').onclick=async()=>{
  const f=current();if(!f)return;
  const run=async ack=>{
    const p=await guard('Registrando revisão…',()=>post(reviewUrl(f),{action:'accept',revision:f.revision,acknowledge:ack}));
    if(p){setProject(p);notice(f.geometry?'Feição aceita: entra na exportação.':'Feição aceita, mas sem georreferenciamento comprovado: não será exportada.')}
  };
  if(!f.issues.length)return run(false);
  modal('Confirmar pendências',`<div class="alert"><strong>Pendências desta feição</strong><ul class="compactlist">${f.issues.map(i=>`<li>${esc(i)}</li>`).join('')}</ul></div><label class="inlinecheck"><input type="checkbox" id="ack"><span>Conferi a geometria sobre a planta e assumo as pendências acima.</span></label><div class="modalactions"><button id="acNo">Cancelar</button><button class="accept" id="acYes" disabled>✓ Aceitar</button></div>`,b=>{
    const y=b.querySelector('#acYes');b.querySelector('#ack').onchange=e=>y.disabled=!e.target.checked;
    b.querySelector('#acNo').onclick=()=>$('#modal').close();
    y.onclick=()=>{$('#modal').close();run(true)};
  });
};
$('#correctBtn').onclick=()=>{
  const f=current();if(!f)return;
  const ring=(f.geometry_type||'Polygon')==='Polygon'?f.page_ring.slice(0,-1):f.page_ring;
  const catOpts=Object.entries(CATS).map(([k,[n]])=>`<option value="${esc(k)}" ${k===f.category?'selected':''}>${esc(n)}</option>`).join('');
  const annotation=objectType(f)==='annotation',a=f.attributes||{};
  const annotationFields=annotation?`<label class="full">Texto original: ${esc(a.original_text)}<textarea id="cText">${esc(a.text)}</textarea></label><label>Orientação (graus)<input id="cAngle" type="number" step="any" value="${a.orientation||0}"></label><label>Caixa na página (x0,y0,x1,y1)<input id="cBox" value="${esc(a.bbox?.join(','))}"></label><label class="full">Vínculo com feição<select id="cLink"><option value="">Sem vínculo</option>${feats().filter(x=>x.page===f.page&&x.id!==f.id&&objectType(x)!=='annotation').map(x=>`<option value="${esc(x.id)}" ${f.linked_feature_id===x.id?'selected':''}>${esc(x.label)} (${esc(x.id)})</option>`).join('')}</select></label>`:'';
  modal('Corrigir feição',`<div class="formgrid"><label>Rótulo<input id="cLabel" value="${esc(f.label)}" maxlength="180"></label><label>Categoria<select id="cCat">${catOpts}</select></label>
    <label class="full">Motivo da correção (obrigatório, vai para o histórico)<input id="cNote" maxlength="2000"></label>
    <label class="full">Nova classe (opcional)<input id="cClassName" maxlength="100" placeholder="Ex.: Rede de esgoto, poste, vegetação"></label>${annotationFields}
    <div class="full"><strong>Vértices na planta (coordenadas da página)</strong><table class="data-table"><tr><th>#</th><th>x</th><th>y</th></tr>${ring.map((p,i)=>`<tr><td>${i+1}</td><td><input type="number" step="any" data-i="${i}" data-k="0" value="${p[0].toFixed(2)}"></td><td><input type="number" step="any" data-i="${i}" data-k="1" value="${p[1].toFixed(2)}"></td></tr>`).join('')}</table></div></div>
    <div class="modalactions"><button id="cNo">Cancelar</button><button class="primary" id="cYes">Salvar correção</button></div>`,b=>{
    const cur=()=>ring.map((_,i)=>[+b.querySelector(`[data-i="${i}"][data-k="0"]`).value,+b.querySelector(`[data-i="${i}"][data-k="1"]`).value]);
    b.querySelectorAll('.data-table input').forEach(inp=>inp.oninput=()=>{S.preview=cur();renderPage()});
    b.querySelector('#cNo').onclick=()=>$('#modal').close();
    b.querySelector('#cYes').onclick=async()=>{
      const pts=cur(),changed=JSON.stringify(pts)!==JSON.stringify(ring);
      const body={action:'correct',revision:f.revision,label:b.querySelector('#cLabel').value,category:b.querySelector('#cCat').value,note:b.querySelector('#cNote').value};
      const newClass=b.querySelector('#cClassName').value.trim();if(newClass)body.class_name=newClass;
      if(annotation){body.text=b.querySelector('#cText').value;body.orientation=+b.querySelector('#cAngle').value;body.linked_feature_id=b.querySelector('#cLink').value||null;
        const box=b.querySelector('#cBox').value.split(',').map(Number);if(JSON.stringify(box)!==JSON.stringify(a.bbox))body.text_bbox=box;}
      if(changed)body.page_ring=pts;
      $('#modal').close();
      const p=await guard('Aplicando correção…',()=>post(reviewUrl(f),body));
      if(p){setProject(p);select(f.id);notice('Correção aplicada na planta e no mapa, e registrada no histórico.')}
    };
  });
};

/* ---------- draw (assisted vectorization) ---------- */
function pageClick(pt){
  if(S.pick){pickPoint(pt);return}
  if(S.draw){S.draw.push([+pt.x.toFixed(2),+pt.y.toFixed(2)]);renderPage()}
}
$('#drawBtn').onclick=()=>{S.draw=[];notice('Clique nos vértices da feição na planta e depois em Concluir.');renderPage()};
$('#cancelDraw').onclick=()=>{S.draw=null;renderPage();notice('Vetorização cancelada.')};
$('#finishDraw').onclick=()=>{
  if(!S.draw||!S.draw.length){notice('Marque ao menos um ponto.','error');return}
  const catOpts=Object.entries(CATS).map(([k,[n]])=>`<option value="${esc(k)}">${esc(n)}</option>`).join('');
  modal('Nova feição vetorizada',`<div class="formgrid"><label>Rótulo<input id="nLabel" maxlength="180"></label><label>Categoria<select id="nCat">${catOpts}</select></label><label>Tipo<select id="nKind">${S.draw.length>=3?'<option value="Polygon">Polígono</option>':''}${S.draw.length>=2?'<option value="LineString">Linha</option>':''}${S.draw.length===1?'<option value="Point">Ponto</option>':''}</select></label><label class="full">Evidência / motivo<input id="nNote" maxlength="2000"></label></div><div class="modalactions"><button id="nNo">Voltar</button><button class="primary" id="nYes">Criar feição</button></div>`,b=>{
    b.querySelector('#nNo').onclick=()=>$('#modal').close();
    b.querySelector('#nYes').onclick=async()=>{
      const body={page:S.page,page_ring:S.draw,geometry_type:b.querySelector('#nKind').value,label:b.querySelector('#nLabel').value,category:b.querySelector('#nCat').value,note:b.querySelector('#nNote').value};
      $('#modal').close();
      const p=await guard('Criando feição…',()=>post(`/api/projects/${S.project.id}/features`,body));
      if(p){S.draw=null;setProject(p);select(p.features[p.features.length-1].id);notice('Feição criada como candidata; revise antes de aceitar.')}
    };
  });
};

/* ---------- georeferencing ---------- */
function openGeoref(){
  const g=geoOf();
  S.draft??={crs:g?.crs||'EPSG:31984',ev:g?.origin||'',rmse:2,controls:(g?.controls||[]).map(c=>({px:c.page[0],py:c.page[1],e:c.world[0],n:c.world[1],ev:c.evidence||''}))};
  while(S.draft.controls.length<4)S.draft.controls.push({px:'',py:'',e:'',n:'',ev:''});
  const d=S.draft;
  modal('Georreferenciamento assistido',`<p class="modaltext">Informe ao menos 4 pontos de controle lidos no documento (interseções da grade, vértices de tabela). Use <strong>Marcar</strong> para clicar a posição na planta. O sistema calcula a transformação afim, mostra resíduos e recusa RMSE acima do limite. ${g?.verified?`<br><strong>Atual:</strong> ${esc(g.method)}, RMSE ${fmt(g.rmse,3)} m.`:''}</p>
    <div class="formgrid"><label>CRS projetado em metros (ex.: EPSG:31984)<input id="gCrs" value="${esc(d.crs)}"></label><label>RMSE máximo (m)<input id="gMax" type="number" step="any" value="${d.rmse}"></label><label class="full">Origem do CRS no documento<input id="gEv" value="${esc(d.ev)}" placeholder="ex.: legenda 'SIRGAS 2000 / UTM 24S'"></label></div>
    <table class="data-table"><tr><th>#</th><th>x pág.</th><th>y pág.</th><th>E (m)</th><th>N (m)</th><th>Evidência</th><th></th><th>Resíduo</th></tr>${d.controls.map((c,i)=>`<tr><td>${i+1}</td><td><input data-c="${i}" data-f="px" value="${c.px}"></td><td><input data-c="${i}" data-f="py" value="${c.py}"></td><td><input data-c="${i}" data-f="e" value="${c.e}"></td><td><input data-c="${i}" data-f="n" value="${c.n}"></td><td><input data-c="${i}" data-f="ev" value="${esc(c.ev)}"></td><td><button data-pick="${i}">Marcar</button></td><td>${g?.residuals?.[i]!=null?fmt(g.residuals[i],3)+' m':'—'}</td></tr>`).join('')}</table>
    <div class="modalactions"><button id="gAdd">＋ Ponto</button><button id="gNo">Cancelar</button><button class="primary" id="gYes">Calcular e aplicar</button></div>`,b=>{
    const sync=()=>{d.crs=b.querySelector('#gCrs').value;d.rmse=+b.querySelector('#gMax').value||2;d.ev=b.querySelector('#gEv').value;
      b.querySelectorAll('[data-c]').forEach(i=>d.controls[i.dataset.c][i.dataset.f]=i.value)};
    b.querySelector('#gAdd').onclick=()=>{sync();d.controls.push({px:'',py:'',e:'',n:'',ev:''});openGeoref()};
    b.querySelector('#gNo').onclick=()=>{S.draft=null;$('#modal').close()};
    b.querySelectorAll('[data-pick]').forEach(btn=>btn.onclick=()=>{sync();S.pick=+btn.dataset.pick;$('#modal').close();notice(`Clique na planta para marcar o ponto de controle ${S.pick+1}.`)});
    b.querySelector('#gYes').onclick=async()=>{
      sync();
      const controls=d.controls.filter(c=>c.px!==''||c.e!=='').map(c=>({page:[+c.px,+c.py],world:[+c.e,+c.n],evidence:c.ev||'informado pelo revisor'}));
      $('#modal').close();
      const p=await guard('Calculando transformação…',()=>post(`/api/projects/${S.project.id}/georeference`,{page:S.page,crs:d.crs,controls,max_rmse:d.rmse,crs_evidence:d.ev||'informado pelo revisor'}));
      if(p){S.draft=null;setProject(p,{fit:false});fitMap();notice(`Georreferenciamento aplicado: RMSE ${fmt(p.georeferencing[S.page].rmse,3)} m. Feições reprojetadas voltaram para revisão.`)}
    };
  });
}
function pickPoint(pt){
  const c=S.draft.controls[S.pick];c.px=pt.x.toFixed(1);c.py=pt.y.toFixed(1);S.pick=null;notice('Ponto marcado.');openGeoref();
}
$('#georefBtn').onclick=()=>{S.draft=null;openGeoref()};

/* ---------- vertex table import ---------- */
$('#tableBtn').onclick=()=>{
  const g=geoOf();
  modal('Reconstruir perímetro a partir de tabela de vértices',`<p class="modaltext">Digite os vértices exatamente como aparecem no documento, um por linha (<code>nome; E/lon; N/lat</code> ou apenas <code>E N</code>). O perímetro só é criado depois de conferido sobre a planta com georreferenciamento já comprovado.${g?.verified?'':'<br><strong>Georreferencie a página antes.</strong>'}</p>
    <div class="formgrid"><label>Rótulo<input id="tLabel" value="Perímetro (tabela)"></label><label>CRS da tabela<input id="tCrs" placeholder="EPSG:31983"></label><label>Área declarada (m²)<input id="tArea" type="number" step="any"></label><label>Caixa da tabela (x0,y0,x1,y1)<input id="tBox" placeholder="40,640,150,680"></label><label class="full">Vértices<textarea id="tVert" rows="7"></textarea></label><label class="full">Evidência (onde está a tabela)<input id="tEv"></label></div>
    <div class="modalactions"><button id="tNo">Cancelar</button><button class="primary" id="tYes">Reconstruir</button></div>`,b=>{
    b.querySelector('#tNo').onclick=()=>$('#modal').close();
    b.querySelector('#tYes').onclick=async()=>{
      const vertices=b.querySelector('#tVert').value.split('\n').map(l=>l.trim()).filter(Boolean).map(l=>{const n=l.split(/[;\s]+/).map(x=>+x.replace(',','.')).filter(x=>!isNaN(x));return n.slice(-2)});
      const box=b.querySelector('#tBox').value.split(/[,\s]+/).map(Number);
      const a=b.querySelector('#tArea').value;
      $('#modal').close();
      const p=await guard('Reconstruindo polígono…',()=>post(`/api/projects/${S.project.id}/table`,{page:S.page,label:b.querySelector('#tLabel').value,crs:b.querySelector('#tCrs').value,bbox:box,vertices,declared_area:a?+a:null,evidence:b.querySelector('#tEv').value}));
      if(p){setProject(p);select(p.features[p.features.length-1].id);notice('Perímetro reconstruído a partir da tabela. Confira a sobreposição antes de aceitar.')}
    };
  });
};

/* ---------- diagnostic / history ---------- */
$('#diagnosticBtn').onclick=()=>{
  const p=S.project;if(!p){notice('Abra um documento primeiro.');return}
  const rej={};(p.rejected_candidates||[]).forEach(r=>rej[r.reason]=(rej[r.reason]||0)+1);
  const g=Object.entries(p.georeferencing||{}).map(([pg,x])=>`<tr><td>${pg}</td><td>${esc(x.crs)}</td><td>${esc(x.method)}</td><td>${fmt(x.rmse,3)} m</td><td>${x.control_count}</td><td>${esc(x.origin)}</td></tr>`).join('');
  modal('Diagnóstico e fontes',`<div class="modaltext"><p><strong>Arquivo:</strong> ${esc(p.name)}<br><strong>SHA-256:</strong> ${esc(p.sha256)}</p>
    <table class="data-table"><tr><th>Página</th><th>Dimensões</th><th>Textos</th><th>Caminhos</th><th>Imagens</th><th>Fontes</th></tr>${p.pages.map(x=>`<tr><td>${x.number}</td><td>${fmt(x.width,0)}×${fmt(x.height,0)}</td><td>${x.text_count}</td><td>${x.path_count}</td><td>${x.image_count}</td><td>${p.name.toLowerCase().endsWith('.pdf')&&p.stage==='extracted'?`<a href="/api/projects/${p.id}/source/text/${x.number}" target="_blank">texto</a> • <a href="/api/projects/${p.id}/source/vectors/${x.number}" target="_blank">vetores</a>`:'—'}</td></tr>`).join('')}</table>
    <p><strong>Georreferenciamento</strong></p>${g?`<table class="data-table"><tr><th>Pág.</th><th>CRS</th><th>Método</th><th>RMSE</th><th>Controles</th><th>Origem</th></tr>${g}</table>`:'<div class="alert">Nenhuma página com georreferenciamento comprovado.</div>'}
    <p><strong>Resumo da extração</strong></p><div class="json-view">${esc(JSON.stringify(p.diagnostic,null,1))}</div>
    <p><strong>Candidatos rejeitados pelo motor</strong></p>${Object.keys(rej).length?`<ul class="compactlist">${Object.entries(rej).map(([k,v])=>`<li>${esc(k)}: ${v}</li>`).join('')}</ul>`:'<p>Nenhum.</p>'}</div>`);
};
$('#historyBtn').onclick=()=>{
  const h=S.project?.history||[];
  modal('Histórico de alterações',h.length?[...h].reverse().map(x=>`<div class="histrow"><small>${new Date(x.time).toLocaleString('pt-BR')} • ${esc(x.action)}</small><p>${esc(x.note||'(sem observação)')}${x.after?.label?` — <strong>${esc(x.after.label)}</strong>`:''}</p></div>`).join(''):'<p class="modaltext">Nenhuma alteração registrada.</p>');
};
$('#help').onclick=()=>modal('Sobre o módulo',`<div class="modaltext"><p>O motor SIG determinístico extrai texto e caminhos do PDF, transforma coordenadas e mede resíduos. A IA multimodal (opcional, chave só no backend) apenas <strong>sugere observações</strong>: nenhuma coordenada da IA vira geometria sem comprovação no documento e validação geométrica.</p><p>Feições sem georreferenciamento comprovado ficam em coordenadas da página e não são exportadas.</p></div>`);

/* ---------- bounded extraction agent ---------- */
const TOOL_LABELS={diagnostics:'Diagnóstico',embedded:'GeoPDF',native_text:'Texto nativo',ocr:'OCR local',regions:'Regiões',vectors:'Vetores e símbolos',raster:'Linhas e regiões raster',legend:'Legenda',spatial:'Referência espacial',georeference:'Georreferenciamento',reconstruct:'Motor SIG',annotations:'Anotações',roads:'Vias',classify:'Classes',associate:'Vínculos de textos',validate:'Validação'};
$('#typeFilters').querySelectorAll('input').forEach(input=>input.onchange=()=>{input.checked?S.hiddenTypes.delete(input.value):S.hiddenTypes.add(input.value);renderPage();renderMap();renderFooter()});
$('#aiBtn').onclick=()=>{
  if(!S.project){notice('Abra um documento primeiro.');return}
  if(S.agentJob){notice('Uma análise já está em andamento.');return}
  const config=S.config.agent||{},regions=S.project.regions?.[S.page]||[];
  modal('Analisar com agente',`<p class="modaltext">${esc(config.disclosure||'Modo local: nenhum conteúdo é enviado a um serviço externo.')}</p>
    <div class="formgrid"><label>Modo<select id="agentMode"><option value="local">Local — sem envio externo</option><option value="ai" ${S.config.ai_configured?'':'disabled'}>IA + ferramentas locais</option></select></label><label>Páginas<select id="agentPages"><option value="current">Página atual (${S.page})</option><option value="all">Todas</option></select></label></div>
    <p class="modaltext">Provedor: ${esc(config.provider)} · modelo: ${esc(config.model||'não configurado')}. Limites: ${config.limits?.max_tools||48} ferramentas, ${config.limits?.max_seconds||240} s, US$ ${config.limits?.max_cost_usd??.5} estimados. ${config.cost_ready?'':'Configure as tarifas no backend para habilitar chamadas pagas.'}</p>
    <details><summary>Regiões da página atual (opcional, editável)</summary><p class="modaltext">Confirme as caixas em coordenadas da página. Papéis: main_map, legend, stamp, table, location_map. Se vazio, o agente propõe a separação.</p><textarea id="agentRegions" rows="7">${esc(regions.length?JSON.stringify(regions,null,2):'[]')}</textarea></details>
    <p class="modaltext">O agente cria candidatas. Leitura, classificação e erro do ajuste serão mostrados separadamente. A revisão manual continua disponível.</p><div class="modalactions"><button id="agentNo">Cancelar</button><button id="agentGo" class="primary">Iniciar análise</button></div>`,b=>{
    b.querySelector('#agentNo').onclick=()=>$('#modal').close();
    b.querySelector('#agentGo').onclick=async()=>{
      let regions;try{regions=JSON.parse(b.querySelector('#agentRegions').value)}catch{notice('JSON de regiões inválido.','error');return}
      const body={mode:b.querySelector('#agentMode').value,pages:b.querySelector('#agentPages').value==='all'?null:[S.page],regions:regions.length?{[S.page]:regions}:{}};
      const pid=S.project.id;$('#modal').close();
      const job=await guard('Iniciando agente…',()=>post(`/api/projects/${pid}/agent`,body));
      if(job){S.agentJob={id:job.id,pid};localStorage.setItem('geodoc-agent',JSON.stringify(S.agentJob));pollAgent()}
    };
  });
};
async function pollAgent(){
  const job=S.agentJob;if(!job)return;
  try{
    const state=await (await api(`/api/projects/${job.pid}/agent/${job.id}`)).json();
    const box=$('#agentProgress');box.hidden=false;
    box.innerHTML=`<strong>${esc(TOOL_LABELS[state.stage]||state.stage||state.status)}</strong><small>Página ${state.page||1} · ${state.tool_calls||0} ferramentas · ${fmt(state.elapsed_seconds||0,0)} s · US$ ${fmt(state.estimated_cost_usd||0,4)}</small><small>${esc(state.events?.at(-1)?.reason||'Preparando análise')}</small>`;
    if(['queued','running'].includes(state.status)){
      const cancel=document.createElement('button');cancel.textContent='Cancelar análise';cancel.onclick=()=>post(`/api/projects/${job.pid}/agent/${job.id}/cancel`,{});box.appendChild(cancel);
      setTimeout(pollAgent,900);return;
    }
    S.agentJob=null;localStorage.removeItem('geodoc-agent');
    box.innerHTML+=`<small>${esc(state.error||state.status)}</small>`;
    if(S.project?.id===job.pid&&['completed','partial'].includes(state.status)){await reload();notice('Análise concluída. Consulte o relatório e revise as camadas propostas.')}
    else notice(state.error||'Execução encerrada.','error');
  }catch(e){notice(e.message,'error');setTimeout(pollAgent,2500)}
}
$('#agentReportBtn').onclick=()=>{
  const p=S.project,r=p?.agent_report;
  if(!r){notice('Execute Analisar com agente para gerar o relatório.');return}
  modal('Relatório rastreável de extração',`<p class="modaltext">${esc(r.status)} · ${r.tool_calls} ferramentas · ${fmt(r.elapsed_seconds,1)} s · modelo ${r.model_used?'utilizado':'não utilizado'}. ${esc(r.evaluation_note)}</p>
    <p><a target="_blank" href="/api/projects/${p.id}/agent-report">Baixar relatório JSON</a> · <a target="_blank" href="/api/projects/${p.id}/agent-source/text/${S.page}">Texto/OCR com posições</a></p>
    <table class="data-table"><tr><th>Etapa</th><th>Motivo</th><th>Resultado</th><th>Tempo</th></tr>${r.tools.map(t=>`<tr><td>${esc(TOOL_LABELS[t.tool]||t.tool)}</td><td>${esc(t.reason)}</td><td>${esc(t.status)}</td><td>${fmt(t.duration_seconds,2)} s</td></tr>`).join('')}</table>
    <details open><summary>Qualidade e pendências</summary><p class="modaltext">Não identificados: ${r.quality.unidentified.length}; vínculos ambíguos: ${r.quality.ambiguous_text_links.length}; geometrias inválidas: ${r.quality.invalid_geometries.length}; sem posição: ${r.quality.without_georeferencing.length}. Erro posicional externo: ${r.external_positional_error_m??'não medido'}.</p><ul>${r.issues.map(i=>`<li>${esc(i)}</li>`).join('')}</ul></details>
    <details><summary>Comparação nas mesmas regiões e investigação do motor anterior</summary><pre class="json-view">${esc(JSON.stringify({comparison:r.comparison,legacy:r.legacy_findings},null,2))}</pre></details>`);
};

/* ---------- toolbar / steps / upload ---------- */
document.querySelectorAll('[data-zoom]').forEach(b=>b.onclick=()=>{
  const [t,a]=b.dataset.zoom.split(':'),v=t==='page'?pageView:mapView;
  if(a==='in')v.zoom(1.4);else if(a==='out')v.zoom(1/1.4);else t==='page'?fitPage():fitMap();
});
$('#prevPage').onclick=()=>{S.page--;renderPage();fitPage();renderMap();fitMap()};
$('#nextPage').onclick=()=>{S.page++;renderPage();fitPage();renderMap();fitMap()};
$('#basemap').onchange=e=>{S.basemap=e.target.value;renderMap()};
$('#uploadBtn').onclick=()=>$('#uploadFile').click();
$('#stepUpload').onclick=()=>$('#uploadFile').click();
$('#uploadFile').onchange=async e=>{
  const file=e.target.files[0];e.target.value='';if(!file)return;
  const fd=new FormData();fd.append('file',file);
  const p=await guard('Enviando e diagnosticando…',()=>api('/api/upload',{method:'POST',body:fd}).then(r=>r.json()));
  if(p){setProject(p);offerExtract()}
};
function offerExtract(){notice('Diagnóstico concluído: o arquivo foi lido e as fontes foram preservadas.','',{label:'Extrair feições',run:doExtract})}
async function doExtract(){
  if(!S.project)return;
  if(S.project.stage==='extracted'){notice('Este projeto já foi extraído.');return}
  const p=await guard('Extraindo feições candidatas…',()=>api(`/api/projects/${S.project.id}/extract`,{method:'POST'}).then(r=>r.json()));
  if(p){setProject(p,{fit:true});const d=p.diagnostic;notice(`Extração concluída: ${d.generated} candidatas, ${d.rejected} rejeitadas pelo motor. Nada foi aceito automaticamente.`)}
}
$('#stepExtract').onclick=()=>S.project?doExtract():notice('Envie uma planta primeiro.');
$('#stepReview').onclick=()=>document.querySelector('.bottomrow').scrollIntoView({behavior:'smooth'});
$('#stepExport').onclick=()=>document.querySelector('.export').scrollIntoView({behavior:'smooth'});
$('#exampleSelect').onchange=async e=>{
  const name=e.target.value;e.target.value='';if(!name)return;
  const p=await guard('Abrindo exemplo e extraindo…',()=>post('/api/examples',{name}));
  if(p){setProject(p);const d=p.diagnostic;notice(`Exemplo extraído: ${d.generated} feições candidatas, ${d.rejected} rejeitadas. Revise antes de exportar.`)}
};

/* ---------- export ---------- */
document.querySelectorAll('[data-export]').forEach(b=>b.onclick=async()=>{
  if(!S.project)return;
  const layers=Object.keys(CATS).filter(c=>!S.hidden.has(c)).join(',');
  await guard('Gerando arquivo…',async()=>{
    const types=['polygon','line','point','annotation'].filter(t=>!S.hiddenTypes.has(t)).join(',');
    const r=await api(`/api/projects/${S.project.id}/export/${b.dataset.export}?layers=${encodeURIComponent(layers)}&types=${types}`);
    const blob=await r.blob(),a=document.createElement('a');
    a.href=URL.createObjectURL(blob);a.download=(r.headers.get('Content-Disposition')||'').match(/filename="([^"]+)"/)?.[1]||'camadas';
    a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);
    notice('Exportação concluída com as feições aceitas e georreferenciadas das camadas visíveis.');
  });
});

/* ---------- init ---------- */
window.addEventListener('resize',()=>{if(S.project){pageView.apply();mapView.apply()}});
(async()=>{
  try{
    S.config=await (await api('/api/config')).json();
    const ex=await (await api('/api/examples')).json();
    $('#exampleSelect').innerHTML='<option value="">Abrir exemplo…</option>'+ex.map(x=>`<option>${esc(x.name)}</option>`).join('');
    $('#connection').textContent='API local • conectada';
  }catch{$('#connection').textContent='API indisponível';notice('Servidor local indisponível.','error')}
  renderAll();
  const q=new URLSearchParams(location.search).get('project');
  if(q)try{setProject(await (await api('/api/projects/'+q)).json(),{fit:true})}catch{}
  try{const job=JSON.parse(localStorage.getItem('geodoc-agent'));if(job){S.agentJob=job;pollAgent()}}catch{}
})();
window.__S=S;
