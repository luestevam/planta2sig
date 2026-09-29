"""Real, bounded document tools. All candidate coordinates come from libraries."""
import copy
import hashlib
import json
import math
import os
import re
import time
import unicodedata
from pathlib import Path
import cv2
import numpy as np
import pymupdf as fitz
from PIL import Image
from pyproj import CRS, Transformer
from shapely.geometry import Point, Polygon, LineString, box, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree
from .agent_schema import Region, normalize_feature
from .embedded_geo import inspect as inspect_embedded
from .extraction import feature, evidence, analyze
from .geometry import transform, reconstruct, affine
from .examples import PDF_HASH, PROFILES as IMAGE_PROFILES
from .macuco import PROFILES as MACUCO_PROFILES

ROOT = Path(__file__).resolve().parents[1]


def slug(value):
    value=unicodedata.normalize('NFKD',value).encode('ascii','ignore').decode().lower()
    return re.sub('[^a-z0-9]+','_',value).strip('_')[:60] or 'nao_identificado'


def read_text(page, textpage=None, method='native_text'):
    items=[]
    for block in page.get_text('dict', textpage=textpage, flags=fitz.TEXTFLAGS_TEXT)['blocks']:
        for line in block.get('lines',[]):
            direction=line.get('dir',(1,0))
            angle=math.degrees(math.atan2(direction[1],direction[0]))
            for span in line['spans']:
                if not span['text'].strip(): continue
                quad=fitz.recover_quad(direction,span)
                items.append({'id':f't{page.number+1}_{method}_{len(items)}','page':page.number+1,
                              'text':span['text'],'bbox':list(span['bbox']),
                              'quad':[list(quad.ul),list(quad.ur),list(quad.lr),list(quad.ll)],
                              'anchor':list(span['origin']),'orientation':angle,'font':span['font'],
                              'font_size':span['size'],'method':method,
                              'reading_quality':1. if method=='native_text' else None})
    return items


def drawing_style(d):
    color=d.get('color') or d.get('fill')
    return {'color':list(color) if color else None,'fill':list(d['fill']) if d.get('fill') else None,
            'width':float(d.get('width') or 0),'dashes':str(d.get('dashes') or '[] 0'),
            'symbol':None, 'path_type':d['type']}


def vector_parts(d):
    """Split discontinuous paths; flatten cubic curves deterministically."""
    parts=[]; current=[]
    for item in d['items']:
        kind=item[0]
        if kind=='l': pts=[list(item[1]),list(item[2])]
        elif kind=='re':
            r=item[1]; pts=[[r.x0,r.y0],[r.x1,r.y0],[r.x1,r.y1],[r.x0,r.y1],[r.x0,r.y0]]
        elif kind=='qu':
            q=item[1]; pts=[list(q.ul),list(q.ur),list(q.lr),list(q.ll),list(q.ul)]
        elif kind=='c':
            a,b,c,e=(np.array(p) for p in item[1:])
            ts=np.linspace(0,1,13)
            pts=[((1-t)**3*a+3*(1-t)**2*t*b+3*(1-t)*t*t*c+t**3*e).tolist() for t in ts]
        else: continue
        if current and np.linalg.norm(np.array(current[-1])-pts[0])>.001:
            parts.append(current); current=[]
        current.extend(pts if not current else pts[1:])
        if kind in ('re','qu'): parts.append(current); current=[]
    if current:
        if d.get('closePath') and current[0]!=current[-1]: current.append(current[0])
        parts.append(current)
    return parts


