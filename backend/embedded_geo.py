"""Read ISO 32000 GEO measures before attempting manual control fits.

Coordinates are read only from PDF dictionaries. GPTS pairs are lat/lon.
Viewport BBox endpoints retain their order (some exporters reverse Y).
Unsupported LGIDict and non-affine measures remain explicit pending items.
"""
import re
import numpy as np
import pymupdf as fitz
from pyproj import CRS, Transformer
from .geometry import affine


def parse_pdf(text):
    tokens = re.findall(r'<<|>>|\[|\]|\((?:\\.|[^\\()])*\)|<[^<>]*>|/[^/\s<>\[\]()]+|[^/\s<>\[\]()]+', text)
    pos = 0
    def value():
        nonlocal pos
        t = tokens[pos]; pos += 1
        if t == '<<':
            out = {}
            while tokens[pos] != '>>':
                key = tokens[pos].lstrip('/'); pos += 1; out[key] = value()
            pos += 1; return out
        if t == '[':
            out = []
            while tokens[pos] != ']': out.append(value())
            pos += 1; return out
        if t.startswith('('): return re.sub(r'\\([()\\])', r'\1', t[1:-1])
        if re.fullmatch(r'[-+]?\d+(?:\.\d*)?|[-+]?\.\d+', t):
            if pos+1 < len(tokens) and tokens[pos].isdigit() and tokens[pos+1] == 'R':
                pos += 2; return ('ref', int(t))
            return float(t)
        return t
    return value()


def inspect(page):
    doc = page.parent
    def resolve(v):
        for _ in range(8):
            if isinstance(v, tuple) and v[0] == 'ref': v = parse_pdf(doc.xref_object(v[1]))
            else: return v
        raise ValueError('Referências PDF recursivas.')
    result = {'viewports': [], 'issues': [], 'checked': True}
    kind, raw = doc.xref_get_key(page.xref, 'VP')
    if kind == 'null':
        if doc.xref_get_key(page.xref, 'LGIDict')[0] != 'null':
            result['issues'].append('LGIDict presente, sem suporte automático; conferir controles manualmente.')
        return result
    try:
        viewports = resolve(parse_pdf(raw))
        if not isinstance(viewports, list): viewports = [viewports]
        for i, vp in enumerate(viewports):
            vp = resolve(vp); measure = resolve(vp.get('Measure'))
            if not isinstance(measure, dict) or measure.get('Subtype') != '/GEO': continue
            gcs = resolve(measure.get('GCS')); epsg = gcs.get('EPSG')
            crs = CRS.from_epsg(int(epsg)) if epsg else CRS.from_wkt(gcs['WKT'])
            geographic = crs.geodetic_crs
            gps = np.asarray(measure['GPTS'], float).reshape(-1, 2)
            local = np.asarray(measure['LPTS'], float).reshape(-1, 2)
            if len(gps) != len(local) or len(gps) < 4 or not np.isfinite(gps).all():
                raise ValueError('GPTS/LPTS inválidos.')
            if np.any(np.abs(gps[:, 0])>90) or np.any(np.abs(gps[:, 1])>180):
                raise ValueError('GPTS fora dos limites geográficos.')
            if crs.is_geographic:
                zone = int((gps[:, 1].mean()+180)//6)+1
                metric = CRS.from_epsg((32700 if gps[:, 0].mean()<0 else 32600)+zone)
            else: metric = crs
            if abs(metric.axis_info[0].unit_conversion_factor-1)>1e-8:
                raise ValueError('CRS embutido não está em metros.')
            x0, y0, x1, y1 = vp['BBox']
            pts = [fitz.Point(x0+x*(x1-x0), y0+y*(y1-y0))*page.transformation_matrix*page.rotation_matrix for x,y in local]
            t = Transformer.from_crs(geographic, metric, always_xy=True)
            controls = [{'page':list(p),'world':list(t.transform(lon,lat)),
                         'evidence':f'PDF VP[{i}] /Measure /GPTS /LPTS'} for p,(lat,lon) in zip(pts,gps)]
            g = affine(controls)
            xs, ys = zip(*(list(p) for p in pts))
            bounds = [min(xs), min(ys), max(xs), max(ys)]
            g.update(crs=metric.to_string(), controls=controls, unit='m',
                     verified=max(g['residuals'])<=2, origin=f'GeoPDF nativo: VP[{i}] /Measure /GCS',
                     scope_bbox=bounds, embedded=True, viewport_index=i,
                     geographic_crs=geographic.to_string(), external_accuracy_m=None)
            if not g['verified']: result['issues'].append(f'Viewport {i}: resíduo acima de 2 m; não aplicado.')
            result['viewports'].append(g)
    except (ValueError, KeyError, TypeError, IndexError, RuntimeError) as exc:
        result['issues'].append('Georreferenciamento embutido não utilizável: '+str(exc))
    return result
