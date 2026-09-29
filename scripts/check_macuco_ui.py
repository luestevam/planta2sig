"""Local browser smoke check; server must be running on port 8001."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
projects = json.loads((root/'scratch/macuco_projects.json').read_text())
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width':1600,'height':1100})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    for i, project in enumerate(projects):
        page.goto('http://127.0.0.1:8001/?project='+project['id'])
        page.wait_for_function('window.__S?.project?.features.length > 0')
        page.wait_for_timeout(1000)
        for category in ['ponto','limite','hidrografia']:
            page.locator('#mapSvg .cat-'+category).first.dispatch_event('click')
            assert page.locator('#featureDetails').is_visible()
            assert 'NaN' not in page.locator('#pageSvg').inner_html()+page.locator('#mapSvg').inner_html()
            assert page.locator('#selectedArea').inner_text() != 'sem georreferenciamento'
            if category == 'ponto':
                assert page.locator('#selectedVertices').inner_text() == '1'
            page.locator('#correctBtn').click()
            assert page.locator('#modalBody .data-table input').count() >= 2
            page.locator('#cNo').click()
        page.screenshot(path=str(root/f'scratch/macuco_ui{i}.png'), full_page=True)
        print(project['name'], page.locator('#mapSvg .feature-poly').count(), 'features; errors', errors)
    assert not errors
    browser.close()
