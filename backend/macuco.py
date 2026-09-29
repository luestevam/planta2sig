"""Hash-bound visual transcription of the supplied outlined-text survey PDFs.

Table coordinates refer to the destination (Para) vertex, not the De vertex.
No OCR or model coordinates are generated at runtime. Alignment is checked
against every perimeter vertex, with a strict residual and area gate.
"""
import numpy as np
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union
from .geometry import affine, transform

PROFILES = {
    '998c9877ce197fed33fe27d623e83e432f2500f2666190224d8cbddaa13eff2d': {
        'boundary': 2, 'lots': [4, 3, 1], 'area': 482230.921,
        'bbox': [62, 106, 268, 354], 'hydro': [(3213, 'Polygon', 'Corpo d’água')],
        'ne': '''7952850.6248 401643.4369
7952845.7115 401658.3591
7952846.8973 401677.2291
7952854.2361 401688.1089
7952901.7297 401720.1148
7952908.8254 401722.5354
7952915.1469 401762.1256
7952918.2481 401788.9613
7952919.1909 401797.1202
7952912.8543 401825.6049
7952913.0938 401863.4967
7952937.0290 401939.6203
7952959.6144 402019.2911
7953157.1203 401976.1899
7953191.2350 401964.6202
7953212.9832 401964.1124
7953399.7907 401941.1863
7953741.0756 401900.1540
7954021.3652 401864.0586
7954063.8209 401859.4793
7953979.3105 401643.4246
7953936.0320 401532.7811
7953912.9039 401506.3166
7953814.7315 401396.5343
7953686.8102 401404.6671
7953605.6574 401420.2854
7953161.0304 401553.2247
7952986.1197 401607.6381
7952852.2817 401642.5006''',
    },
    '90c04dc5a67cd47f932549cdc090d25e3d582ac266c49bca5285ee6880594eb1': {
        'boundary': 40, 'lots': list(range(1, 14)), 'area': 741984.719,
        'bbox': [46, 120, 195, 391], 'hydro': [(2117, 'LineString', 'Curso d’água / margem')],
        'ne': '''7952390.9292 402197.6739
7952398.8934 402294.3965
7952406.6919 402389.1066
7952414.2006 402480.2976
7952421.4492 402568.3286
7952428.4628 402653.5069
7952435.2392 402735.8031
7952439.0410 402781.9753
7952344.7298 402789.5804
7952333.7427 402804.0557
7952336.3791 402821.0167
7952346.7755 402887.9024
7952356.8233 402952.5457
7952366.5395 403015.0553
7952375.9548 403075.6295
7952390.8419 403171.4063
7953273.2739 403016.5782
7953333.1072 403007.3027
7953318.4397 402984.8005
7953280.7331 402926.9528
7953241.8217 402867.2568
7953201.5822 402805.5231
7953179.1814 402771.1569
7953166.6656 402741.0005
7953136.4615 402668.2250
7953106.1632 402595.2224
7953103.8551 402588.4181
7953075.6533 402505.2817
7953046.5071 402419.3610
7953016.3146 402330.3561
7952987.1722 402244.4469
7952986.3446 402237.7827
7952974.3813 402141.4456
7952962.3188 402044.3103
7952395.3425 402098.9508
7952383.9249 402112.6103''',
    },
}


def path_points(drawing):
    points = []
    for item in drawing['items']:
        if item[0] == 'l':
            chain = [list(item[1]), list(item[2])]
        elif item[0] == 'qu':
            q = item[1]
            chain = [list(q.ul), list(q.ur), list(q.lr), list(q.ll), list(q.ul)]
        else:
            raise ValueError('Caminho cadastral contém segmento não suportado.')
        if points and np.linalg.norm(np.array(points[-1])-chain[0]) > .001:
            raise ValueError('Caminho cadastral descontínuo.')
        points.extend(chain if not points else chain[1:])
    return points


