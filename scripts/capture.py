"""Drives the real UI with Playwright and saves screenshots (dev tool)."""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT=Path(__file__).resolve().parents[1]/'screenshots'
URL='http://127.0.0.1:5173/'
EX={'pdf':'Projeto Urbanistico (A0)_877 (1).pdf','j1':'Exmplo01.jpg','j2':'Exmeplo02.jpg'}
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={'width':1672,'height':940})
    errors=[]; pg.on('pageerror',lambda e:errors.append(str(e))); pg.on('console',lambda m:m.type=='error' and errors.append(m.text))
    for key,name in EX.items():
        pg.goto(URL); pg.wait_for_selector('#exampleSelect option:nth-child(2)',state='attached')
        pg.select_option('#exampleSelect',label=name); pg.wait_for_function('window.__S.project&&window.__S.project.stage==="extracted"',timeout=180000)
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT/f'{key}-1-extraido.png'))
        pg.evaluate('()=>{const f=window.__S.project.features[0]; document.querySelector(`.feature-poly[data-id="${f.id}"]`).dispatchEvent(new MouseEvent("click",{bubbles:true}))}')
        pg.wait_for_timeout(600); pg.screenshot(path=str(OUT/f'{key}-2-selecionado.png'))
    print('errors',errors)
    b.close()
