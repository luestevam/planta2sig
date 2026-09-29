"""Contract fixtures are synthetic; real-file checks and demo are identified separately."""
import copy
import io
import json
import time
import zipfile
from pathlib import Path
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer
from backend import main
from backend.agent_schema import Limits, AgentRequest, normalize_feature
from backend.agent_runner import run
from backend.agent_provider import Provider, ProviderUnavailable, SYSTEM
from backend.embedded_geo import inspect
from backend.document_tools import read_text
from backend.exporting import export

ROOT=Path(__file__).resolve().parents[1]


def fixture_pdf(path, embedded=True):
    doc=fitz.open();p=doc.new_page(width=400,height=400)
    p.draw_rect(fitz.Rect(80,80,200,200),color=(0,0,1),width=1)
    p.draw_line((80,240),(230,240),color=(1,0,0),width=2)
    p.draw_circle((260,100),4,color=(0,0,0))
    p.insert_text((100,120),'AREA A',fontsize=12)
    p.insert_text((250,220),'VERTICAL',fontsize=10,rotate=90)
    p.insert_text((300,300),'LEGENDA',fontsize=10)
    p.draw_line((300,315),(320,315),color=(1,0,0),width=2)
    p.insert_text((325,318),'Rede',fontsize=9)
    p.insert_text((10,25),'EPSG:31984',fontsize=9)
    if embedded:
        t=Transformer.from_crs(31984,4674,always_xy=True)
        geographic=[]
        for e,n in [(400000,7900000),(400000,7900400),(400400,7900400),(400400,7900000)]:
            lon,lat=t.transform(e,n);geographic.extend([lat,lon])
        g=doc.get_new_xref();doc.update_object(g,'<< /Type /GEOGCS /EPSG 4674 >>')
        m=doc.get_new_xref();doc.update_object(m,f'<< /Type /Measure /Subtype /GEO /GCS {g} 0 R /LPTS [0 0 0 1 1 1 1 0] /GPTS ['+' '.join(map(str,geographic))+'] >>')
        doc.xref_set_key(p.xref,'VP',f'[<< /Type /Viewport /BBox [0 0 400 400] /Measure {m} 0 R >>]')
    doc.save(path);doc.close()


@pytest.fixture
def tiny_project(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path)
    path=tmp_path/'fixture.pdf';fixture_pdf(path)
    p=main.create(path)
    return p,tmp_path/p['id']


def test_embedded_geopdf_precedes_controls_and_preserves_text(tiny_project):
    p,d=tiny_project
    with fitz.open(d/p['name']) as doc:
        g=inspect(doc[0]);assert g['checked'] and g['viewports'][0]['verified']
        texts=read_text(doc[0]);rotated=next(x for x in texts if x['text']=='VERTICAL')
        assert rotated['orientation']==pytest.approx(-90)
        assert len(rotated['quad'])==4
    r=run(p,d,{'mode':'local'})
    report=r['agent_report']
    assert report['status']=='completed'
    assert not report['model_used']
    assert r['georeferencing']['1']['embedded']
    tools=[t['tool'] for t in report['tools']]
    assert tools.index('embedded')<tools.index('georeference')
    assert {'polygon','line','point','annotation'} <= {f['object_type'] for f in r['features']}
    assert all(f['status']=='candidate' for f in r['features'])
    annotation=next(f for f in r['features'] if f['object_type']=='annotation' and f['label']=='AREA A')
    assert annotation['attributes']['original_text']=='AREA A'
    assert annotation['linked_feature_id']
    assert annotation['quality']['georeferencing']['external_accuracy_m'] is None
    assert report['classification_error_rate'] is None


def test_no_reference_stays_in_page_coordinates(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path)
    source=tmp_path/'no-geo.pdf';fixture_pdf(source,False)
    p=main.create(source);r=run(p,tmp_path/p['id'])
    assert r['features'] and not r['georeferencing']
    assert all(f['geometry'] is None for f in r['features'])
    assert any(f['category']=='nao_identificado' for f in r['features'])


