from pathlib import Path
from playwright.sync_api import sync_playwright
OUT=Path(__file__).resolve().parents[1]/'screenshots'
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={'width':1672,'height':940},accept_downloads=True)
    errs=[]; pg.on('pageerror',lambda e:errs.append(str(e)))
    pg.goto('http://127.0.0.1:5173/'); pg.wait_for_selector('#exampleSelect option:nth-child(2)',state='attached')
    pg.select_option('#exampleSelect',label='Exmplo01.jpg'); pg.wait_for_function('window.__S.project&&window.__S.project.stage==="extracted"')
    pg.wait_for_timeout(800)
    pg.click('.feature-poly',force=True); pg.wait_for_timeout(400)
    pg.screenshot(path=str(OUT/'fluxo-1-selecionado.png'),full_page=True)
    pg.click('#correctBtn'); pg.fill('#cNote','ajuste do vértice 1 sobre a planta'); pg.fill('[data-i="0"][data-k="0"]',str(float(pg.input_value('[data-i="0"][data-k="0"]'))+8))
    pg.wait_for_timeout(300); pg.screenshot(path=str(OUT/'fluxo-2-corrigir.png'))
    pg.click('#cYes'); pg.wait_for_timeout(800)
    print('hist',pg.inner_text('#historyCount'),'area',pg.inner_text('#selectedArea'))
    pg.click('#acceptBtn'); pg.check('#ack'); pg.click('#acYes'); pg.wait_for_timeout(600)
    for fmt in ['geojson','kml','shp']:
        with pg.expect_download() as d: pg.click(f'[data-export="{fmt}"]')
        print(fmt,d.value.suggested_filename)
    pg.screenshot(path=str(OUT/'fluxo-3-exportado.png'),full_page=True)
    pg.click('#historyBtn'); pg.wait_for_timeout(300); pg.screenshot(path=str(OUT/'fluxo-4-historico.png')); pg.click('#closeModal')
    pg.select_option('#exampleSelect',label='Projeto Urbanistico (A0)_877 (1).pdf'); pg.wait_for_function('window.__S.project.name.endsWith(".pdf")',timeout=180000); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT/'pdf-3-evidencias.png'))
    pg.click('#diagnosticBtn'); pg.wait_for_timeout(300); pg.screenshot(path=str(OUT/'pdf-4-diagnostico.png')); pg.click('#closeModal')
    pg.set_viewport_size({'width':390,'height':844}); pg.wait_for_timeout(600); pg.screenshot(path=str(OUT/'mobile.png'),full_page=True)
    print('errors',errs); b.close()
