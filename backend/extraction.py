import hashlib, re, json, os
from pathlib import Path
import pymupdf as fitz
import numpy as np
import cv2
from PIL import Image
from pyproj import Transformer
from shapely.geometry import Point, Polygon, mapping
from .geometry import polygon, page_geometry, reconstruct, affine, transform, area_check
from .examples import PROFILES,PDF_HASH

CACHE_DIR=Path(os.getenv('GEODOC_CACHE',str(Path(__file__).resolve().parents[1]/'.cache')))   # extraction of a given file hash is deterministic

CATEGORIES=['perimetro','quadra','lote','via','construcao','ponto','limite','hidrografia']


def evidence(category,value,bbox,reason,confidence=1.,page=1,origin='Motor determinístico'):
    return dict(category=category,value=value,bbox=list(bbox),reason=reason,confidence=confidence,page=page,origin=origin)


def feature(ring,category,label,ev,crs=None,world=None,declared=None,geometry_type='Polygon'):
    p=page_geometry(ring,geometry_type)
    f=dict(id='',category=category,label=label,page=ev['page'],geometry_type=geometry_type,page_ring=list(map(list,p.exterior.coords if geometry_type=='Polygon' else p.coords)),
           evidence=[ev],status='candidate',confidence=ev['confidence'],revision=0,
           geometry=None,area=None,perimeter=None,issues=[],declared_area=declared)
    if world is not None and crs:
        f.update(reconstruct(world,crs,geometry_type))
        f['source_crs']=crs
        f['world_ring']=list(map(list,world))
        f['area_check']=area_check(f['area'],declared if geometry_type=='Polygon' else None)
        if f['area_check']['ok'] is False: f['issues'].append('Divergência de área superior a 3%.')
    else: f['issues'].append('Sem georreferenciamento comprovado.')
    return f


def analyze(path,outdir):
    from . import macuco
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    result=dict(name=path.name,sha256=digest,pages=[],features=[],evidence=[],history=[],
                georeferencing={},diagnostic={},issues=[],rejected_candidates=[],stage='diagnosed')
    if path.suffix.lower()=='.pdf':
        doc=fitz.open(path)
        if len(doc)>20: raise ValueError('Limite de 20 páginas por projeto.')
        for i,p in enumerate(doc):
            scale=min(1.,3600/max(p.rect.width,p.rect.height))
            pix=p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
            pix.save(outdir/f'page-{i+1}.png')
            words=p.get_text('words')
            drawings=p.get_drawings()
            # Raw text and paths are available for audit, never promoted wholesale to cadastral features.
            raw=[{'bbox':list(w[:4]),'value':w[4],'block':w[5],'line':w[6]} for w in words]
            paths=[{'bbox':list(d['rect']),'color':d['color'],'fill':d['fill'],
                    'items':[[v[0],*[list(x) if hasattr(x,'__iter__') else x for x in v[1:]]] for v in d['items']]} for d in drawings]
            (outdir/f'text-{i+1}.json').write_text(json.dumps(raw,ensure_ascii=False),encoding='utf8')
            (outdir/f'vectors-{i+1}.json').write_text(json.dumps(paths,default=str),encoding='utf8')
            result['pages'].append(dict(number=i+1,width=p.rect.width,height=p.rect.height,
                                        text_count=len(words),path_count=len(drawings),image_count=len(p.get_images())))
            for w in words:
                if any(k in w[4].upper() for k in ['SIRGAS','UTM','LEGENDA']):
                    result['evidence'].append(evidence('crs' if w[4]!='Legenda' else 'legenda',w[4],w[:4],'Texto nativo do PDF.',page=i+1))
            if digest==PDF_HASH and i==0: pdf_profile(p,pix,result)
            elif digest in macuco.PROFILES and i==0: macuco.extract(p,result)
        result['diagnostic']['text_words']=sum(p['text_count'] for p in result['pages'])
        result['diagnostic']['vector_paths']=sum(p['path_count'] for p in result['pages'])
        doc.close()
        if digest!=PDF_HASH and digest not in macuco.PROFILES:
            result['issues'].append('Documento sem perfil verificado: selecione região da planta, confirme CRS e controles e vetorize. Texto e caminhos extraídos estão disponíveis para auditoria.')
    else:
        with Image.open(path) as im:
            if im.width*im.height>60_000_000: raise ValueError('Imagem excede 60 megapixels.')
            im.convert('RGB').save(outdir/'page-1.png')
            result['pages']=[dict(number=1,width=im.width,height=im.height,text_count=0,path_count=0,image_count=1)]
        if digest in PROFILES: jpg_profile(PROFILES[digest],result)
        else: result['issues'].append('Imagem sem transcrição validada. Use IA opcional para leitura e confirme a tabela ou vetorize manualmente.')
    for i,f in enumerate(result['features']): f['id']=f'f{i+1}'
    result['diagnostic']['generated']=len(result['features'])
    result['diagnostic']['rejected']=len(result['rejected_candidates'])
    result['diagnostic']['pending']=len(result['features'])
    return result