def extract(page, result):
    from .extraction import feature, evidence
    profile = PROFILES[result['sha256']]
    ds = page.get_drawings()
    ring = path_points(ds[profile['boundary']])
    if np.linalg.norm(np.array(ring[0])-ring[-1]) > .001:
        raise ValueError('Perímetro do perfil não está fechado.')
    pixels = np.array(ring[:-1])
    table = np.array([[float(e), float(n)] for n, e in
                      (line.split() for line in profile['ne'].splitlines())])
    if len(pixels) != len(table):
        raise ValueError('Quantidade de vértices difere da tabela do perfil.')
    candidates = []
    for reverse in (False, True):
        indices = np.arange(len(table))[::-1] if reverse else np.arange(len(table))
        for shift in range(len(table)):
            order = np.roll(indices, shift)
            controls = [{'page': p.tolist(), 'world': w.tolist(),
                         'evidence': f'Tabela, destino P{(int(idx)+1)%len(table)+1}'}
                        for p, w, idx in zip(pixels, table[order], order)]
            g = affine(controls)
            candidates.append((g['rmse'], g, controls, order))
    _, geo, controls, order = min(candidates, key=lambda c: c[0])
    if max(geo['residuals']) > .05:
        raise ValueError('Tabela e vetores não conferem: resíduo maior que 5 cm.')
    if abs(Polygon(table).area/profile['area']-1) > .001:
        raise ValueError('Área da tabela não confere com a área declarada.')
    origin = 'Tabela transcrita visualmente do PDF, vinculada ao SHA-256; vértices vetoriais conferidos por ajuste afim'
    geo.update(crs='EPSG:31984', controls=controls, verified=True, unit='m', origin=origin)
    result['georeferencing']['1'] = geo
    result['evidence'].extend([
        evidence('crs', 'SIRGAS 2000 / UTM 24S • EPSG:31984', [730, 102, 828, 185],
                 'Carimbo: SIRGAS2000, MC 39°; hemisfério sul na latitude.', .95, origin=origin),
        evidence('tabela', f'{len(table)} vértices E/N', profile['bbox'],
                 'Coordenadas na coluna Para; transcrição visual conferida com os vetores e a área.', .95, origin=origin)])
    def add(points, category, label, kind='Polygon', world=None, declared=None, bbox=None):
        bounds = bbox or (Polygon(points).bounds if kind == 'Polygon' else
                          LineString(points).bounds if kind == 'LineString' else [*points[0], *points[0]])
        ev = evidence(category, label, bounds, 'Geometria nativa do PDF associada à tabela de coordenadas.', .95, origin=origin)
        f = feature(points, category, label, ev, geo['crs'],
                    world if world is not None else transform(points, geo['matrix']), declared,
                    geometry_type=kind)
        f['issues'].append('Conferir a transcrição e a sobreposição antes de aceitar; resíduo interno não mede acurácia externa.')
        result['features'].append(f)
        return f
    perimeter = add(ring, 'perimetro', 'Perímetro do imóvel', world=table[order].tolist(), declared=profile['area'])
    perimeter['vertices_table'] = [[f'P{(i+1)%len(table)+1}', *w] for i, w in enumerate(table.tolist())]
    lot_polys = []
    for number, idx in enumerate(profile['lots'], 1):
        coords = path_points(ds[idx])
        pg = Polygon(coords)
        if not pg.is_valid or not Polygon(ring).buffer(.001).covers(pg):
            raise ValueError('Divisão fora do perímetro ou inválida.')
        lot_polys.append(pg)
        add(coords, 'lote', f'Divisão {number:02d}')
    if unary_union(lot_polys).symmetric_difference(Polygon(ring)).area > .1:
        raise ValueError('Divisões não cobrem o perímetro.')
    # Unique cadastral edges, preserving perimeter and internal divisions.
    edges = {}
    for pg in lot_polys:
        pts = list(pg.exterior.coords)
        for a, b in zip(pts, pts[1:]):
            key = tuple(sorted((tuple(round(v, 3) for v in a), tuple(round(v, 3) for v in b))))
            edges.setdefault(key, [a, b])
    for i, pts in enumerate(edges.values(), 1):
        add(pts, 'limite', f'Limite {i:02d}', 'LineString')
    for p, idx in zip(pixels.tolist(), order):
        add([p], 'ponto', f'P{(int(idx)+1)%len(table)+1}', 'Point', world=[table[idx].tolist()], bbox=profile['bbox'])
    # Blue hydrographic paths only within the main plan; legend and hatch strokes excluded.
    for idx, kind, label in profile['hydro']:
        add(path_points(ds[idx]), 'hidrografia', label, kind)
    from pyproj import Transformer
    stamp = Transformer.from_crs(4674, 31984, always_xy=True).transform(
        -(39+59/60+48.718/3600), -(18+16/60+38.434/3600))
    stamp_difference = float(np.linalg.norm(np.array(stamp)-table[-1]))
    if stamp_difference > 2:
        warning = f'P1 do carimbo diverge {stamp_difference:.1f} m do P1 da tabela. Georreferenciamento baseado na tabela completa; confirmar com o responsável pelo levantamento.'
        result['issues'].append(warning)
        for f in result['features']:
            f['issues'].append(warning)
    result['diagnostic'].update(recognized=['Tabela de coordenadas em contornos vetoriais', 'Perímetro', 'Divisões', 'Vértices', 'Limites', 'Hidrografia'],
                                declared_lots=len(lot_polys), transcription='Visual, vinculada ao SHA-256',
                                max_residual_m=max(geo['residuals']))
    result['issues'].append('Textos convertidos em desenhos: este arquivo usa transcrição visual vinculada ao seu hash, não OCR genérico.')