def test_tool_budget_returns_partial_without_promoting_unvalidated_coordinates(tiny_project):
    p,d=tiny_project
    r=run(p,d,limits=Limits(max_tools=5).model_dump())
    assert r['agent_report']['status']=='partial'
    assert r['agent_report']['tool_calls']==5
    assert all(f['geometry'] is None for f in r['features'])


def test_provider_unavailable_keeps_local_extraction(tiny_project,monkeypatch):
    monkeypatch.delenv('AI_API_KEY',raising=False)
    p,d=tiny_project;r=run(p,d,{'mode':'ai'})
    assert r['features'] and not r['agent_report']['model_used']
    assert any('AI_API_KEY' in issue for issue in r['issues'])


def test_cost_reservation_blocks_network(monkeypatch):
    monkeypatch.setenv('AI_API_KEY','test-secret-never-sent')
    monkeypatch.setenv('AI_MODEL','contract-fixture')
    limits=Limits(max_cost_usd=0,input_usd_per_million=1,output_usd_per_million=1)
    provider=Provider(limits,time.monotonic()+10)
    with pytest.raises(ProviderUnavailable,match='orçamento'):
        provider.choose(['native_text'],{})
    assert provider.calls==0


def test_structured_tool_call_contract_and_invalid_tool_rejected(monkeypatch):
    import httpx
    monkeypatch.setenv('AI_API_KEY','test-secret')
    monkeypatch.setenv('AI_MODEL','contract-fixture')
    original=httpx.Client
    def handler(request):
        body=json.loads(request.content)
        assert body['tool_choice']['function']['name']=='choose_tool'
        assert 'não confiável' in body['messages'][0]['content']
        return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'function':{'name':'choose_tool','arguments':json.dumps({'tool':'run_shell','reason':'invalid'})}}]}}],
                                       'usage':{'prompt_tokens':10,'completion_tokens':5}})
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    provider=Provider(Limits(input_usd_per_million=1,output_usd_per_million=1),time.monotonic()+10)
    with pytest.raises(ProviderUnavailable,match='inválida'):provider.choose(['native_text'],{})
    assert provider.calls==1


def test_dynamic_class_annotation_corrections_exports_and_examples(tiny_project,monkeypatch):
    p,d=tiny_project;r=run(p,d);main.save(r)
    client=TestClient(main.app)
    f=next(f for f in r['features'] if f['object_type']=='annotation' and f['label']=='AREA A')
    url=f"/api/projects/{r['id']}/features/{f['id']}/review"
    response=client.post(url,json={'action':'correct','revision':0,'note':'Texto e classe conferidos',
                                   'class_name':'Rede sanitária','text':'ÁREA A revisada','orientation':12,
                                   'linked_feature_id':None,'text_bbox':[90,95,190,130]})
    assert response.status_code==200,response.text
    edited=next(x for x in response.json()['features'] if x['id']==f['id'])
    assert edited['category']=='custom_rede_sanitaria'
    assert edited['attributes']['original_text']=='AREA A'
    assert edited['attributes']['orientation']==12
    assert edited['linked_feature_id'] is None
    assert response.json()['reviewed_examples'][-1]['action']=='correct'
    accepted=client.post(url,json={'action':'accept','revision':1,'acknowledge':True})
    assert accepted.status_code==200
    project=accepted.json()
    gj=json.loads(export(project,'geojson',types=['annotation'])[0])
    assert gj['features'][0]['properties']['attributes']['text']=='ÁREA A revisada'
    assert gj['audit']['excluded']
    kml=export(project,'kml')[0];assert b'<Folder>' in kml and b'<ExtendedData>' in kml
    z=zipfile.ZipFile(io.BytesIO(export(project,'shp')[0]))
    assert 'LEIA-ME.txt' in z.namelist()
    assert json.loads(z.read('feicoes.geojson'))['features'][0]['properties']['attributes']['quad']
    invalid=client.post(url,json={'action':'correct','revision':2,'note':'erro','linked_feature_id':'missing'})
    assert invalid.status_code==422


