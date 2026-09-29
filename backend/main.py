import copy,json,os,uuid,threading
from pathlib import Path
from datetime import datetime,timezone
from typing import Literal
import pymupdf as fitz
from PIL import Image
from fastapi import FastAPI,HTTPException,UploadFile,File,Request
from fastapi.responses import FileResponse,Response,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from . import agent
from .extraction import analyze,feature,evidence
from .geometry import polygon,page_geometry,reconstruct,affine,transform,area_check
from .exporting import export
from .agent_schema import AgentRequest, Limits, normalize_feature
from . import agent_jobs
from .document_tools import slug

ROOT=Path(__file__).resolve().parents[1]
DATA=Path(os.getenv('GEODOC_DATA',str(ROOT/'data')))
DATA.mkdir(parents=True,exist_ok=True)
LOCK=threading.RLock()
app=FastAPI(title='GeoDoc SIG • Módulo 01')


@app.middleware('http')
async def origin_guard(request:Request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/') and origin not in ('http://127.0.0.1:8000','http://localhost:8000','http://127.0.0.1:5173','http://localhost:5173'):
            return JSONResponse({'detail':'Origem não permitida.'},status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error(request,exc): return JSONResponse({'detail':str(exc)},status_code=422)


def directory(pid):
    try: uuid.UUID(pid)
    except ValueError: raise HTTPException(404,'Projeto não encontrado.')
    d=DATA/pid
    if not d.is_dir(): raise HTTPException(404,'Projeto não encontrado.')
    return d


def read(pid):
    d=directory(pid)
    p=json.loads((d/'project.json').read_text(encoding='utf8'))
    for f in p['features']: normalize_feature(f)
    return p


def save(p):
    for f in p['features']:normalize_feature(f)
    d=directory(p['id']); target=d/'project.json'; tmp=d/'project.tmp'
    tmp.write_text(json.dumps(p,ensure_ascii=False,allow_nan=False),encoding='utf8'); tmp.replace(target)


def log(p,action,before,after,note=''):
    p['history'].append({'time':datetime.now(timezone.utc).isoformat(),'action':action,'before':before,'after':after,'note':note})


def create(source,name=None,extract=False):
    pid=str(uuid.uuid4()); d=DATA/pid; d.mkdir()
    path=d/(name or source.name)
    if path.suffix.lower() not in ['.pdf','.jpg','.jpeg','.png']: raise ValueError('Envie PDF, JPG ou PNG.')
    path.write_bytes(source.read_bytes())
    if extract:
        p=analyze(path,d); p['stage']='extracted'
    else:
        pages=[]
        if path.suffix.lower()=='.pdf':
            with fitz.open(path) as doc:
                if not 1<=len(doc)<=20: raise ValueError('Envie entre 1 e 20 páginas.')
                for i,page in enumerate(doc):
                    pix=page.get_pixmap(matrix=fitz.Matrix(min(1,3600/max(page.rect.width,page.rect.height)),)*2) if False else page.get_pixmap(matrix=fitz.Matrix(*( [min(1,3600/max(page.rect.width,page.rect.height))]*2)),alpha=False)
                    pix.save(d/f'page-{i+1}.png')
                    pages.append({'number':i+1,'width':page.rect.width,'height':page.rect.height,'text_count':len(page.get_text('words')),'path_count':len(page.get_drawings()),'image_count':len(page.get_images())})
        else:
            with Image.open(path) as im:
                if im.width*im.height>60_000_000: raise ValueError('Imagem excede 60 megapixels.')
                im.convert('RGB').save(d/'page-1.png')
                pages=[{'number':1,'width':im.width,'height':im.height,'text_count':0,'path_count':0,'image_count':1}]
        import hashlib
        p=dict(name=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),pages=pages,stage='diagnosed',features=[],evidence=[],history=[],georeferencing={},diagnostic={},issues=['Diagnóstico concluído. Clique em Extrair para procurar feições candidatas.'],rejected_candidates=[])
    p['id']=pid; save(p); return p


@app.get('/api/config')
def config():
    limits=Limits.environment()
    return {'ai_configured':agent.configured(),'formats':['geojson','kml','shp'],'version':'2.0',
            'agent':{'provider':os.getenv('AI_PROVIDER','openai-compatible'),'model':os.getenv('AI_MODEL',''),
                     'limits':limits.model_dump(),'local_available':True,
                     'disclosure':'Modo IA: serão enviados ao provedor configurado prévias das páginas selecionadas, trechos de texto/OCR, caixas, estilos, evidências, resumos de feições e exemplos revisados deste projeto. Modo local: nenhum conteúdo sai deste computador.',
                     'cost_ready':bool(limits.input_usd_per_million and limits.output_usd_per_million)}}


@app.get('/api/examples')
def examples(): return [{'name':p.name} for p in sorted((ROOT/'exemplos_plantas').iterdir()) if p.suffix.lower() in ['.pdf','.jpg','.png']]


class Example(BaseModel): name:str
@app.post('/api/examples')
def open_example(body:Example):
    files={p.name:p for p in (ROOT/'exemplos_plantas').iterdir() if p.suffix.lower() in ['.pdf','.jpg','.png']}
    if body.name not in files: raise HTTPException(404,'Exemplo não encontrado.')
    return create(files[body.name],extract=True)


@app.post('/api/upload')
async def upload(file:UploadFile=File(...)):
    name=Path((file.filename or 'planta').replace('\\','/')).name
    if name in ['project.json','project.tmp'] or len(name)>180: raise ValueError('Nome inválido.')
    blob=await file.read(50*1024*1024+1)
    if len(blob)>50*1024*1024: raise ValueError('Limite de upload: 50 MB.')
    tmp=DATA/(str(uuid.uuid4())+Path(name).suffix)
    try:
        tmp.write_bytes(blob)
        return create(tmp,name)
    except (fitz.FileDataError,Image.UnidentifiedImageError):
        raise ValueError('Arquivo inválido ou corrompido.')
    finally: tmp.unlink(missing_ok=True)


@app.get('/api/projects/{pid}')
def project(pid:str): return read(pid)


@app.post('/api/projects/{pid}/extract')
def extract_project(pid:str):
    with LOCK:
        old=read(pid)
        if old['history'] or old['stage']=='extracted': raise ValueError('Extração já realizada. Abra outra cópia para reiniciar sem perder revisões.')
        p=analyze(directory(pid)/old['name'],directory(pid)); p.update(id=pid,stage='extracted'); save(p); return p


@app.get('/api/projects/{pid}/pages/{page}.png')
def page_image(pid:str,page:int):
    if page<1 or page>20: raise HTTPException(404)
    path=directory(pid)/f'page-{page}.png'
    if not path.exists(): raise HTTPException(404)
    return FileResponse(path)


@app.get('/api/projects/{pid}/render/{page}.png')
def render_clip(pid:str,page:int,x0:float,y0:float,x1:float,y1:float,w:int=1600):
    """Sharp crop of a PDF page for the current zoom window (the stored page PNG is only 1 px per point)."""
    p=read(pid); src=directory(pid)/p['name']
    info=next((x for x in p['pages'] if x['number']==page),None)
    if src.suffix.lower()!='.pdf' or not info: raise HTTPException(404)
    clip=fitz.Rect(max(0,x0),max(0,y0),min(info['width'],x1),min(info['height'],y1))
    if clip.width<1 or clip.height<1: raise ValueError('Janela de recorte inválida.')
    zoom=min(max(200,min(w,2400))/clip.width,8)
    with fitz.open(src) as doc: pix=doc[page-1].get_pixmap(matrix=fitz.Matrix(zoom,zoom),clip=clip,alpha=False)
    return Response(pix.tobytes('png'),media_type='image/png',headers={'Cache-Control':'private, max-age=600'})


@app.get('/api/projects/{pid}/source/{kind}/{page}')
def raw_source(pid:str,kind:Literal['text','vectors'],page:int):
    path=directory(pid)/f'{kind}-{page}.json'
    if not path.exists(): raise HTTPException(404)
    return FileResponse(path,media_type='application/json')


class Review(BaseModel):
    action:Literal['accept','reject','correct']
    revision:int
    page_ring:list[list[float]]|None=None
    label:str|None=Field(default=None,max_length=180)
    category:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9_\-]{1,80}$')
    class_name:str|None=Field(default=None,min_length=1,max_length=100)
    text:str|None=Field(default=None,max_length=5000)
    linked_feature_id:str|None=Field(default=None,max_length=100)
    text_bbox:list[float]|None=Field(default=None,min_length=4,max_length=4)
    orientation:float|None=Field(default=None,ge=-360,le=360,allow_inf_nan=False)
    note:str=Field(default='',max_length=2000)
    acknowledge:bool=False


