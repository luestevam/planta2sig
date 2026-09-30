"""Deterministic lot / block / perimeter extraction for the urban-plan PDF.

Lot boundaries of this document are raster strokes (the vector layer only holds text outlines), so the
regions are recovered by seeded watershed: every bold lot number is a seed, boundary strokes are barriers.
Every polygon is checked against the area table printed on the sheet before it is trusted.
"""
import re
import numpy as np
import cv2
from shapely.geometry import Polygon, MultiPoint, Point
from shapely.validation import make_valid

S = 2                    # render scale (pixels per PDF point)
MAP_X_MAX = 2830         # right-hand panels (situation map, stamp, legends) start here
ROAD, EXTERIOR = 1, 2
LOT0 = 10                # first lot marker id

NUM = r'\d{1,3}(?:\.\d{3})*,\d{2}'


def num(s):
    return float(s.replace('.', '').replace(',', '.'))


def area_table(page):
    """{(quadra, lote): (area_m2, ac_m2)} rebuilt from the printed table, row by row from word positions."""
    words = [w for w in page.get_text('words') if w[0] < 1150 and w[1] > 1180]
    rows = {}
    for w in words:
        rows.setdefault(round((w[1] + w[3]) / 2 / 2), []).append(w)   # ~2pt row tolerance
    table = {}
    for _, ws in rows.items():
        toks = [w[4] for w in sorted(ws, key=lambda w: w[0])]
        i = 0
        while i + 3 < len(toks):
            q, l, a, ac = toks[i:i + 4]
            if re.fullmatch(r'\d\d', q) and re.fullmatch(r'\d\d', l) and re.fullmatch(NUM, a) and re.fullmatch(NUM, ac):
                table[(q, l)] = (num(a), num(ac)); i += 4
            else:
                i += 1
    return table


def declared_totals(page):
    text = page.get_text()
    out = {}
    m = re.search(r'Área Total:\s*(' + NUM + ')', text)
    if m: out['area'] = num(m.group(1))
    m = re.search(r'Perímetro:\s*(' + NUM + ')', text)
    if m: out['perimeter'] = num(m.group(1))
    return out


def labels(page):
    lots, quads = [], []
    for b in page.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                t = s['text'].strip()
                if re.fullmatch(r'\d\d', t) and 'Bold' in s['font']:
                    x0, y0, x1, y1 = s['bbox']
                    item = (t, ((x0 + x1) / 2, (y0 + y1) / 2), (x0, y0, x1, y1))
                    if item[1][0] > MAP_X_MAX: continue
                    (quads if round(s['size']) >= 8 else lots).append(item)
    return lots, quads


def flood(edt, markers, step=0.5):
    """Seeded flooding from the middle of each cell (highest distance-to-wall) outwards, in bands of `step` pixels.
    Same idea as a marker-controlled watershed on -edt, but only NumPy/OpenCV so the deploy stays small.
    Only the moving front is touched, so the cost grows with the number of pixels assigned, not with the image."""
    H, W = edt.shape
    Wp = W + 2
    q = np.full((H + 2, Wp), -1, np.int32)          # -1 border: never flooded, no bounds checks needed
    q[1:-1, 1:-1] = np.floor(edt / step)
    lab = np.zeros((H + 2, Wp), np.int32)
    lab[1:-1, 1:-1] = markers
    qf, lf = q.ravel(), lab.ravel()
    offs = np.array([1, -1, Wp, -Wp])
    pend = {}                                        # band -> proposals waiting for their band to be reached

    def propose(pos, labels):
        p = (pos[:, None] + offs[None, :]).ravel()
        l = np.repeat(labels, 4)
        m = (qf[p] >= 0) & (lf[p] == 0)
        return p[m], l[m]

    def stash(p, l, level):
        band = qf[p]
        now = band >= level
        later = ~now
        if later.any():
            for lv in np.unique(band[later]):
                m = later & (band == lv)
                pend.setdefault(int(lv), []).append((p[m], l[m]))
        return p[now], l[now]

    seeds = np.flatnonzero(lf)
    qmax = int(qf.max())
    cur_p, cur_l = stash(*propose(seeds, lf[seeds]), qmax)
    for level in range(qmax, -1, -1):
        if level in pend:
            ps = [cur_p] + [x[0] for x in pend[level]]
            ls = [cur_l] + [x[1] for x in pend.pop(level)]
            cur_p, cur_l = np.concatenate(ps), np.concatenate(ls)
        while len(cur_p):
            keep = lf[cur_p] == 0
            cur_p, cur_l = cur_p[keep], cur_l[keep]
            if not len(cur_p): break
            cur_p, first = np.unique(cur_p, return_index=True)
            cur_l = cur_l[first]
            lf[cur_p] = cur_l
            cur_p, cur_l = stash(*propose(cur_p, cur_l), level)
    return lab[1:-1, 1:-1]