def test_manual_override_requires_embedded_check(tiny_project):
    p,d=tiny_project
    controls=[{'page':x,'world':[400000+x[0],7900400-x[1]],'evidence':'teste'} for x in [[0,0],[400,0],[400,400],[0,400]]]
    client=TestClient(main.app)
    body={'page':1,'crs':'EPSG:31984','controls':controls,'crs_evidence':'controles verificados'}
    res=client.post(f"/api/projects/{p['id']}/georeference",json=body)
    assert res.status_code==422 and 'GeoPDF' in res.json()['detail']
    res=client.post(f"/api/projects/{p['id']}/georeference",json={**body,'override_embedded':True})
    assert res.status_code==200


def test_real_a0_contains_two_distinct_georeferenced_viewports():
    with fitz.open(ROOT/'exemplos_plantas/Projeto Urbanistico (A0)_877 (1).pdf') as doc:
        result=inspect(doc[0])
    assert len(result['viewports'])==2
    assert {v['crs'] for v in result['viewports']}=={'EPSG:31982','EPSG:31984'}
    assert all(v['verified'] for v in result['viewports'])


def test_real_outline_pdf_ocr(tmp_path,monkeypatch):
    if not (ROOT/'models/tessdata/por.traineddata').exists():pytest.skip('OCR language models not installed')
    monkeypatch.setattr(main,'DATA',tmp_path)
    source=ROOT/'exemplos_plantas/macuco_divisao1.PDF'
    p=main.create(source);r=run(p,tmp_path/p['id'])
    annotations=[f for f in r['features'] if f['object_type']=='annotation']
    assert annotations and all(f['extraction_method']=='tesseract_ocr' for f in annotations)
    assert all(f['quality']['reading'] is None for f in annotations)
    assert any(t['tool']=='ocr' and t['status']=='completed' for t in r['agent_report']['tools'])


def test_model_cannot_supply_final_coordinates():
    from backend.agent_schema import Semantics
    with pytest.raises(ValueError):Semantics.model_validate({'geometry':{'type':'Point','coordinates':[1,2]}})
    assert 'Nunca produza coordenadas finais' in SYSTEM


def test_background_job_commits_real_results(tiny_project):
    p,d=tiny_project;client=TestClient(main.app)
    response=client.post(f"/api/projects/{p['id']}/agent",json={'mode':'local'})
    assert response.status_code==200,response.text
    job=response.json()['id'];deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        state=client.get(f"/api/projects/{p['id']}/agent/{job}").json()
        if state['status'] not in ('queued','running'):break
        time.sleep(.05)
    assert state['status']=='completed',state
    assert main.read(p['id'])['features']
    assert client.get(f"/api/projects/{p['id']}/agent/{job}/report").status_code==200


def test_job_cancel_preserves_project(tiny_project):
    p,d=tiny_project;client=TestClient(main.app)
    before=(d/'project.json').read_bytes()
    job=client.post(f"/api/projects/{p['id']}/agent",json={'mode':'local'}).json()['id']
    assert client.post(f"/api/projects/{p['id']}/agent/{job}/cancel").status_code==200
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        state=client.get(f"/api/projects/{p['id']}/agent/{job}").json()
        if state['status'] not in ('queued','running'):break
        time.sleep(.05)
    assert state['status']=='cancelled'
    assert (d/'project.json').read_bytes()==before


def test_job_concurrent_review_conflict_preserves_user_change(tiny_project):
    p,d=tiny_project;client=TestClient(main.app)
    job=client.post(f"/api/projects/{p['id']}/agent",json={'mode':'local'}).json()['id']
    edited=main.read(p['id']);edited['issues'].append('Revisão durante execução');main.save(edited)
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        state=client.get(f"/api/projects/{p['id']}/agent/{job}").json()
        if state['status'] not in ('queued','running'):break
        time.sleep(.05)
    assert state['status']=='conflict',state
    assert 'Revisão durante execução' in main.read(p['id'])['issues']
    assert not main.read(p['id'])['features']


def test_invalid_region_is_rejected_before_execution(tiny_project):
    p,d=tiny_project;client=TestClient(main.app)
    r=client.post(f"/api/projects/{p['id']}/agent",json={'regions':{'1':[{'id':'r','role':'main_map','bbox':[-1,0,400,400]}]}})
    assert r.status_code==422
