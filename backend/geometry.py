"""Deterministic geometry only: no model-generated coordinates enter this module."""
import math
import numpy as np
from pyproj import CRS, Transformer, Geod
from shapely.geometry import Polygon, Point, LineString
from shapely.validation import explain_validity


def polygon(points):
    a=np.asarray(points,dtype=float)
    if a.ndim!=2 or a.shape[1]!=2 or len(a)<3 or not np.isfinite(a).all():
        raise ValueError('São necessários ao menos 3 vértices finitos (x, y).')
    p=Polygon(a)
    if not p.is_valid or p.area<=0:
        raise ValueError('Geometria inválida: '+explain_validity(p))
    return p


def page_geometry(points, geometry_type='Polygon'):
    if geometry_type == 'Polygon':
        return polygon(points)
    a = np.asarray(points, dtype=float)
    if a.ndim != 2 or a.shape[1] != 2 or not np.isfinite(a).all():
        raise ValueError('Coordenadas devem ser pares finitos (x, y).')
    if geometry_type == 'Point' and len(a) == 1:
        return Point(a[0])
    if geometry_type == 'LineString' and len(a) >= 2:
        line = LineString(a)
        if line.is_valid and line.length > 0:
            return line
    raise ValueError('Tipo ou quantidade de coordenadas inválidos.')


def reconstruct(points, crs, geometry_type='Polygon', holes=None):
    if not crs:
        raise ValueError('CRS ausente: a geometria permanece em coordenadas da página.')
    c=CRS.from_user_input(crs)
    p=page_geometry(points, geometry_type)
    if geometry_type=='Polygon' and holes:
        p=Polygon(points,holes)
        if not p.is_valid:raise ValueError('Geometria com anéis internos inválida: '+explain_validity(p))
    t=Transformer.from_crs(c,4326,always_xy=True)
    ring=[list(t.transform(*v)) for v in (p.exterior.coords if geometry_type == 'Polygon' else p.coords)]
    if any(not math.isfinite(x) or not math.isfinite(y) or abs(x)>180 or abs(y)>90 for x,y in ring):
        raise ValueError('Coordenadas incompatíveis com o CRS.')
    if geometry_type != 'Polygon':
        length = None if geometry_type == 'Point' else (Geod(ellps='GRS80').line_length(*zip(*ring)) if c.is_geographic else p.length*c.axis_info[0].unit_conversion_factor)
        return {'geometry': {'type': geometry_type, 'coordinates': ring[0] if geometry_type == 'Point' else ring},
                'area': None, 'perimeter': length}
    if c.is_geographic:
        from shapely.geometry.polygon import orient
        from shapely.ops import transform as shapely_transform
        geographic=orient(shapely_transform(t.transform,p),sign=1.)
        area,perimeter=Geod(ellps='GRS80').geometry_area_perimeter(geographic)
        area=abs(area)
    else:
        factor=c.axis_info[0].unit_conversion_factor
        area=p.area*factor**2
        perimeter=p.length*factor
    inner=[[[*t.transform(*v)] for v in r.coords] for r in p.interiors]
    return {'geometry':{'type':'Polygon','coordinates':[ring,*inner]},'area':area,'perimeter':perimeter}


def area_check(actual, declared, tolerance=.03):
    if declared is None: return {'declared':None,'difference_percent':None,'ok':None}
    if declared<=0: raise ValueError('Área declarada deve ser positiva.')
    diff=abs(actual-declared)/declared
    return {'declared':declared,'difference_percent':100*diff,'ok':diff<=tolerance}


def affine(controls):
    if len(controls)<4:
        raise ValueError('Use pelo menos 4 controles: ajuste afim com redundância.')
    a=np.array([[*c['page'],1] for c in controls],float)
    b=np.array([c['world'] for c in controls],float)
    if not np.isfinite(a).all() or not np.isfinite(b).all() or np.linalg.matrix_rank(a)<3:
        raise ValueError('Controles inválidos ou colineares.')
    m=np.linalg.lstsq(a,b,rcond=None)[0]
    if abs(np.linalg.det(m[:2]))<1e-12: raise ValueError('Transformação degenerada.')
    residuals=np.linalg.norm(a@m-b,axis=1)
    return {'matrix':m.tolist(),'residuals':residuals.tolist(),'rmse':float(np.sqrt(np.mean(residuals**2))),
            'method':'Afim, mínimos quadrados','control_count':len(controls),
            'note':'Resíduos nos controles de ajuste; não representam acurácia externa.'}


def transform(points, matrix):
    return (np.c_[np.asarray(points,float),np.ones(len(points))]@np.array(matrix)).tolist()
