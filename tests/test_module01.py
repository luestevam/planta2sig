import io, json, os, tempfile, zipfile
from pathlib import Path
import pytest

os.environ['GEODOC_DATA'] = tempfile.mkdtemp()
from fastapi.testclient import TestClient
from backend import geometry as g
from backend.main import app

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / 'exemplos_plantas'
SQUARE = [[238000, 9363000], [238100, 9363000], [238100, 9363100], [238000, 9363100]]


def test_polygon_reconstruction_from_vertex_table():
    r = g.reconstruct(SQUARE, 'EPSG:31983')
    assert r['area'] == pytest.approx(10000, rel=1e-9)
    assert r['perimeter'] == pytest.approx(400, rel=1e-9)
    ring = r['geometry']['coordinates'][0]
    assert ring[0] == ring[-1] and len(ring) == 5
    assert all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in ring)


def test_missing_crs_keeps_page_coordinates():
    with pytest.raises(ValueError, match='CRS ausente'):
        g.reconstruct(SQUARE, None)


def test_invalid_geometry_rejected():
    bowtie = [[0, 0], [10, 10], [10, 0], [0, 10]]
    with pytest.raises(ValueError, match='inválida'):
        g.polygon(bowtie)
    with pytest.raises(ValueError):
        g.polygon([[0, 0], [1, 1]])
    with pytest.raises(ValueError):
        g.polygon([[0, 0], [1, float('nan')], [2, 2]])


def test_area_divergence_detected():
    assert g.area_check(10000, 10100)['ok'] is True
    bad = g.area_check(10000, 8000)
    assert bad['ok'] is False and bad['difference_percent'] == pytest.approx(25)
    assert g.area_check(10, None)['ok'] is None


def test_affine_residuals_and_degenerate_controls():
    ctl = [{'page': p, 'world': [p[0] * 2 + 100, -p[1] * 2 + 500]} for p in [[0, 0], [10, 0], [10, 10], [0, 10]]]
    t = g.affine(ctl)
    assert t['rmse'] < 1e-9
    ctl[2]['world'][0] += 3  # one wrong control produces measurable residual
    assert g.affine(ctl)['rmse'] > .1
    with pytest.raises(ValueError):
        g.affine(ctl[:3])
    with pytest.raises(ValueError):
        g.affine([{'page': [i, i], 'world': [i, i]} for i in range(4)])


@pytest.fixture(scope='module')
def client():
    return TestClient(app, base_url='http://127.0.0.1:8000')


def test_end_to_end_jpg_example(client):
    p = client.post('/api/examples', json={'name': 'Exmplo01.jpg'}).json()
    assert p['stage'] == 'extracted' and len(p['features']) == 1
    f = p['features'][0]
    assert f['status'] == 'candidate' and f['geometry'] and f['area_check']['ok'] is True
    pid, fid = p['id'], f['id']
    # nothing accepted -> nothing exported
    assert client.get(f'/api/projects/{pid}/export/geojson').status_code == 422
    # stale revision refused
    assert client.post(f'/api/projects/{pid}/features/{fid}/review', json={'action': 'reject', 'revision': 99}).status_code == 409
    # issues require acknowledgment
    assert client.post(f'/api/projects/{pid}/features/{fid}/review', json={'action': 'accept', 'revision': 0}).status_code == 422
    r = client.post(f'/api/projects/{pid}/features/{fid}/review', json={'action': 'accept', 'revision': 0, 'acknowledge': True})
    assert r.json()['features'][0]['status'] == 'accepted'
    gj = client.get(f'/api/projects/{pid}/export/geojson')
    assert gj.status_code == 200 and len(gj.json()['features']) == 1
    assert b'<kml' in client.get(f'/api/projects/{pid}/export/kml').content
    z = zipfile.ZipFile(io.BytesIO(client.get(f'/api/projects/{pid}/export/shp').content))
    assert {'perimetro.shp', 'perimetro.shx', 'perimetro.dbf', 'perimetro.prj'} <= set(z.namelist())


def test_correction_updates_both_representations_and_logs(client):
    p = client.post('/api/examples', json={'name': 'Exmplo01.jpg'}).json()
    f = p['features'][0]
    ring = f['page_ring'][:-1]
    ring[0] = [ring[0][0] + 5, ring[0][1]]
    r = client.post(f"/api/projects/{p['id']}/features/{f['id']}/review",
                    json={'action': 'correct', 'revision': f['revision'], 'page_ring': ring, 'note': 'vértice ajustado'}).json()
    nf = r['features'][0]
    assert nf['page_ring'][0] == ring[0] and nf['geometry'] != f['geometry'] and nf['revision'] == 1
    assert r['history'][-1]['action'] == 'correct' and r['history'][-1]['note'] == 'vértice ajustado'
    assert client.post(f"/api/projects/{p['id']}/features/{f['id']}/review",
                       json={'action': 'correct', 'revision': 1, 'page_ring': [[0, 0], [1e9, 1], [2, 2]], 'note': 'x'}).status_code == 422