@app.post('/api/projects/{pid}/features/{fid}/review')
def review(pid:str,fid:str,b:Review):
    with LOCK:
        p=read(pid); f=next((f for f in p['features'] if f['id']==fid),None)
        if not f: raise HTTPException(404)
        if b.revision!=f['revision']: raise HTTPException(409,'Feição alterada: recarregue o projeto.')
        before=copy.deepcopy(f)
        if b.action=='correct':
            if not b.note.strip(): raise ValueError('Descreva a correção para o histórico.')
            if b.page_ring is not None:
                ring=check_page_ring(p,f['page'],b.page_ring,f.get('geometry_type','Polygon'))
                f['page_ring']=ring
                from shapely.geometry import mapping
                from shapely.geometry import Polygon
                edited=Polygon(ring,f.get('page_holes',[])) if f.get('geometry_type','Polygon')=='Polygon' else page_geometry(ring,f['geometry_type'])
                if not edited.is_valid:raise ValueError('Geometria corrigida incompatível com anéis internos.')
                f['page_geometry']=mapping(edited)
                geo=p['georeferencing'].get(str(f['page']))
                f.update(geometry=None,area=None,perimeter=None)
                if geo and geo['verified']:
                    world=transform(ring,geo['matrix']);holes=[transform(h,geo['matrix']) for h in f.get('page_holes',[])]
                    f.update(reconstruct(world,geo['crs'],f.get('geometry_type','Polygon'),holes),world_ring=world,source_crs=geo['crs'])
                    f['area_check']=area_check(f['area'],f.get('declared_area'))
                f['issues']=['Geometria corrigida manualmente; revisar antes de aceitar.']
                if f.get('area_check',{}).get('ok') is False: f['issues'].append('Divergência de área superior a 3%.')
            if b.label: f['label']=b.label
            if b.category: f['category']=b.category
            if b.class_name:
                cid='custom_'+slug(b.class_name)
                p.setdefault('classes',[])
                if not any(c['id']==cid for c in p['classes']):p['classes'].append({'id':cid,'name':b.class_name,'color':'#617888','evidence':[],'status':'reviewed'})
                f['category']=cid
            if b.text is not None or b.text_bbox is not None or b.orientation is not None:
                if f.get('object_type')!='annotation':raise ValueError('Texto, caixa e orientação editáveis somente em anotações.')
                attrs=f['attributes']
                if b.text is not None:attrs['text']=b.text;f['label']=b.text[:180]
                if b.text_bbox is not None:
                    info=next(x for x in p['pages'] if x['number']==f['page'])
                    x0,y0,x1,y1=b.text_bbox
                    if not 0<=x0<x1<=info['width'] or not 0<=y0<y1<=info['height']:raise ValueError('Caixa do texto fora da página.')
                    attrs['bbox']=b.text_bbox;attrs['quad']=[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
                if b.orientation is not None:attrs['orientation']=b.orientation
            if 'linked_feature_id' in b.model_fields_set:
                target=next((x for x in p['features'] if x['id']==b.linked_feature_id),None)
                if b.linked_feature_id and (not target or target['id']==fid or target['page']!=f['page']):raise ValueError('Vínculo deve apontar para outra feição da mesma página.')
                f['linked_feature_id']=b.linked_feature_id
            if f.get('object_type')=='annotation':
                attrs=f['attributes']
                if b.page_ring is not None and b.text_bbox is None:
                    dx=f['page_ring'][0][0]-before['page_ring'][0][0];dy=f['page_ring'][0][1]-before['page_ring'][0][1]
                    attrs['quad']=[[x+dx,y+dy] for x,y in attrs['quad']]
                    x0,y0,x1,y1=attrs['bbox'];attrs['bbox']=[x0+dx,y0+dy,x1+dx,y1+dy]
                    info=next(x for x in p['pages'] if x['number']==f['page'])
                    if not all(0<=x<=info['width'] and 0<=y<=info['height'] for x,y in attrs['quad']):raise ValueError('Caixa do texto fora da página.')
                geo=p['georeferencing'].get(str(f['page']))
                if geo and geo.get('verified'):attrs['world_quad']=transform(attrs['quad'],geo['matrix'])
            if b.category or b.class_name:
                f['quality']['classification']=None;f['attributes']['classification_method']='reviewer'
            f['status']='candidate'
        elif b.action=='accept':
            page_geometry(f['page_ring'],f.get('geometry_type','Polygon'))
            if f['issues'] and not b.acknowledge: raise ValueError('Confira as pendências e marque a confirmação de revisão.')
            f['status']='accepted'
        else: f['status']='rejected'
        f['revision']+=1
        if b.action in ('correct','accept'):
            p.setdefault('reviewed_examples',[]).append({'feature_id':fid,'revision':f['revision'],'action':b.action,
                'page':f['page'],'class_id':f['category'],'text':f.get('attributes',{}).get('text'),
                'linked_feature_id':f.get('linked_feature_id'),'geometry':f['page_geometry'],
                'style':f.get('attributes',{}).get('style'),'note':b.note,'use':'Exemplo revisado do projeto; não é treinamento do modelo.'})
        log(p,b.action,before,copy.deepcopy(f),b.note); save(p); return p


def check_page_ring(p,page,ring,geometry_type='Polygon'):
    pageinfo=next((x for x in p['pages'] if x['number']==page),None)
    if not pageinfo: raise ValueError('Página inexistente.')
    geom=page_geometry(ring,geometry_type)
    r=list(map(list,geom.exterior.coords if geometry_type=='Polygon' else geom.coords))
    if any(not 0<=x<=pageinfo['width'] or not 0<=y<=pageinfo['height'] for x,y in r): raise ValueError('Vértice fora da página.')
    return r


class Manual(BaseModel):
    page:int=Field(ge=1,le=20)
    page_ring:list[list[float]]
    label:str=Field(min_length=1,max_length=180)
    category:str=Field(pattern=r'^[a-zA-Z0-9_\-]{1,80}$')
    geometry_type:Literal['Polygon','Point','LineString']='Polygon'
    note:str=Field(min_length=1,max_length=2000)


@app.post('/api/projects/{pid}/features')
def manual(pid:str,b:Manual):
    with LOCK:
        p=read(pid); ring=check_page_ring(p,b.page,b.page_ring,b.geometry_type)
        ev=evidence(b.category,b.label,page_geometry(ring,b.geometry_type).bounds,b.note,1.,b.page,'Vetorização manual')
        geo=p['georeferencing'].get(str(b.page))
        f=feature(ring,b.category,b.label,ev,geo['crs'] if geo else None,transform(ring,geo['matrix']) if geo and geo['verified'] else None,geometry_type=b.geometry_type)
        f['id']='f'+uuid.uuid4().hex[:10]; p['features'].append(f)
        log(p,'manual',None,copy.deepcopy(f),b.note); save(p); return p


class Control(BaseModel):
    page:list[float]=Field(min_length=2,max_length=2)
    world:list[float]=Field(min_length=2,max_length=2)
    evidence:str=Field(min_length=1,max_length=500)
class Georeference(BaseModel):
    page:int=Field(ge=1,le=20)
    crs:str
    controls:list[Control]=Field(min_length=4,max_length=100)
    max_rmse:float=Field(default=2.,gt=0,le=100)
    crs_evidence:str=Field(min_length=1,max_length=1000)
    override_embedded:bool=False


@app.post('/api/projects/{pid}/georeference')
def georeference(pid:str,b:Georeference):
    from pyproj import CRS
    c=CRS.from_user_input(b.crs)
    if not c.is_projected or abs(c.axis_info[0].unit_conversion_factor-1)>1e-8: raise ValueError('Use CRS projetado em metros para medir resíduos.')
    with LOCK:
        p=read(pid); page=next((x for x in p['pages'] if x['number']==b.page),None)
        if not page: raise ValueError('Página inexistente.')
        source=directory(pid)/p['name']
        if source.suffix.lower()=='.pdf':
            from .embedded_geo import inspect
            with fitz.open(source) as doc:embedded=inspect(doc[b.page-1])
            if any(g['verified'] for g in embedded['viewports']) and not b.override_embedded:
                raise ValueError('GeoPDF utilizável encontrado. Execute o agente para usá-lo ou confirme explicitamente a substituição pelos controles.')
        controls=[c.model_dump() for c in b.controls]
        if any(not 0<=v['page'][0]<=page['width'] or not 0<=v['page'][1]<=page['height'] for v in controls): raise ValueError('Controle fora da página.')
        g=affine(controls)
        if g['rmse']>b.max_rmse: raise ValueError(f"RMSE {g['rmse']:.3f} m excede o limite {b.max_rmse} m. Revise controles.")
        g.update(crs=b.crs,controls=controls,verified=True,unit='m',origin=b.crs_evidence)
        before=copy.deepcopy(p['georeferencing'].get(str(b.page)))
        p['georeferencing'][str(b.page)]=g
        for f in p['features']:
            if f['page']!=b.page: continue
            old=copy.deepcopy(f); world=transform(f['page_ring'],g['matrix'])
            holes=[transform(h,g['matrix']) for h in f.get('page_holes',[])]
            f.update(reconstruct(world,b.crs,f.get('geometry_type','Polygon'),holes),source_crs=b.crs,world_ring=world,status='candidate',revision=f['revision']+1)
            if f.get('object_type')=='annotation':f['attributes']['world_quad']=transform(f['attributes']['quad'],g['matrix'])
            f['quality']['georeferencing']={'rmse_m':g['rmse'],'external_accuracy_m':None,'method':g['origin']}
            f['area_check']=area_check(f['area'],f.get('declared_area'))
            f['issues']=['Georreferenciamento alterado; revisar novamente.']
            if f['area_check']['ok'] is False: f['issues'].append('Divergência de área superior a 3%.')
            log(p,'reproject',old,copy.deepcopy(f))
        log(p,'georeference',before,g,b.crs_evidence); save(p); return p


class Table(BaseModel):
    page:int=Field(ge=1,le=20)
    label:str=Field(min_length=1,max_length=180)
    crs:str
    bbox:list[float]=Field(min_length=4,max_length=4)
    vertices:list[list[float]]=Field(min_length=3,max_length=10000)
    declared_area:float|None=Field(default=None,gt=0)
    evidence:str=Field(min_length=1,max_length=2000)


@app.post('/api/projects/{pid}/table')
def table(pid:str,b:Table):
    import numpy as np
    from pyproj import Transformer
    with LOCK:
        p=read(pid); g=p['georeferencing'].get(str(b.page))
        if not g or not g['verified']: raise ValueError('Adicione controles para conferir o perímetro sobre a planta antes de importar a tabela.')
        reconstruct(b.vertices,b.crs)
        t=Transformer.from_crs(b.crs,g['crs'],always_xy=True)
        world=np.array([t.transform(*v) for v in b.vertices]); m=np.array(g['matrix'])
        ring=((world-m[2])@np.linalg.inv(m[:2])).tolist()
        check_page_ring(p,b.page,ring)
        page=next(x for x in p['pages'] if x['number']==b.page)
        x0,y0,x1,y1=b.bbox
        if not 0<=x0<x1<=page['width'] or not 0<=y0<y1<=page['height']: raise ValueError('Posição da tabela fora da página.')
        ev=evidence('perimetro',b.label,b.bbox,b.evidence,1.,b.page,'Tabela conferida manualmente')
        f=feature(ring,'perimetro',b.label,ev,b.crs,b.vertices,b.declared_area)
        f['id']='f'+uuid.uuid4().hex[:10]; p['features'].append(f)
        log(p,'table',None,copy.deepcopy(f),b.evidence); save(p); return p


@app.post('/api/projects/{pid}/ai/{page}')
async def ai(pid:str,page:int):
    p=read(pid); pg=next((x for x in p['pages'] if x['number']==page),None)
    if not pg: raise HTTPException(404)
    result=await agent.interpret(directory(pid)/f'page-{page}.png',page,pg['width'],pg['height'])
    with LOCK:
        p=read(pid)
        for o in result['observations']: o['origin']='IA • hipótese a conferir'
        p['evidence']+=result['observations']; p['issues']+=result['inconsistencies']
        log(p,'ai',None,result,'Observações apenas; nenhuma coordenada promovida automaticamente.')
        save(p); return p


@app.post('/api/projects/{pid}/agent')
def start_agent(pid:str,request:AgentRequest):
    with LOCK:
        p=read(pid)
        for page_number,regions in request.regions.items():
            info=next((x for x in p['pages'] if str(x['number'])==page_number),None)
            if not info:raise ValueError('Página das regiões inexistente.')
            if regions and not any(r.role=='main_map' for r in regions):raise ValueError('Informe ao menos uma região main_map.')
            for region in regions:
                x0,y0,x1,y1=region.bbox
                if not 0<=x0<x1<=info['width'] or not 0<=y0<y1<=info['height']:raise ValueError('Região fora da página.')
        def commit(result,expected):
            with LOCK:
                current=read(pid)
                if agent_jobs.fingerprint(current)!=expected:return False
                log(result,'agent',None,{'status':result['agent_report']['status'],'tool_calls':result['agent_report']['tool_calls']},'Análise rastreável; nenhuma candidata aceita automaticamente.')
                save(result);return True
        return agent_jobs.start(p,directory(pid),request.model_dump(),commit)


@app.get('/api/projects/{pid}/agent/{jobid}')
def agent_status(pid:str,jobid:str):return agent_jobs.get(directory(pid),jobid)


@app.post('/api/projects/{pid}/agent/{jobid}/cancel')
def cancel_agent(pid:str,jobid:str):
    directory(pid);return agent_jobs.cancel(pid,jobid)


@app.get('/api/projects/{pid}/agent/{jobid}/report')
def job_report(pid:str,jobid:str):
    agent_jobs.get(directory(pid),jobid)
    path=directory(pid)/'agent_jobs'/jobid/'report.json'
    if not path.exists():raise HTTPException(404,'Relatório ainda não disponível.')
    return FileResponse(path,media_type='application/json')


@app.get('/api/projects/{pid}/agent-report')
def agent_report(pid:str):
    p=read(pid)
    if not p.get('agent_report'):raise HTTPException(404,'Nenhuma análise concluída.')
    return p['agent_report']


@app.get('/api/projects/{pid}/agent-source/{kind}/{page}')
def agent_source(pid:str,kind:Literal['text','vectors','regions','embedded'],page:int):
    if not 1<=page<=20:raise HTTPException(404)
    path=directory(pid)/f'agent-{kind}-{page}.json'
    if not path.exists():raise HTTPException(404)
    return FileResponse(path,media_type='application/json')


@app.get('/api/projects/{pid}/export/{fmt}')
def export_project(pid:str,fmt:str,layers:str|None=None,types:str|None=None):
    p=read(pid); data,mime,ext=export(p,fmt,layers.split(',') if layers is not None else None,types.split(',') if types is not None else None)
    return Response(data,media_type=mime,headers={'Content-Disposition':f'attachment; filename="camadas-revisadas.{ext}"'})


app.mount('/',StaticFiles(directory=ROOT/'frontend',html=True),name='frontend')