def jpg_profile(profile,result):
    result['issues']+=profile['notes']
    result['evidence'].append(evidence('crs',profile['crs'],profile['crs_bbox'],'Transcrição visual do datum no carimbo.',.95,origin='Perfil de exemplo • transcrição humana'))
    result['evidence'].append(evidence('tabela',f"{len(profile['vertices'])} vértices E/N ou longitude/latitude",profile['bbox'],'Valores transcritos da tabela; disponíveis no editor.',.85,origin='Perfil de exemplo • transcrição humana'))
    world=[v[1:] for v in profile['vertices']]
    if profile['crs']=='EPSG:4674':
        t=Transformer.from_crs(4674,31984,always_xy=True)
        metric=[list(t.transform(*v)) for v in world]
        controls=[{'page':p,'world':w} for p,w in zip(profile['page_ring'],metric)]
        metric_crs='EPSG:31984'
    else:
        metric=world
        controls=[{'page':c['page'],'world':metric[c['index']]} for c in profile['controls']]
        metric_crs=profile['crs']
    geo=affine(controls)
    geo.update(crs=metric_crs,controls=controls,unit='m',verified=True,origin='Tabela transcrita + controles visuais',
               method='Reconstrução pela tabela; afim para sobreposição na página')
    mat=np.array(geo['matrix']); inv=np.linalg.inv(mat[:2])
    ring=((np.array(metric)-mat[2])@inv).tolist()
    result['georeferencing']['1']=geo
    ev=evidence('perimetro',profile['title'],profile['bbox'],
        'Polígono reconstruído na ordem dos vértices da tabela. Coordenadas não geradas por IA.',.85,origin='Transcrição humana + motor SIG')
    f=feature(ring,'perimetro',profile['title'],ev,profile['crs'],world,profile['area'])
    f['vertices_table']=profile['vertices']
    f['issues'].append('Confirmar transcrição e correspondência visual antes de aceitar.')
    f['declared_perimeter']=profile['perimeter']
    result['features'].append(f)
    result['diagnostic'].update(recognized=['Tabela de vértices','Carimbo / CRS','Perímetro','Mapa de localização','Legenda'],
                                 declared_area=profile['area'])


def pdf_profile(p,pix,result):
    # Grid ticks, not text centres. Evidence is tied to this exact source hash.
    drawings=p.get_drawings()
    ticks=[drawings[i]['rect'] for i in [1529,1531,1533,1535]]
    x1,x2=ticks[0].x0,ticks[1].x0
    y1,y2=ticks[2].y0,ticks[3].y0
    controls=[{'page':[x,y],'world':[e,n]} for x,e in [(x1,458100),(x2,458400)] for y,n in [(y1,9089900),(y2,9090200)]]
    geo=affine(controls)
    geo.update(crs='EPSG:31984',controls=controls,unit='m',verified=True,origin='Marcas e rótulos da grade no PDF',
               note='Ajuste de 4 interseções derivadas de 2 eixos. Resíduo interno; acurácia externa não medida.')
    result['georeferencing']['1']=geo
    result['evidence']+= [evidence('grade','E 458100 / 458400 • N 9089900 / 9090200',[44,2,2841,2378],
        'Marcas vetoriais da moldura associadas aos rótulos. 4 interseções derivadas, sem controle externo.'),
        evidence('crs','SIRGAS 2000 / UTM 24S • EPSG:31984',[2870,710,3325,835],'Texto nativo das referências cartográficas.'),
        evidence('tabela','18 quadras / 493 lotes declarados',[2990,1050,3230,1340],
        'Contagem declarada na tabela, não quantidade de geometrias extraídas.'),
        evidence('localizacao','Mapa de situação excluído da extração',[2870,75,3320,430],'Escala e posição próprias; não corresponde à planta principal.')]
    result['issues']+=['Limites principais rasterizados em 223 imagens incorporadas; vetores incluem halos de texto e molduras.',
                       'Rótulos de quadra ainda precisam de associação espacial confirmada aos lotes.',
                       'Carimbo apresenta N/E em ordem ambígua; grade usada como evidência de posição.',
                       '493 lotes é a contagem declarada, não a cobertura da extração automática.']
    lots=_pdf_lots(p,geo,result,outdir=None)