def test_area_table_is_rebuilt_from_the_pdf():
    import pymupdf as fitz
    from backend.pdf_lots import area_table
    with fitz.open(EX / 'Projeto Urbanistico (A0)_877 (1).pdf') as d:
        t = area_table(d[0])
    assert len(t) == 493
    assert t[('01', '01')] == (470.73, 197.57) and t[('10', '63')] == (94.13, 94.13)


def test_pdf_example_lots_are_validated_against_the_area_table(client):
    p = client.post('/api/examples', json={'name': 'Projeto Urbanistico (A0)_877 (1).pdf'}, timeout=900).json()
    lots = [f for f in p['features'] if f['category'] == 'lote']
    assert 400 <= len(lots) <= 493                       # extraction coverage, not the declared count
    assert p['diagnostic']['declared_lots'] == 493 and p['georeferencing']['1']['crs'] == 'EPSG:31984'
    good = [f for f in lots if f['confidence'] >= .75]
    assert len(good) >= 400                              # confirmed: polygon area within 5% of the printed table
    assert all(f['area_check']['ok'] for f in good)
    weak = [f for f in lots if f['confidence'] < .75]
    alt = [f for f in lots if .75 <= f['confidence'] < .9]
    assert alt and all(any('alternativo' in i for i in f['issues']) for f in alt)   # non-default outline is disclosed
    assert all(f['issues'] for f in weak)                # nothing unconfirmed goes without a pending note
    assert all(f['status'] == 'candidate' for f in p['features'])
    per = [f for f in p['features'] if f['category'] == 'perimetro'][0]
    assert per['area_check']['ok'] is True and per['area_check']['difference_percent'] < 2   # mosaic outline reproduces the stamp
    assert per['perimeter'] == pytest.approx(per['declared_perimeter'], rel=.02)


def test_upload_without_georef_stays_in_page_coordinates_and_rejects_bad_georef(client):
    img = (EX / 'Exmeplo02.jpg').read_bytes()
    p = client.post('/api/upload', files={'file': ('planta-nova.jpg', img, 'image/jpeg')}).json()
    assert p['stage'] == 'diagnosed' and p['georeferencing'] == {}
    ok = client.post(f"/api/projects/{p['id']}/features", json={'page': 1, 'label': 'Manual', 'category': 'lote', 'note': 'teste',
                                                                'page_ring': [[10, 10], [100, 10], [100, 100], [10, 100]]}).json()
    f = ok['features'][0]
    assert f['geometry'] is None and 'Sem georreferenciamento comprovado.' in f['issues']
    bad = [{'page': [i * 10, i * 10], 'world': [i, i], 'evidence': 'x'} for i in range(4)]
    r = client.post(f"/api/projects/{p['id']}/georeference", json={'page': 1, 'crs': 'EPSG:31984', 'controls': bad, 'crs_evidence': 'x'})
    assert r.status_code == 422
    assert client.post('/api/upload', files={'file': ('x.txt', b'oi', 'text/plain')}).status_code == 422


def test_ai_requires_backend_key(client, monkeypatch):
    monkeypatch.delenv('AI_API_KEY', raising=False)
    p = client.post('/api/examples', json={'name': 'Exmplo01.jpg'}).json()
    r = client.post(f"/api/projects/{p['id']}/ai/1")
    assert r.status_code == 422 and 'chave' in r.json()['detail']
    assert client.get('/api/config').json()['ai_configured'] is False


def test_render_clip_is_sharper_than_stored_page(client):
    p = client.post('/api/examples', json={'name': 'Projeto Urbanistico (A0)_877 (1).pdf'}, timeout=900).json()
    r = client.get(f"/api/projects/{p['id']}/render/1.png", params=dict(x0=700, y0=440, x1=800, y1=500, w=700))
    assert r.status_code == 200 and r.headers['content-type'] == 'image/png'
    from PIL import Image
    assert Image.open(io.BytesIO(r.content)).width == 700       # 7 px per PDF point, vs 1 px in the stored page image
    assert client.get(f"/api/projects/{p['id']}/render/1.png", params=dict(x0=5, y0=5, x1=5.2, y1=6)).status_code == 422
