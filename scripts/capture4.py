from pathlib import Path
from playwright.sync_api import sync_playwright
OUT=Path(__file__).resolve().parents[1]/'screenshots'
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={'width':1672,'height':940}); errs=[]
    pg.on('pageerror',lambda e:errs.append(str(e)))
    pg.goto('http://127.0.0.1:5173/'); pg.wait_for_selector('#exampleSelect option:nth-child(2)',state='attached')
    pg.select_option('#exampleSelect',label='Projeto Urbanistico (A0)_877 (1).pdf'); pg.wait_for_function('window.__S.project&&window.__S.project.stage==="extracted"',timeout=400000); pg.wait_for_timeout(2000)
    pg.evaluate('()=>{const f=window.__S.project.features.find(f=>f.category==="perimetro");document.querySelector(`#pageSvg .feature-poly[data-id="${f.id}"]`).dispatchEvent(new MouseEvent("click",{bubbles:true}))}')
    pg.wait_for_timeout(800); pg.screenshot(path=str(OUT/'pdf-v3-perimetro.png'))
    # zoom in on lots 48-50 of the page view and wait for the sharp crop
    box=pg.locator('#pageSvg').bounding_box(); pg.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
    for _ in range(16): pg.mouse.wheel(0,-300); pg.wait_for_timeout(120)
    pg.wait_for_selector('#hires',state='attached',timeout=15000); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT/'pdf-v3-zoom-nitido.png'))
    print('hires present:',pg.evaluate('!!document.querySelector("#hires")'),'errors',errs); b.close()
