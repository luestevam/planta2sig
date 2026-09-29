import io
import json
import zipfile
from pathlib import Path
from collections import Counter
import pytest
import shapefile
from fastapi.testclient import TestClient
from shapely.geometry import Polygon
from shapely.ops import unary_union
from backend import main
from backend.extraction import analyze
from backend.exporting import export

EXAMPLES = Path(__file__).resolve().parents[1] / 'exemplos_plantas'


@pytest.mark.parametrize('name,points,lots,lines,area', [
    ('macuco_divisao1.PDF', 29, 3, 31, 482230.921),
    ('macuco_divisão2.PDF', 36, 13, 48, 741984.719),
])
def test_real_surveys(tmp_path, name, points, lots, lines, area):
    p = analyze(EXAMPLES/name, tmp_path)
    counts = Counter(f['category'] for f in p['features'])
    assert counts == dict(ponto=points, lote=lots, limite=lines, perimetro=1, hidrografia=1)
    assert p['pages'][0]['text_count'] == 0
    assert p['georeferencing']['1']['crs'] == 'EPSG:31984'
    assert max(p['georeferencing']['1']['residuals']) < .001
    boundary = next(f for f in p['features'] if f['category']=='perimetro')
    assert boundary['area'] == pytest.approx(area, abs=.1)
    divisions = [Polygon(f['world_ring']) for f in p['features'] if f['category']=='lote']
    assert sum(g.area for g in divisions) == pytest.approx(unary_union(divisions).area, abs=.1)
    assert sum(g.area for g in divisions) == pytest.approx(area, abs=.1)
    assert all(f['geometry'] and f['status']=='candidate' for f in p['features'])
    with pytest.raises(ValueError, match='aceita'):
        export(p, 'geojson')
    for f in p['features']: f['status']='accepted'
    gj = json.loads(export(p, 'geojson')[0])
    assert {f['geometry']['type'] for f in gj['features']} == {'Point','LineString','Polygon'}
    kml = export(p, 'kml')[0]
    assert b'<Point>' in kml and b'<LineString>' in kml and b'<Polygon>' in kml
    z = zipfile.ZipFile(io.BytesIO(export(p, 'shp')[0]))
    for layer, shape_type, count in [('ponto',1,points),('limite',3,lines),('lote',5,lots)]:
        reader = shapefile.Reader(**{ext:io.BytesIO(z.read(f'{layer}.{ext}')) for ext in ['shp','shx','dbf']})
        assert reader.shapeType == shape_type and len(reader)==count


def test_upload_review_correct_and_reproject_mixed_geometries(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    client = TestClient(main.app)
    response = client.post('/api/upload', files={'file':('renamed.PDF',(EXAMPLES/'macuco_divisao1.PDF').read_bytes(),'application/pdf')})
    assert response.status_code==200
    pid=response.json()['id']; url=f'/api/projects/{pid}'
    response=client.post(url+'/extract'); assert response.status_code==200
    p=response.json()
    for category in ['ponto','limite','hidrografia']:
        f=next(f for f in p['features'] if f['category']==category)
        review=url+f"/features/{f['id']}/review"
        ring=f['page_ring']; ring[0][0]+=.01
        r=client.post(review,json={'action':'correct','revision':0,'note':'Teste de correção','page_ring':ring})
        assert r.status_code==200, r.text
        r=client.post(review,json={'action':'accept','revision':1,'acknowledge':True})
        assert r.status_code==200, r.text
    assert client.get(url+'/export/geojson').status_code==200
    g=p['georeferencing']['1']
    r=client.post(url+'/georeference', json={'page':1,'crs':g['crs'],'crs_evidence':g['origin'],'controls':g['controls']})
    assert r.status_code==200, r.text
    assert all(f['status']=='candidate' and f['geometry'] for f in r.json()['features'])
    assert client.get(url+'/export/geojson').status_code==422


def test_bad_transcription_is_blocked(tmp_path, monkeypatch):
    from backend.macuco import PROFILES
    profile=next(iter(PROFILES.values()))
    monkeypatch.setitem(profile,'ne',profile['ne'].replace('401643.4369','401743.4369'))
    with pytest.raises(ValueError,match='resíduo'):
        analyze(EXAMPLES/'macuco_divisao1.PDF',tmp_path)