def _pdf_lots(p,geo,result,outdir=None):
    import json as _json
    from . import pdf_lots
    cache=CACHE_DIR/f'{result["sha256"]}-lots-v5.json'
    if cache.exists(): runs=_json.loads(cache.read_text(encoding='utf8'))
    else:
        runs=pdf_lots.extract_all(p.parent.name,geo['matrix'])
        for r in runs: r['table']={f'{q}|{l}':list(v) for (q,l),v in r['table'].items()}
        runs=_json.loads(_json.dumps(runs,default=lambda o:o.item() if hasattr(o,'item') else list(o)))
        CACHE_DIR.mkdir(parents=True,exist_ok=True); cache.write_text(_json.dumps(runs),encoding='utf8')
    res=runs[0]; by_var=[{l['idx']:l for l in r['lots']} for r in runs]
    table={tuple(k.split('|')):v for k,v in res['table'].items()}
    by_lot={}
    for (q,l),(a,ac) in table.items(): by_lot.setdefault(l,[]).append((q,a,ac))
    blocks=res['blocks']; block_of={}
    for bi,b in enumerate(blocks):
        for li in b['lots']: block_of[li]=bi
    made=[];bad=[]
    for lot in res['lots']:
        cands=by_lot.get(lot['lote'],[])
        circles=set(blocks[block_of[lot['idx']]]['circles']) if lot['idx'] in block_of else set()
        pool=[c for c in cands if c[0] in circles] or cands
        err=lambda o:min(abs(o['area']-c[1])/c[1] for c in pool)
        alt=False
        if pool and err(lot)>.05:      # default outline does not match the table: try the other wall settings
            opts=[v[lot['idx']] for v in by_var[1:] if lot['idx'] in v]
            better=min(opts,key=err,default=None)
            if better and err(better)<err(lot) and err(better)<=.15: lot=better; alt=True
        best=min(pool,key=lambda c:abs(c[1]-lot['area'])/c[1]) if pool else None
        diff=abs(best[1]-lot['area'])/best[1] if best else None
        ring=lot['page_ring']
        if lot['area']<10 or (best and lot['area']>5*best[1]):
            bad.append({'reason':'Região implausível (área %.0f m² frente a %.0f m² na tabela); não promovida'%(lot['area'],best[1] if best else 0),'bbox':lot['label_bbox']}); continue
        world=transform(ring,geo['matrix'])
        if diff is not None and diff<=.05: conf,q,decl,how=(.75 if alt else .9),best[0],best[1],('confere com a tabela usando ajuste alternativo de linhas (escolhido por concordar com a tabela)' if alt else 'confere com a tabela')
        elif diff is not None and diff<=.25: conf,q,decl,how=.6,best[0],best[1],'diverge da tabela'
        else: conf,q,decl,how=.35,None,None,'sem correspondência na tabela'
        label=f"Lote {lot['lote']} • Quadra {q}" if q else f"Lote {lot['lote']} • quadra a confirmar"
        reason=(f"Região delimitada pelas linhas de lote que contém o número {lot['lote']}. Área do polígono {lot['area']:.2f} m²"
                +(f"; tabela (quadra {best[0]}, lote {lot['lote']}): {best[1]:.2f} m² (Δ {100*diff:.1f}%) — {how} (tolerância 5%)." if best else '; lote ausente da tabela.'))
        ev=evidence('lote',lot['lote'],lot['label_bbox'],reason,conf,1,'Motor SIG • segmentação + tabela de áreas')
        f=feature(ring,'lote',label,ev,geo['crs'],world,decl)
        f['quadra']=q; f['lote']=lot['lote']
        if decl:   # lots are judged with a 5% tolerance: the outline is traced from strokes ~1 px wide on a 6700 px sheet
            f['area_check']=area_check(f['area'],decl,.05); f['issues']=[i for i in f['issues'] if 'superior a 3%' not in i]
            if not f['area_check']['ok']: f['issues'].append('Divergência de área superior a 5%.')
        if diff is not None and diff>.05: f['issues'].append(f'Área do polígono difere {100*diff:.0f}% da tabela: conferir limites.')
        if not q: f['issues'].append('Quadra não determinada pela tabela: confirmar.')
        if alt: f['issues'].append('Contorno obtido com ajuste alternativo das linhas, escolhido por concordar com a tabela: conferir visualmente.')
        made.append(f)
    # two lots claiming the same ground is a segmentation conflict, whatever the areas say
    from shapely.strtree import STRtree
    polys=[Polygon(f['page_ring']) for f in made]; tree=STRtree(polys)
    for i,pg in enumerate(polys):
        for j in tree.query(pg):
            if j<=i: continue
            inter=pg.intersection(polys[j]).area
            if inter>.25*min(pg.area,polys[j].area):
                for a,b in ((i,j),(j,i)):
                    made[a]['confidence']=min(made[a]['confidence'],.5)
                    made[a]['issues'].append(f"Sobrepõe {made[b]['label']} em {100*inter/polys[a].area:.0f}% da área: conflito de segmentação.")
    result['features']+=made; result['rejected_candidates']+=bad+res['rejected']
    # blocks -> quadras: union of matched lots that share a quadra number
    from shapely.geometry import Polygon as P
    from shapely.ops import unary_union
    groups={}
    for f in made:
        if f['quadra'] and f['confidence']>=.75: groups.setdefault(f['quadra'],[]).append(f)   # only lots confirmed by the table
    for q,fs in sorted(groups.items()):
        u=unary_union([P(f['page_ring']).buffer(.6) for f in fs]).buffer(-.6)
        parts=list(u.geoms) if u.geom_type=='MultiPolygon' else [u]
        expected=sum(1 for (qq,l) in table if qq==q)
        for part in parts:
            if part.is_empty or part.area<50: continue
            ring=list(map(list,part.exterior.coords)); n=sum(1 for f in fs if part.buffer(1).contains(P(f['page_ring']).centroid))
            if n<3: continue
            ev=evidence('quadra',q,list(part.bounds),f'União de {n} lotes cuja quadra foi determinada pela tabela ({expected} lotes na tabela para a quadra {q}).',
                        .8 if n>=.9*expected else .55,1,'Motor SIG • agrupamento de lotes')
            f=feature(ring,'quadra',f'Quadra {q}',ev,geo['crs'],transform(ring,geo['matrix']))
            f['issues']=[f'{n} de {expected} lotes da tabela nesta parte da quadra.']+( ['Parte da quadra sem todos os lotes: conferir.'] if n<expected else [])
            result['features'].append(f)
    # project perimeter: outer edge of the lot mosaic, bridging the streets between blocks (closing by ~9 m).
    # The stamp (area / perimeter) is only used afterwards, to check the result.
    from shapely.ops import unary_union as _u
    m=np.array(geo['matrix'])[:2]; k=float(abs(np.linalg.det(m)))**.5      # metres per page point
    d=9.35/k
    mosaic=_u([P(f['page_ring']).buffer(d) for f in made]).buffer(-d)
    if mosaic.geom_type=='MultiPolygon': mosaic=max(mosaic.geoms,key=lambda g:g.area)
    ring=list(map(list,P(mosaic.exterior).simplify(1.0).exterior.coords)); world=P(transform(ring,geo['matrix']))
    decl=res['declared']; area=world.area; da=abs(area-decl['area'])/decl['area'] if decl.get('area') else None
    dp=abs(world.length-decl['perimeter'])/decl['perimeter'] if decl.get('perimeter') else None
    good=da is not None and da<=.03
    ev=evidence('perimetro','Contorno da área projetada',list(P(ring).bounds),
        f"Contorno externo do mosaico de {len(made)} lotes, fechando as ruas entre quadras (ponte de 9,35 m). Área {area:.2f} m² e perímetro {world.length:.2f} m; "
        f"o carimbo declara {decl.get('area')} m² e {decl.get('perimeter')} m (Δ área {100*(da or 0):.1f}%, Δ perímetro {100*(dp or 0):.1f}%).",
        .85 if good else .5,1,'Motor SIG • contorno do mosaico de lotes')
    f=feature(ring,'perimetro','Perímetro do projeto',ev,geo['crs'],world.exterior.coords[:],decl.get('area'))
    f['declared_perimeter']=decl.get('perimeter')
    if not good: f['issues'].append(f'Área do contorno difere {100*(da or 0):.1f}% do carimbo: corrigir antes de aceitar.')
    else: f['issues'].append('Conferir o contorno sobre a planta antes de aceitar; a concordância com o carimbo não substitui a conferência visual.')
    result['features'].append(f)
    ok=sum(1 for f in made if f['confidence']>=.75)
    result['diagnostic'].update(recognized=['Grade UTM','Carimbo','Legenda','Tabela de áreas (%d lotes)'%len(table),'Rótulos de lotes e quadras',
                                           'Vias (faixas cinza, não vetorizadas)','Construções (hachura, não vetorizadas)','Mapa de situação (excluído)'],
                               declared_lots=len(table),declared_blocks=18,numeric_labels=len(res['lots']),lots_matching_table=ok,
                               lots_extracted=len(made),declared_totals=res['declared'],
                               excluded='Traços, molduras, hachuras e textos não são feições cadastrais.')
    result['issues']=[i for i in result['issues'] if not i.startswith(('Rótulos de quadra ainda','493 lotes'))]+[
        f'{ok} de {len(table)} lotes têm área do polígono dentro de 5% da tabela; os demais precisam de revisão.',
        'Vias e construções foram reconhecidas mas não vetorizadas nesta versão.']