def _long(mask, min_len):
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = np.maximum(st[1:, cv2.CC_STAT_WIDTH], st[1:, cv2.CC_STAT_HEIGHT]) >= min_len
    return keep[lab]


def walls_and_roads(page, close=27, min_len=25, grey=(28, 65, 45, 100, 175), dil=1):
    import pymupdf as fitz
    pix = page.get_pixmap(matrix=fitz.Matrix(S, S), alpha=False)
    im = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    r, g, b = (im[:, :, i].astype(np.int16) for i in range(3))
    spread = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    def near(c, t): return (np.abs(r - c[0]) <= t) & (np.abs(g - c[1]) <= t) & (np.abs(b - c[2]) <= t)
    rg, rb, gb, lo, hi = grey
    greyish = (np.abs(r - g) <= rg) & ((r - b) <= rb) & ((g - b) <= gb) & (r >= lo) & (r <= hi)   # lot separators, incl. where hatch blends in
    strokes = greyish | near((169, 0, 230), 60) | near((115, 0, 0), 45) | near((255, 170, 0), 18) \
        | ((spread <= 8) & (r <= 40))
    wall = cv2.dilate(_long(strokes, min_len).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=dil)
    # bridge the small breaks where hatch or text interrupts a separator
    wall = cv2.morphologyEx(wall, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close, close)))
    road = near((224, 224, 224), 5).astype(np.uint8)
    road = cv2.morphologyEx(road, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    road = _long(road, 40)
    return wall.astype(bool), road, im.shape[:2]


def extract(page, to_world, crs_meters_factor=1.0, **wall_params):
    """Returns dict(lots, blocks, perimeter, rejected, stats). `to_world(points_pt)` maps page points to E/N metres."""
    lots, quads = labels(page)
    table = area_table(page)
    declared = declared_totals(page)
    wall, road, (H, W) = walls_and_roads(page, **wall_params)
    wall &= ~road.astype(bool)
    free = ~(wall | road.astype(bool))
    edt = cv2.distanceTransform(free.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    markers = np.zeros((H, W), np.int32)
    markers[road.astype(bool)] = ROAD
    pts = np.array([c for _, c, _ in lots]) * S
    hull = MultiPoint([tuple(p) for p in pts]).convex_hull.buffer(60 * S)
    ext = np.zeros((H, W), np.uint8)
    cv2.fillPoly(ext, [np.array(hull.exterior.coords, np.int32)], 1)
    ext[:, int(MAP_X_MAX * S):] = 0
    ext = 1 - ext; ext[:, int(MAP_X_MAX * S):] = 0
    markers[(ext > 0) & free] = EXTERIOR
    for i, (_, c, bb) in enumerate(lots):
        # seed = the printed number's box (grown slightly), so a stray stroke next to the centre cannot enclose it
        x0, y0, x1, y1 = (int(v * S) for v in bb)
        cv2.rectangle(markers, (x0 - 2, y0 - 2), (x1 + 2, y1 + 2), LOT0 + i, -1)
    ws = flood(edt, markers)
    del edt
    res = dict(lots=[], blocks=[], perimeter=None, rejected=[], table=table, declared=declared)
    lot_mask = ws >= LOT0
    for i, (t, c, bb) in enumerate(lots):
        m = (ws == LOT0 + i).astype(np.uint8)
        x0, y0, bw, bh = cv2.boundingRect(m)
        if m.sum() == 0: res['rejected'].append(dict(reason='Sem região', bbox=list(bb))); continue
        sub = m[y0:y0 + bh, x0:x0 + bw]
        sub = cv2.morphologyEx(sub, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        cs, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cs: res['rejected'].append(dict(reason='Região desfeita pela limpeza', bbox=list(bb))); continue
        cnt = max(cs, key=cv2.contourArea)
        ring = (cv2.approxPolyDP(cnt, 2.0, True).reshape(-1, 2) + [x0, y0]) / S
        if len(ring) < 3: res['rejected'].append(dict(reason='Contorno degenerado', bbox=list(bb))); continue
        poly = Polygon(ring)
        if not poly.is_valid: poly = make_valid(poly).buffer(0)
        if poly.is_empty or poly.geom_type != 'Polygon' or poly.area <= 0:
            res['rejected'].append(dict(reason='Polígono inválido', bbox=list(bb))); continue
        ring = list(map(list, poly.exterior.coords))
        world = np.array(to_world(ring))
        area = Polygon(world).area * crs_meters_factor ** 2
        res['lots'].append(dict(lote=t, label_bbox=list(bb), page_ring=ring, area=area, seed=c, idx=i))
    # blocks = contiguous groups of lot regions (bounded by roads / exterior)
    n, comp = cv2.connectedComponents(lot_mask.astype(np.uint8), connectivity=4)
    lot_comp = {}
    for l in res['lots']:
        c = l['seed']
        v = comp[int(c[1] * S), int(c[0] * S)]
        lot_comp[l['idx']] = v
    by_comp = {}
    for l in res['lots']:
        by_comp.setdefault(lot_comp[l['idx']], []).append(l)
    qpts = [(t, c) for t, c, _ in quads]
    for v, ls in by_comp.items():
        if v == 0: continue
        ys, xs = np.nonzero(comp == v)
        m = np.zeros((ys.max() - ys.min() + 3, xs.max() - xs.min() + 3), np.uint8)
        m[ys - ys.min() + 1, xs - xs.min() + 1] = 1
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(cs, key=cv2.contourArea)
        ring = (cv2.approxPolyDP(cnt, 2.0, True).reshape(-1, 2) + [xs.min() - 1, ys.min() - 1]) / S
        poly = Polygon(ring)
        if not poly.is_valid: poly = make_valid(poly).buffer(0)
        if poly.geom_type != 'Polygon' or poly.area <= 0: continue
        inside = [q for q in qpts if poly.contains(Point(q[1]))]
        res['blocks'].append(dict(page_ring=list(map(list, poly.exterior.coords)), lots=[l['idx'] for l in ls],
                                  circles=[q[0] for q in inside], circle_pos=[q[1] for q in inside]))
    # project perimeter = everything that is neither exterior nor outside the map
    proj = (ws != EXTERIOR) & (np.arange(W)[None, :] < MAP_X_MAX * S)
    proj &= ~((ws == 0))
    proj = cv2.morphologyEx(proj.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cs, _ = cv2.findContours(proj, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cs:
        cnt = max(cs, key=cv2.contourArea)
        ring = cv2.approxPolyDP(cnt, 3.0, True).reshape(-1, 2) / S
        poly = Polygon(ring)
        if poly.is_valid and poly.area > 0:
            ring = list(map(list, poly.exterior.coords))
            world = Polygon(to_world(ring))
            res['perimeter'] = dict(page_ring=ring, area=world.area * crs_meters_factor ** 2, length=world.length * crs_meters_factor)
    res['ws_shape'] = (H, W)
    return res


# Three wall-detection settings for this sheet, tried in parallel. The first is the default; the others only replace
# a lot's outline when they reproduce the printed area better (the choice is recorded in the feature's evidence).
VARIANTS = [
    dict(close=21, min_len=10, grey=(40, 95, 65, 85, 195), dil=2),
    dict(close=21, min_len=12, grey=(50, 110, 80, 75, 205), dil=2),
    dict(close=15, min_len=15, grey=(40, 95, 65, 85, 195), dil=2),
]


def run_variant(path, matrix, params):
    import pymupdf as fitz
    from .geometry import transform
    with fitz.open(path) as doc:
        return extract(doc[0], lambda pts: transform(pts, matrix), **params)


def extract_all(path, matrix):
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=len(VARIANTS)) as ex:
        futures = [ex.submit(run_variant, path, matrix, v) for v in VARIANTS]
        return [f.result() for f in futures]
