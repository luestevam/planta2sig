from pathlib import Path
from playwright.sync_api import sync_playwright
OUT=Path(__file__).resolve().parents[1]/'screenshots'
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={'width':1672,'height':940}); errs=[]
    pg.on('pageerror',lambda e:errs.append(str(e)))
    pg.goto('http://127.0.0.1:5173/'); pg.wait_for_selector('#exampleSelect option:nth-child(2)',state='attached')
    pg.select_option('#exampleSelect',label='Projeto Urbanistico (A0)_877 (1).pdf'); pg.wait_for_function('window.__S.project&&window.__S.project.stage==="extracted"',timeout=400000); pg.wait_for_timeout(2500)
    pg.screenshot(path=str(OUT/'pdf-v2-1-extraido.png'))
    fid=pg.evaluate('()=>{const f=window.__S.project.features.find(f=>f.category==="lote"&&f.confidence>=.9&&f.label.includes("Lote 39"))||window.__S.project.features.find(f=>f.confidence>=.9);return f.id}')
    pg.evaluate('id=>document.querySelector(`#pageSvg .feature-poly[data-id="${id}"]`).dispatchEvent(new MouseEvent("click",{bubbles:true}))',fid)
    pg.wait_for_timeout(800); pg.screenshot(path=str(OUT/'pdf-v2-2-lote-selecionado.png'))
    print('errors',errs); b.close()
