import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf as fitz, cv2, numpy as np
from shapely.geometry import Polygon,Point
p=fitz.open('exemplos_plantas/Projeto Urbanistico (A0)_877 (1).pdf')[0]
pix=p.get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
im=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3).copy()
gray=cv2.cvtColor(im,cv2.COLOR_RGB2GRAY)
mask=(gray<170).astype(np.uint8)*255
mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((2,2),np.uint8))
cs,_=cv2.findContours(mask,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
labels=[w for w in p.get_text('words') if w[4].isdigit() and len(w[4])==2 and w[0]<2830 and not (w[0]<960 and w[1]>1190)]
found=[]
for c in cs:
    a=cv2.contourArea(c)
    if not 500<a<25000: continue
    ring=cv2.approxPolyDP(c,1,True).reshape(-1,2).tolist()
    poly=Polygon(ring)
    if not poly.is_valid: continue
    inside=[w for w in labels if poly.contains(Point((w[0]+w[2])/2,(w[1]+w[3])/2))]
    if len(inside)==1:
        found.append((ring,inside[0][4],a))
        cv2.polylines(im,[np.array(ring)],True,(0,150,220),2)
print('labels',len(labels),'contours',len(cs),'candidates',len(found))
cv2.imwrite('segments_preview.jpg',cv2.cvtColor(cv2.resize(im,None,fx=.45,fy=.45),cv2.COLOR_RGB2BGR))