class DocumentTools:
    def __init__(self, project, directory, request, limits, deadline, provider=None):
        self.project=copy.deepcopy(project); self.directory=Path(directory)
        self.source=self.directory/project['name']; self.request=request; self.limits=limits
        self.deadline=deadline; self.provider=provider; self.cloud_enabled=request.mode=='ai'
        self.pages={}; self.current=1; self.logs=[]; self.issues=[]; self.rejected=[]
        self.original=copy.deepcopy(project['features']); self.baseline=None
        self.generated=[]; self.classes={c['id']:c for c in project.get('classes',[])}
        self.classes.setdefault('nao_identificado',{'id':'nao_identificado','name':'Não identificado','color':'#87939d','evidence':[]})
        self.classes.setdefault('anotacao',{'id':'anotacao','name':'Anotações','color':'#7753a6','evidence':[]})
        self.done={}; self.stats={}; self.source_registry={}

    def checkpoint(self):
        if time.monotonic()>=self.deadline: raise TimeoutError('Limite de tempo da análise atingido.')

    def pdf(self):
        if self.source.suffix.lower()=='.pdf': return fitz.open(self.source)
        # Same page coordinates as the existing image viewer: one unit per pixel.
        with Image.open(self.source) as im: width,height=im.size
        doc=fitz.open(); p=doc.new_page(width=width,height=height); p.insert_image(p.rect,filename=str(self.directory/'page-1.png'))
        return doc

    def persist_source(self,name,data):
        target=self.directory/f'agent-{name}-{self.current}.json'
        target.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,default=lambda o:list(o)),encoding='utf8')
        return target.name

    def diagnostics(self):
        selected=self.request.pages or [p['number'] for p in self.project['pages']]
        if any(i not in [p['number'] for p in self.project['pages']] for i in selected): raise ValueError('Página inexistente.')
        with self.pdf() as doc:
            for i in selected:
                self.checkpoint(); p=doc[i-1]
                drawings=p.get_drawings(); native=read_text(p)
                self.pages[i]={'info':next(x for x in self.project['pages'] if x['number']==i),
                               'drawings':drawings,'texts':native,'regions':[],'embedded':None,
                               'needs_ocr':len(native)<5, 'raster':bool(p.get_images()),
                               'image_boxes':[list(x['bbox']) for x in p.get_image_info()],
                               'native_text_count':len(native),'truncated':False}
                for t in native:self.source_registry[t['id']]=t
                for j,d in enumerate(drawings):
                    self.source_registry[f'v{i}_{j}']={'id':f'v{i}_{j}','bbox':list(d['rect']),'page':i,'style':drawing_style(d)}
                self.done[i]=set()
        return {'pages':[{k:v for k,v in x.items() if k in ('info','needs_ocr','raster','native_text_count')} for x in self.pages.values()]}

    @property
    def state(self): return self.pages[self.current]

    def embedded(self):
        if self.source.suffix.lower()!='.pdf': result={'checked':True,'viewports':[],'issues':[]}
        else:
            with self.pdf() as doc: result=inspect_embedded(doc[self.current-1])
        self.state['embedded']=result; self.issues.extend(result['issues'])
        self.persist_source('embedded',result)
        return result

    def native_text(self):
        return {'count':len(self.state['texts']),'artifact':self.persist_source('text',self.state['texts'])}

    def ocr(self):
        tessdata=Path(os.getenv('OCR_TESSDATA',str(ROOT/'models/tessdata')))
        languages=os.getenv('OCR_LANGUAGES','por+eng')
        if any(not (tessdata/(lang+'.traineddata')).exists() for lang in languages.split('+')):
            self.issues.append(f'Página {self.current}: OCR indisponível; configure OCR_TESSDATA. Leitura e classificação permanecem pendentes.')
            return {'status':'unavailable','count':0}
        with self.pdf() as doc:
            p=doc[self.current-1]
            # Raster size capped to avoid unbounded A0 OCR allocation.
            dpi=min(240,max(72,int(4000/max(p.rect.width,p.rect.height)*72)))
            tp=p.get_textpage_ocr(language=languages,dpi=dpi,full=True,tessdata=str(tessdata))
            texts=read_text(p,tp,'tesseract_ocr')
        self.state['texts'].extend(texts)
        for t in texts:self.source_registry[t['id']]=t
        return {'status':'completed','count':len(texts),'dpi':dpi,'reading_quality':None,
                'note':'Tesseract via PyMuPDF não fornece escore calibrado por palavra; leitura exige revisão.',
                'artifact':self.persist_source('text',self.state['texts'])}

    def summary(self):
        s=self.state
        return {'page':self.current,'size':[s['info']['width'],s['info']['height']],
                'diagnostic':{'needs_ocr':s['needs_ocr'],'raster':s['raster'],'paths':len(s['drawings'])},
                'regions':s['regions'],'texts':s['texts'][:250],
                'symbols':[v for k,v in self.source_registry.items() if k.startswith(f'v{self.current}_')][:100],
                'classes':list(self.classes.values()),
                'features':[{'id':f['id'],'page':f['page'],'type':f['object_type'],'bbox':f['evidence'][0]['bbox'],
                             'style':f['attributes'].get('style'),'class_id':f['category']}
                            for f in self.generated if f['page']==self.current][:200],
                'reviewed_examples':self.project.get('reviewed_examples',[])[-12:],
                'completed_tools':sorted(self.done[self.current]),'issues':self.issues[-12:]}

    def model_semantics(self,task):
        if not self.cloud_enabled or not self.provider:return None
        try:
            payload=self.summary();payload['task']=task
            return self.provider.interpret(payload,self.directory/f'page-{self.current}.png')
        except ValueError as exc:
            self.issues.append(str(exc)); self.cloud_enabled=False; return None

    def regions(self):
        s=self.state; w,h=s['info']['width'],s['info']['height']; digest=self.project['sha256']
        regions=[]
        explicit=self.request.regions.get(str(self.current))
        if explicit:
            regions=[dict(r.model_dump(),confirmed=True,method='reviewer') for r in explicit]
        elif digest==PDF_HASH and self.current==1:
            # Auditable source-specific zones; baseline and agent use identical main-map ROI.
            for role,bounds in [('main_map',[70,28,2835,2356]),('legend',[2870,1960,3100,2120]),
                                ('stamp',[2870,435,3330,850]),('table',[40,1180,1150,2370]),
                                ('table',[2980,1040,3300,1320]),('location_map',[2870,70,3330,435])]:
                regions.append(dict(id=f'r{self.current}_{len(regions)}',role=role,bbox=bounds,evidence_ids=[],confirmed=True,method='hash_verified_profile'))
        elif digest in MACUCO_PROFILES and self.current==1:
            profile=MACUCO_PROFILES[digest]; b=s['drawings'][profile['boundary']]['rect']
            hb=s['drawings'][profile['hydro'][0][0]]['rect']; bounds=list(b|hb)
            specs=[('main_map',[bounds[0]-8,bounds[1]-8,bounds[2]+8,bounds[3]+8]),
                   ('table',profile['bbox']),('legend',[655,105,725,185]),('stamp',[655,100,834,185]),
                   ('stamp',[655,309,834,520]),('location_map',[655,185,834,310])]
            regions=[dict(id=f'r{self.current}_{i}',role=role,bbox=bounds,evidence_ids=[],confirmed=True,method='hash_verified_profile') for i,(role,bounds) in enumerate(specs)]
        elif digest in IMAGE_PROFILES and self.current==1:
            profile=IMAGE_PROFILES[digest]
            pts=profile.get('page_ring') or [c['page'] for c in profile.get('controls',[])]
            if pts:
                bounds=list(Polygon(pts).bounds)
                specs=[('main_map',[max(0,bounds[0]-30),max(0,bounds[1]-30),min(w,bounds[2]+30),min(h,bounds[3]+30)]),
                       ('table',profile['bbox']),('stamp',profile['crs_bbox'])]
                regions=[dict(id=f'r{self.current}_{i}',role=role,bbox=b,evidence_ids=[],confirmed=True,method='hash_verified_profile') for i,(role,b) in enumerate(specs)]
        else:
            valid=[v for v in (s['embedded'] or {}).get('viewports',[]) if v['verified']]
            if valid:
                largest=max(valid,key=lambda g:box(*g['scope_bbox']).area)
                for i,v in enumerate(valid):
                    regions.append(dict(id=f'r{self.current}_{i}',role='main_map' if v is largest else 'location_map',bbox=v['scope_bbox'],evidence_ids=[],confirmed=True,method='embedded_viewport'))
            proposal=self.model_semantics('Proponha regiões da página, sem geometrias finais. Preserve metadados existentes.')
            if proposal:
                regions.extend(dict(r.model_dump(),confirmed=False,method='model_proposal') for r in proposal.regions)
                self.issues.extend(proposal.issues)
            if not regions:
                # Text blocks provide evidence for ancillary zones; uncertain main map is disclosed.
                terms={'legend':('legenda','convenções'),'stamp':('responsável','proprietário','carimbo'),
                       'table':('coord.','vértices','quadro de áreas'),'location_map':('situação','localização')}
                for t in s['texts']:
                    text=t['text'].lower()
                    role=next((k for k,words in terms.items() if any(word in text for word in words)),None)
                    if role:
                        x0,y0,x1,y1=t['bbox']
                        regions.append(dict(id=f'r{self.current}_{len(regions)}',role=role,
                                            bbox=[max(0,x0-10),max(0,y0-5),min(w,x1+80),min(h,y1+min(h*.18,180))],
                                            evidence_ids=[t['id']],confirmed=False,method='text_anchor_heuristic'))
                regions.insert(0,dict(id=f'r{self.current}_main',role='main_map',bbox=[0,0,w,h],evidence_ids=[],confirmed=False,method='whole_page_pending'))
                self.issues.append(f'Página {self.current}: extensão do mapa principal não confirmada; revise as regiões antes de aceitar candidatos.')
        checked=[]
        for r in regions:
            r=Region.model_validate(r).model_dump();x0,y0,x1,y1=r['bbox']
            if not 0<=x0<x1<=w or not 0<=y0<y1<=h:
                self.issues.append('Região fora da página descartada.');continue
            if any(i not in self.source_registry for i in r['evidence_ids']):continue
            checked.append(r)
        s['regions']=checked
        main=[box(*r['bbox']) for r in checked if r['role']=='main_map']
        s['raster']=any(box(*b).intersection(m).area>1 for b in s['image_boxes'] for m in main)
        return {'regions':checked,'artifact':self.persist_source('regions',checked)}

    def in_map(self,geom):
        regions=self.state['regions']; center=geom.representative_point()
        main=[box(*r['bbox']) for r in regions if r['role']=='main_map']
        excluded=[box(*r['bbox']) for r in regions if r['role']!='main_map' and r['confirmed']]
        return any(b.covers(geom) for b in main) and not any(b.covers(center) for b in excluded)

    def add(self,points,kind,method,source_id,category='nao_identificado',label=None,attributes=None,quality=None):
        if len(self.generated)>=self.limits.max_features:
            self.state['truncated']=True;return None
        try:
            geom=Point(points[0]) if kind=='Point' else LineString(points) if kind=='LineString' else Polygon(points)
            if not geom.is_valid or geom.is_empty or (kind=='Polygon' and geom.area<=0):
                raise ValueError('Geometria inválida')
            if not self.in_map(geom):return None
            fid=f'a{self.current}_{len(self.generated)+1}'
            ev=evidence(category,label or 'Não identificado',geom.bounds,
                        f'{method}; fonte {source_id}. Classe precisa de evidência.',.0,self.current,method)
            ev['source_id']=source_id
            f=feature(points,category,label or 'Não identificado',ev,geometry_type=kind)
            f.update(id=fid,extraction_method=method,attributes=attributes or {},source_ids=[source_id],
                     quality={'reading':quality,'classification':None,'georeferencing':{'rmse_m':None,'external_accuracy_m':None}})
            normalize_feature(f);self.generated.append(f);return f
        except ValueError as exc:
            self.rejected.append({'source_id':source_id,'reason':str(exc),'page':self.current});return None

    def vectors(self):
        s=self.state;before=len(self.generated);raw=[]
        texts=[box(*t['bbox']) for t in s['texts'] if t['method']=='native_text']
        text_tree=STRtree(texts) if texts else None
        seen=set()
        for i,d in enumerate(s['drawings']):
            if i%100==0:self.checkpoint()
            source=f'v{self.current}_{i}';style=drawing_style(d)
            raw.append({'id':source,'bbox':list(d['rect']),'style':style,
                        'items':[[it[0],*[list(x) if hasattr(x,'__iter__') else x for x in it[1:]]] for it in d['items']]})
            if len(self.generated)-before>=500:s['truncated']=True;continue
            if not self.in_map(box(*d['rect'])):continue
            # Filled glyph outlines are not cadastral polygons.
            if d['type']=='f' and (d['rect'].get_area()<200 or len(d['items'])>100):continue
            if text_tree is not None and any(texts[j].covers(box(*d['rect'])) for j in text_tree.query(box(*d['rect']))):continue
            for pts in vector_parts(d):
                if len(pts)<2:continue
                length=LineString(pts).length
                closed=len(pts)>=4 and np.linalg.norm(np.array(pts[0])-pts[-1])<.01
                if closed:
                    pg=Polygon(pts)
                    if not pg.is_valid: self.rejected.append({'source_id':source,'page':self.current,'reason':'Caminho fechado inválido'});continue
                    if pg.area<3:continue
                    kind='Polygon'
                    if pg.area<100 and len(pts)>=8 and max(d['rect'].width,d['rect'].height)<18:
                        center=pg.centroid;kind='Point';style=dict(style,symbol='closed_small_symbol')
                        original=mapping(pg);pts=[[center.x,center.y]]
                    else:original=mapping(pg)
                else:
                    if length<5:continue
                    kind='LineString';original=mapping(LineString(pts))
                key=(kind,tuple(tuple(round(v,3) for v in p) for p in pts))
                if key in seen:continue
                seen.add(key)
                self.add(pts,kind,'pdf_vector',source,attributes={'style':style,'source_path_geometry':original})
        self.persist_source('vectors',raw)
        return {'generated':len(self.generated)-before,'paths':len(raw),'truncated':s['truncated']}

    def raster(self):
        s=self.state; before=len(self.generated)
        with self.pdf() as doc:
            p=doc[self.current-1]; scale=min(2.,2800/max(p.rect.width,p.rect.height))
            pix=p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
        im=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
        grey=cv2.cvtColor(im,cv2.COLOR_RGB2GRAY)
        mask=cv2.threshold(grey,150,255,cv2.THRESH_BINARY_INV)[1]
        roi=np.zeros_like(mask)
        for r in s['regions']:
            if r['role']=='main_map':
                x0,y0,x1,y1=(int(v*scale) for v in r['bbox']);cv2.rectangle(roi,(x0,y0),(x1,y1),255,-1)
        for t in s['texts']:
            x0,y0,x1,y1=(int(v*scale) for v in t['bbox']);cv2.rectangle(mask,(x0-1,y0-1),(x1+1,y1+1),0,-1)
        mask=cv2.bitwise_and(mask,roi)
        lines=cv2.HoughLinesP(mask,1,np.pi/180,threshold=40,minLineLength=35,maxLineGap=6)
        if lines is not None:
            lines=sorted(lines.reshape(-1,4).tolist(),key=lambda v:-(v[2]-v[0])**2-(v[3]-v[1])**2)
            for i,(x0,y0,x1,y1) in enumerate(lines[:200]):
                self.add([[x0/scale,y0/scale],[x1/scale,y1/scale]],'LineString','raster_hough',f'raster{self.current}_line{i}',
                         attributes={'raster_scale':scale,'tolerance_pixels':6})
            if len(lines)>200:s['truncated']=True
        closed=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        contours,hierarchy=cv2.findContours(closed,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
        count=0
        for i,c in enumerate(contours):
            if i%100==0:self.checkpoint()
            if hierarchy is None or hierarchy[0][i][3]<0:continue  # enclosed free regions, not ink glyphs
            area=cv2.contourArea(c)
            if area<300 or area>mask.size*.6:continue
            pts=(cv2.approxPolyDP(c,1.5,True).reshape(-1,2)/scale).tolist()
            if len(pts)<3:continue
            self.add(pts,'Polygon','raster_enclosed_region',f'raster{self.current}_region{i}',attributes={'raster_scale':scale})
            count+=1
            if count>=100:s['truncated']=True;break
        # Small circular marks are candidates only, never auto-labelled equipment.
        for i,c in enumerate(contours):
            if len(self.generated)-before>=400:s['truncated']=True;break
            area=cv2.contourArea(c);length=cv2.arcLength(c,True)
            if 8<area<200 and length and 4*math.pi*area/length**2>.7:
                (x,y),radius=cv2.minEnclosingCircle(c)
                self.add([[x/scale,y/scale]],'Point','raster_symbol',f'raster{self.current}_symbol{i}',
                         attributes={'symbol':'circular_candidate','radius_page':radius/scale})
        return {'generated':len(self.generated)-before,'raster_scale':scale,'truncated':s['truncated'],
                'note':'Linhas e regiões são candidatos; não equivalem a lotes ou vias sem evidência.'}

    def roads(self):
        """Extract actual connected grey road surfaces in the verified A0 ROI.

        The old extractor already detected this mask but discarded it after lot
        segmentation. Keep it as reviewable polygons; do not invent centerlines.
        """
        if self.project['sha256']!=PDF_HASH:return {'generated':0,'reason':'Sem detector de via sustentado por evidência nesta planta.'}
        road_texts=[t for t in self.state['texts'] if 'via pavimentada' in t['text'].lower()]
        if not road_texts:return {'generated':0,'reason':'Legenda de vias não encontrada.'}
        with self.pdf() as doc:
            p=doc[self.current-1];scale=1.5;pix=p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
        im=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3).astype(np.int16)
        mask=(np.max(np.abs(im-224),axis=2)<=5).astype(np.uint8)
        mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
        roi=np.zeros_like(mask)
        for r in self.state['regions']:
            if r['role']=='main_map':
                x0,y0,x1,y1=(int(v*scale) for v in r['bbox']);cv2.rectangle(roi,(x0,y0),(x1,y1),1,-1)
        for r in self.state['regions']:
            if r['role']!='main_map' and r['confirmed']:
                x0,y0,x1,y1=(int(v*scale) for v in r['bbox']);cv2.rectangle(roi,(x0,y0),(x1,y1),0,-1)
        mask &= roi
        contours,hierarchy=cv2.findContours(mask,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
        self.classes['via']={'id':'via','name':'Vias — superfície candidata','color':'#7d8a96','evidence':road_texts,'status':'proposed'}
        n=0
        for i,c in enumerate(contours):
            if hierarchy is not None and hierarchy[0][i][3]>=0:continue
            if cv2.contourArea(c)<400:continue
            pts=(cv2.approxPolyDP(c,1.5,True).reshape(-1,2)/scale).tolist()
            f=self.add(pts,'Polygon','raster_road_surface',f'road{self.current}_{i}','via','Via — superfície candidata',
                       attributes={'rgb_sample':[224,224,224],'color_tolerance':5,'raster_scale':scale,'legend_text_id':road_texts[0]['id']})
            if f:
                holes=[];child=int(hierarchy[0][i][2]) if hierarchy is not None else -1
                while child>=0:
                    hole=(cv2.approxPolyDP(contours[child],1.5,True).reshape(-1,2)/scale).tolist()
                    if len(hole)>=3 and cv2.contourArea(contours[child])>20:holes.append(hole)
                    child=int(hierarchy[0][child][0])
                poly=Polygon(pts,holes)
                if not poly.is_valid:
                    self.generated.remove(f);self.rejected.append({'page':self.current,'source_id':f['source_ids'][0],'reason':'Superfície viária com anéis inválidos'});continue
                f['page_holes']=holes;f['page_geometry']=mapping(poly);f['original_page_geometry']=mapping(poly)
                f['quality']['classification']=.6;f['confidence']=.6
                f['issues'].append('Superfície cinza associada à legenda de vias; contorno, interrupções e pavimentação precisam de revisão.');n+=1
        return {'generated':n,'method':'connected_gray_surface','note':'Superfícies reais extraídas; não são eixos viários.'}

    def legend(self):
        s=self.state; proposals=self.model_semantics('Leia a legenda; proponha classes com text_id e symbol_ids existentes. Não crie geometrias.')
        entries=proposals.classes if proposals else []
        for r in s['regions']:
            if r['role']!='legend':continue
            bounds=box(*r['bbox'])
            for t in s['texts']:
                if not bounds.covers(Point(t['anchor'])) or len(t['text'].strip())<3:continue
                name=t['text'].strip()
                if slug(name) in ('legenda','convencoes'):continue
                nearby=[]
                x0,y0,x1,y1=t['bbox']; cy=(y0+y1)/2
                for i,d in enumerate(s['drawings']):
                    b=d['rect']
                    if b.x1<=x0+2 and x0-b.x1<65 and abs((b.y0+b.y1)/2-cy)<max(7,y1-y0) and b.width<65:
                        nearby.append((abs((b.y0+b.y1)/2-cy)+max(0,x0-b.x1)/10,f'v{self.current}_{i}'))
                nearby.sort()
                if nearby:
                    entries.append({'name':name,'text_id':t['id'],'symbol_ids':[nearby[0][1]],'geometry_types':[],
                                    'confidence':.65,'reason':'Texto na região da legenda e símbolo adjacente; associação a revisar.'})
                elif s['raster'] or t['method']=='tesseract_ocr':
                    # A local raster sample left of the legend text is evidence,
                    # not a model-generated symbol or geographic coordinate.
                    with Image.open(self.directory/f'page-{self.current}.png') as preview:
                        kx=preview.width/s['info']['width'];ky=preview.height/s['info']['height']
                        bb=[max(r['bbox'][0],x0-45),max(r['bbox'][1],y0-2),max(r['bbox'][0],x0-3),min(r['bbox'][3],y1+2)]
                        if bb[2]<=bb[0] or bb[3]<=bb[1]:continue
                        sample=np.array(preview.convert('RGB').crop((int(bb[0]*kx),int(bb[1]*ky),int(bb[2]*kx),int(bb[3]*ky))))
                    pixels=sample.reshape(-1,3);ink=pixels[np.min(pixels,axis=1)<210]
                    if len(ink)<4:continue
                    rgb=np.median(ink,axis=0)/255
                    sid=f's{self.current}_{t["id"]}'
                    style={'color':rgb.tolist(),'fill':None,'width':None,'dashes':'unknown','symbol':'raster_swatch','path_type':'raster'}
                    self.source_registry[sid]={'id':sid,'page':self.current,'bbox':bb,'style':style,'method':'legend_raster_sample'}
                    entries.append({'name':name,'text_id':t['id'],'symbol_ids':[sid],'geometry_types':[],
                                    'confidence':.45,'reason':'Amostra raster à esquerda do texto; símbolo e espessura precisam de revisão.'})
        for entry in entries:
            e=entry.model_dump() if hasattr(entry,'model_dump') else entry
            t=self.source_registry.get(e['text_id'])
            if not t or 'text' not in t or t['page']!=self.current:continue
            if not any(r['role']=='legend' and box(*r['bbox']).covers(Point(t['anchor'])) for r in s['regions']):continue
            symbols=[self.source_registry[i] for i in e['symbol_ids'] if i in self.source_registry and self.source_registry[i].get('page')==self.current]
            class_id='legend_'+slug(e['name'])
            styles=[sym['style'] for sym in symbols if 'style' in sym]
            rgb=next((x['color'] for x in styles if x['color']),None)
            color='#'+''.join(f'{max(0,min(255,round(v*255))):02x}' for v in rgb[:3]) if rgb and len(rgb)>=3 else '#617888'
            self.classes[class_id]={'id':class_id,'name':e['name'],'color':color,'styles':styles,
                                    'geometry_types':e['geometry_types'],'evidence':[t,*symbols],
                                    'classification_confidence':e['confidence'],'reason':e['reason'],'status':'proposed'}
        return {'classes':list(self.classes.values())}

    def spatial(self):
        hits=[]
        for t in self.state['texts']:
            if re.search(r'SIRGAS|SAD.?69|WGS.?84|UTM|EPSG|DATUM|MERIDIANO|\b[ENXY]:|COORD|V[ÉE]RT',t['text'],re.I):
                hits.append(t)
        self.state['spatial_evidence']=hits
        native=[t for t in self.state['texts'] if t['method']=='native_text']
        joined=' '.join(t['text'] for t in native)
        crs=None
        match=re.search(r'EPSG\s*[:=]?\s*(\d{4,6})',joined,re.I)
        try:
            if match:crs=CRS.from_epsg(int(match[1]))
            else:
                utm=re.search(r'UTM\s*(?:ZONE|FUSO)?\s*(\d{1,2})\s*([SN])',joined,re.I)
                if utm and re.search(r'SIRGAS\s*2000',joined,re.I):
                    from pyproj.database import query_crs_info
                    name=f'SIRGAS 2000 / UTM zone {int(utm[1])}{utm[2].upper()}'
                    found=next((c for c in query_crs_info(auth_name='EPSG') if c.name==name),None)
                    if found:crs=CRS.from_epsg(found.code)
        except (ValueError,RuntimeError):pass
        if crs and crs.is_projected and abs(crs.axis_info[0].unit_conversion_factor-1)<1e-8:
            self.state['native_crs']=crs.to_string()
        self.state['numeric_coordinate_evidence']=[t for t in self.state['texts'] if re.search(r'\d{3}[.,]?\d{3}',t['text'])]
        # CRS strings are suggestions until backed by metadata or reviewed controls.
        return {'evidence':hits[:100],'native_crs':self.state.get('native_crs'),
                'coordinate_tokens':len(self.state['numeric_coordinate_evidence']),
                'verified_controls_available':bool(self.project['georeferencing'].get(str(self.current))),
                'note':'OCR e interpretação não viram controles finais automaticamente; use revisão da tabela/grade.'}

    def georeference(self):
        s=self.state; valid=[g for g in s['embedded']['viewports'] if g['verified']]
        main=[box(*r['bbox']) for r in s['regions'] if r['role']=='main_map']
        selected=[]
        for g in valid:
            scope=box(*g['scope_bbox'])
            if any(scope.intersection(b).area/max(1,min(scope.area,b.area))>.8 for b in main):selected.append(g)
        if len(selected)==1:
            chosen=copy.deepcopy(selected[0]); old=self.project['georeferencing'].get(str(self.current))
            if old:
                test=[c['page'] for c in chosen['controls']]
                other_crs=Transformer.from_crs(old['crs'],chosen['crs'],always_xy=True)
                previous=[other_crs.transform(*p) for p in transform(test,old['matrix'])]
                discrepancy=float(np.max(np.linalg.norm(np.array(previous)-np.array(transform(test,chosen['matrix'])),axis=1)))
                chosen['previous_fit_difference_m']=discrepancy
                if discrepancy>2:self.issues.append(f'Página {self.current}: GeoPDF e ajuste anterior divergem até {discrepancy:.2f} m. GeoPDF mantido; conferir referências.')
            self.project['georeferencing'][str(self.current)]=chosen
            return {'source':'embedded','georeferencing':chosen}
        if len(selected)>1:
            self.issues.append(f'Página {self.current}: múltiplos viewports no mapa principal; escolha uma região antes de ajustar.')
            self.project['georeferencing'].pop(str(self.current),None)
            return {'source':None,'pending':True}
        # Existing reviewed controls and hash-bound transcription profiles remain explicit evidence.
        existing=self.project['georeferencing'].get(str(self.current))
        if existing and existing.get('verified'):return {'source':'existing_controls','georeferencing':existing}
        digest=self.project['sha256']
        recognized=digest in MACUCO_PROFILES or digest in IMAGE_PROFILES or digest==PDF_HASH
        if recognized:
            if digest==PDF_HASH and not (ROOT/'.cache'/f'{digest}-lots-v4.json').exists():
                self.issues.append('Perfil A0 sem cache: execute Extrair feições para a comparação completa; agente genérico continua.')
                return {'source':None,'pending':True}
            if self.baseline is None:self.baseline=analyze(self.source,self.directory)
            g=self.baseline['georeferencing'].get(str(self.current))
            if g:self.project['georeferencing'][str(self.current)]=g
            return {'source':'hash_verified_profile','georeferencing':g}
        # Generic native grid: require long orthogonal vector lines and numeric
        # native labels touching their axes. OCR digits never become controls.
        if s.get('native_crs'):
            main_boxes=[r['bbox'] for r in s['regions'] if r['role']=='main_map']
            if len(main_boxes)==1:
                x0,y0,x1,y1=main_boxes[0];xs={};ys={}
                for t in s['texts']:
                    token=t['text'].strip()
                    if t['method']!='native_text' or not re.fullmatch(r'\d{6,7}',token):continue
                    value=int(token);a,b,c,d=t['bbox'];matches=[]
                    for drawing in s['drawings']:
                        for item in drawing['items']:
                            if item[0]!='l':continue
                            p,q=item[1:];dx=abs(p.x-q.x);dy=abs(p.y-q.y)
                            if value<1000000 and dx<.01 and dy>(y1-y0)*.6 and a-2<=p.x<=c+2 and (abs(b-y0)<25 or abs(d-y1)<25):matches.append(('x',round(p.x,3)))
                            if value>=1000000 and dy<.01 and dx>(x1-x0)*.6 and b-2<=p.y<=d+2 and (abs(a-x0)<50 or abs(c-x1)<50):matches.append(('y',round(p.y,3)))
                    matches=set(matches)
                    if len(matches)==1:
                        axis,pos=next(iter(matches));(xs if axis=='x' else ys)[pos]=(value,t['id'])
                if len(xs)>=2 and len(ys)>=2:
                    controls=[{'page':[x,y],'world':[e,n],'evidence':f'Grade nativa: {ex}, {ny}'} for x,(e,ex) in xs.items() for y,(n,ny) in ys.items()][:100]
                    g=affine(controls)
                    if max(g['residuals'])<=2:
                        g.update(crs=s['native_crs'],controls=controls,verified=True,unit='m',origin='Linhas de grade vetoriais + rótulos nativos',scope_bbox=main_boxes[0],external_accuracy_m=None)
                        self.project['georeferencing'][str(self.current)]=g
                        return {'source':'native_grid','georeferencing':g}
        return {'source':None,'pending':True,'reason':'CRS/controles insuficientes; geometria mantida na página.'}

    def reconstruct(self):
        """Reuse verified source adapters without presenting them as new AI detections."""
        digest=self.project['sha256']
        if digest not in MACUCO_PROFILES and digest not in IMAGE_PROFILES and digest!=PDF_HASH:return {'reused':0}
        existing=[f for f in self.original if f['page']==self.current and f.get('extraction_method','legacy_profile') in ('legacy_profile','verified_profile_adapter')]
        if not existing:
            if self.baseline is None:
                if digest==PDF_HASH and not (ROOT/'.cache'/f'{digest}-lots-v4.json').exists():return {'reused':0,'pending':'Perfil sem cache'}
                self.baseline=analyze(self.source,self.directory)
            existing=self.baseline['features']
        copied=0
        for old in existing:
            if old['page']!=self.current:continue
            # Baseline geometries replace coincident generic polygons, not unrelated candidate types.
            if len(self.generated)>=self.limits.max_features: self.state['truncated']=True;break
            f=normalize_feature(copy.deepcopy(old));f['id']=old['id'] if old in self.original else f'profile_{old["id"]}'
            f['status']='candidate';f['revision']=0;f['extraction_method']='verified_profile_adapter'
            f['attributes']['baseline_id']=old['id'];f['source_ids']=[f'profile:{digest}:{old["id"]}']
            f['quality']['classification']=None;f['quality']['reading']=None
            f['attributes']['legacy_score']=old.get('confidence')
            f['attributes']['legacy_score_note']='Escore do perfil, frequentemente baseado em concordância de área; não é precisão posicional nem confiança calibrada de classificação.'
            self.classes.setdefault(f['category'],{'id':f['category'],'name':f['category'].replace('_',' ').capitalize(),'color':'#0aafc4','evidence':f['evidence']})
            self.generated.append(f);copied+=1
        return {'reused':copied,'method':'verified_profile_adapter','note':'Resultados existentes reutilizados, não deteções novas do modelo.'}

    def annotations(self):
        count=0
        for t in self.state['texts']:
            if count>=1200:self.state['truncated']=True;break
            f=self.add([t['anchor']],'Point',t['method'],t['id'],'anotacao',t['text'],
                       attributes={'text':t['text'],'original_text':t['text'],'orientation':t['orientation'],
                                   'bbox':t['bbox'],'quad':t['quad'],'original_bbox':t['bbox'],
                                   'original_quad':t['quad'],'original_orientation':t['orientation'],
                                   'font':t['font'],'font_size':t['font_size']},quality=t['reading_quality'])
            if f:
                f['object_type']='annotation';f['quality']['classification']=None
                f['evidence'][0]['bbox']=t['bbox'];count+=1
        return {'annotations':count}

    def classify(self):
        claims={}
        for f in self.generated:
            if f['page']==self.current and f.get('quadra') and f.get('lote'):
                claims.setdefault((f['quadra'],f['lote']),[]).append(f)
        for (q,l),fs in claims.items():
            if len(fs)>1:
                for f in fs:
                    f['issues'].append(f'Quadra {q}, lote {l}: identificador reivindicado por {len(fs)} geometrias; confirmar registro/matrícula e vínculo espacial.')
                    f['attributes']['identifier_conflicts']=[other['id'] for other in fs if other['id']!=f['id']]
        adapters=[f for f in self.generated if f['page']==self.current and f['extraction_method']=='verified_profile_adapter' and f['geometry_type']=='Polygon']
        if adapters:
            shapes=[shape(f['page_geometry']) for f in adapters];tree=STRtree(shapes);duplicates=set()
            for f in self.generated:
                if f['page']!=self.current or f['geometry_type']!='Polygon' or f['extraction_method']=='verified_profile_adapter':continue
                p=shape(f['page_geometry'])
                for i in tree.query(p):
                    if p.intersection(shapes[i]).area/max(p.union(shapes[i]).area,1e-9)>.98:
                        duplicates.add(f['id']);break
            self.generated=[f for f in self.generated if f['id'] not in duplicates]
            self.stats['duplicate_candidates_removed']=self.stats.get('duplicate_candidates_removed',0)+len(duplicates)
        count=0; model=self.model_semantics('Classifique somente candidatos com evidências suficientes; use class_id existente. Ambíguos ficam Não identificado.')
        candidates={f['id']:f for f in self.generated if f['page']==self.current}
        if model:
            for c in model.classifications:
                f=candidates.get(c.feature_id)
                if not f or c.class_id not in self.classes or any(i not in self.source_registry for i in c.evidence_ids):continue
                if f['status']!='candidate' or f['extraction_method']=='verified_profile_adapter':continue
                f.update(category=c.class_id,suggested_class=c.class_id,label=self.classes[c.class_id]['name'],confidence=c.confidence)
                f['quality']['classification']=c.confidence
                f['evidence'].append(evidence(c.class_id,self.classes[c.class_id]['name'],f['evidence'][0]['bbox'],c.reason,c.confidence,self.current,'model_classification'))
                f['attributes']['classification_evidence_ids']=c.evidence_ids;count+=1
        for f in candidates.values():
            if f['category']!='nao_identificado':continue
            style=f['attributes'].get('style');matches=[]
            if not style:continue
            for cls in self.classes.values():
                for sample in cls.get('styles',[]):
                    if not sample.get('color') or not style.get('color'):continue
                    # Black strokes are shared by text, tables and boundaries: insufficient alone.
                    rgb=np.array(sample['color']);observed=np.array(style['color'])
                    if len(rgb)!=len(observed) or np.max(rgb)-np.min(rgb)<.12:continue
                    if sample.get('width') is None or style.get('width') is None:continue
                    if np.linalg.norm(rgb-observed)<.08 and abs(sample['width']-style['width'])<.25 and sample['dashes']==style['dashes']:
                        matches.append(cls)
            unique={c['id']:c for c in matches}
            if len(unique)==1:
                cls=next(iter(unique.values()));f.update(category=cls['id'],suggested_class=cls['id'],label=cls['name'],confidence=.6)
                f['quality']['classification']=.6;f['attributes']['legend_match']={'color':True,'width':True,'dashes':True,'symbol':False}
                f['issues'].append('Classe sugerida por estilo da legenda; conferir símbolo e contexto.');count+=1
        return {'classified':count,'unidentified':sum(f['category']=='nao_identificado' for f in candidates.values())}

    def associate(self):
        fs=[f for f in self.generated if f['page']==self.current and f['object_type']!='annotation']
        polys=[f for f in fs if f['geometry_type']=='Polygon'];geoms=[shape(f['page_geometry']) for f in polys]
        tree=STRtree(geoms) if geoms else None; linked=ambiguous=0
        for text in (f for f in self.generated if f['page']==self.current and f['object_type']=='annotation'):
            p=Point(text['page_ring'][0]); matches=[]
            if tree is not None:
                matches=[polys[i] for i in tree.query(p) if geoms[i].covers(p) and polys[i]['category']!='perimetro']
            if len(matches)==1:
                text['linked_feature_id']=matches[0]['id'];text['attributes']['association_method']='unique_containment';linked+=1
            elif matches:
                text['attributes']['link_candidates']=[f['id'] for f in matches[:20]]
                text['issues'].append('Texto contido em mais de uma feição; vínculo ambíguo.');ambiguous+=1
            else:
                nearby=sorted([(shape(f['page_geometry']).distance(p),f['id']) for f in fs if f['geometry_type']!='Polygon'])[:3]
                if nearby and nearby[0][0]<20:
                    text['attributes']['link_candidates']=[fid for dist,fid in nearby if dist<20]
                    text['issues'].append('Vínculo por proximidade precisa de confirmação.');ambiguous+=1
        return {'linked':linked,'ambiguous':ambiguous}

    def validate(self):
        g=self.project['georeferencing'].get(str(self.current)); invalid=0; projected=0
        for f in [f for f in self.generated if f['page']==self.current]:
            self.checkpoint()
            pg=shape(f['page_geometry'])
            if not pg.is_valid or pg.is_empty:
                f['issues'].append('Geometria inválida; exportação bloqueada.');invalid+=1;continue
            if g and g.get('verified') and (not g.get('scope_bbox') or box(*g['scope_bbox']).buffer(.01).covers(pg)):
                try:
                    world=transform(f['page_ring'],g['matrix']);world_holes=[transform(h,g['matrix']) for h in f.get('page_holes',[])]
                    f.update(reconstruct(world,g['crs'],f['geometry_type'],world_holes))
                    f.update(world_ring=world,source_crs=g['crs'])
                    f['issues']=[i for i in f['issues'] if i!='Sem georreferenciamento comprovado.']
                    f['quality']['georeferencing']={'rmse_m':g['rmse'],'external_accuracy_m':None,'method':g['origin']}
                    if f['object_type']=='annotation':
                        f['attributes']['world_quad']=transform(f['attributes']['quad'],g['matrix'])
                    projected+=1
                except ValueError as exc:f.update(geometry=None,area=None,perimeter=None);f['issues'].append(str(exc))
            else:
                f.update(geometry=None,area=None,perimeter=None)
                if 'Sem georreferenciamento comprovado.' not in f['issues']:f['issues'].append('Sem georreferenciamento comprovado.')
            if f['category']=='nao_identificado':f['issues'].append('Sem evidência suficiente para identificar a classe.')
        return {'invalid':invalid,'georeferenced':projected,'external_accuracy_m':None}

    def available(self):
        done=self.done[self.current];s=self.state
        dependencies={'embedded':set(),'native_text':set(),'ocr':{'native_text'},
                      'regions':{'embedded','native_text'}|({'ocr'} if s['needs_ocr'] else set()),
                      'vectors':{'regions'},'raster':{'regions','vectors'},'legend':{'regions','native_text'},
                      'spatial':{'native_text','embedded'}|({'ocr'} if s['needs_ocr'] else set()),
                      'georeference':{'regions','spatial','embedded'},'reconstruct':{'georeference'},
                      'annotations':{'regions','native_text'}|({'ocr'} if s['needs_ocr'] else set()),
                      'roads':{'legend','regions'},
                      'classify':{'legend','vectors','reconstruct','annotations','roads'}|({'raster'} if s['raster'] else set()),
                      'associate':{'classify'},'validate':{'associate','georeference'}}
        return [name for name,deps in dependencies.items() if name not in done and deps<=done and
                (name!='ocr' or s['needs_ocr']) and (name!='raster' or s['raster'])]
