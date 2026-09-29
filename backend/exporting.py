import io,json,zipfile,re,hashlib
from xml.sax.saxutils import escape
import shapefile
from pyproj import CRS
from shapely.geometry import shape
from shapely.geometry.polygon import orient


def export(project,fmt,categories=None,types=None):
    from .agent_schema import object_type
    selected=[f for f in project['features'] if f['status']=='accepted' and f['geometry'] and (categories is None or f['category'] in categories) and (types is None or object_type(f) in types)]
    if not selected: raise ValueError('Nenhuma feição aceita e georreferenciada nas camadas selecionadas.')
    for f in selected:
        p=shape(f['geometry'])
        if not p.is_valid or p.is_empty: raise ValueError('Geometria inválida bloqueia a exportação.')
    fc={'type':'FeatureCollection','features':[{'type':'Feature','id':f['id'],'geometry':f['geometry'],
        'properties':{k:f.get(k) for k in ['id','label','category','suggested_class','object_type','page','status','area','perimeter','confidence','quality','evidence','revision','issues','attributes','extraction_method','linked_feature_id','page_geometry','original_page_geometry','source_ids']}} for f in selected]}
    audit={'source':project['name'],'sha256':project['sha256'],'georeferencing':project['georeferencing'],
           'history':project['history'],'feature_ids':[f['id'] for f in selected],
           'classes':project.get('classes',[]),'issues':project.get('issues',[]),
           'excluded':[{'id':f['id'],'status':f['status'],'georeferenced':bool(f['geometry']),'issues':f['issues']} for f in project['features'] if f not in selected],
           'agent_report':project.get('agent_report'),'reviewed_examples':project.get('reviewed_examples',[]),
           'quality_note':'Confiança de leitura/classificação não mede precisão posicional. Erro externo não mensurado permanece nulo.'}
    if fmt=='geojson':
        fc['audit']=audit
        return json.dumps(fc,ensure_ascii=False).encode(),'application/geo+json','geojson'
    if fmt=='kml':
        folders={}
        for f in selected:
            geom=f['geometry']; kind=geom['type']
            def coordinates(points): return ' '.join(f'{x},{y},0' for x,y in points)
            if kind=='Polygon':
                body='<Polygon>'+''.join(f'<{tag}><LinearRing><coordinates>{coordinates(ring)}</coordinates></LinearRing></{tag}>' for tag,ring in [('outerBoundaryIs',geom['coordinates'][0])]+[('innerBoundaryIs',r) for r in geom['coordinates'][1:]])+'</Polygon>'
            else:
                pts=[geom['coordinates']] if kind=='Point' else geom['coordinates']
                body=f'<{kind}><coordinates>{coordinates(pts)}</coordinates></{kind}>'
            props=next(x['properties'] for x in fc['features'] if x['id']==f['id'])
            folder=f['category']+' / '+object_type(f)
            folders.setdefault(folder,[]).append(f'<Placemark><name>{escape(f["label"])}</name><description>{escape(json.dumps(props,ensure_ascii=False))}</description><ExtendedData><Data name="geodoc"><value>{escape(json.dumps(props,ensure_ascii=False))}</value></Data></ExtendedData>{body}</Placemark>')
        kml='<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><description>'+escape(json.dumps({'issues':audit['issues'],'excluded':audit['excluded']},ensure_ascii=False))+'</description>'+''.join('<Folder><name>'+escape(name)+'</name>'+''.join(marks)+'</Folder>' for name,marks in folders.items())+'</Document></kml>'
        return kml.encode(),'application/vnd.google-earth.kml+xml','kml'
    if fmt!='shp': raise ValueError('Formato não suportado.')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        groups=sorted({(f['category'],f['geometry']['type'],object_type(f)) for f in selected})
        for category,kind,otype in groups:
            stem=category if sum(c==category for c,k,t in groups)==1 else category+'_'+otype
            safe=re.sub(r'[^a-zA-Z0-9_-]','_',stem)
            if safe!=stem:safe+='_'+hashlib.sha256(stem.encode()).hexdigest()[:8]
            stem=safe
            shp,shx,dbf=io.BytesIO(),io.BytesIO(),io.BytesIO()
            w=shapefile.Writer(shp=shp,shx=shx,dbf=dbf,shapeType={'Polygon':shapefile.POLYGON,'LineString':shapefile.POLYLINE,'Point':shapefile.POINT}[kind],encoding='utf-8')
            w.field('id','C',100); w.field('label','C',180); w.field('area_m2','N',20,3); w.field('page','N',5,0)
            w.field('class_id','C',80);w.field('obj_type','C',12);w.field('text','C',254);w.field('angle','N',12,4);w.field('linked_id','C',100);w.field('pending','N',5,0)
            for f in selected:
                if f['category']!=category or f['geometry']['type']!=kind or object_type(f)!=otype: continue
                p=shape(f['geometry'])
                if kind=='Polygon':
                    p=orient(p,sign=-1.)
                    w.poly([list(p.exterior.coords)]+[list(r.coords) for r in p.interiors])
                elif kind=='Point': w.point(p.x,p.y)
                else: w.line([list(p.coords)])
                attrs=f.get('attributes',{})
                w.record(f['id'],f['label'],f['area'],f['page'],category,otype,attrs.get('text',''),attrs.get('orientation'),f.get('linked_feature_id') or '',len(f['issues']))
            w.close()
            for ext,b in [('shp',shp),('shx',shx),('dbf',dbf)]: z.writestr(f'{stem}.{ext}',b.getvalue())
            z.writestr(f'{stem}.prj',CRS.from_epsg(4326).to_wkt(version='WKT1_ESRI'))
            z.writestr(f'{stem}.cpg','UTF-8')
        z.writestr('auditoria.json',json.dumps(audit,ensure_ascii=False))
        z.writestr('feicoes.geojson',json.dumps(fc,ensure_ascii=False))
        z.writestr('LEIA-ME.txt','Shapefiles separados por classe e tipo. Anotações usam pontos de ancoragem; caixas, quadriláteros, orientação e texto integral estão em feicoes.geojson. Campos DBF podem truncar textos; consulte o GeoJSON e auditoria.json. Pendências não equivalem a precisão posicional.')
    return output.getvalue(),'application/zip','zip'
