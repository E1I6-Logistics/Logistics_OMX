"""Opt-in presence of known COLOURED products on a white fixed pallet.

This mode does not verify absence of arbitrary white/achromatic products.
No reference learning, sensor controls, or full-resolution work occurs here.
"""
import cv2
import numpy as np


def component(mask):
    count,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
    return int(stats[1:,cv2.CC_STAT_AREA].max()) if count>1 else 0


def features(image,threshold):
    a=image.astype(np.float32)
    value=a.max(axis=2)
    if float(np.percentile(value,90))<60:raise ValueError('팔레트 영상이 너무 어두움')
    saturation=(value-a.min(axis=2))/np.maximum(value,1)
    bright=value>=np.percentile(value,65)
    white_pixels=bright&(saturation<=np.percentile(saturation[bright],50))
    white=np.median(a[white_pixels],axis=0)
    if min(white)<40 or max(white)/min(white)>2:raise ValueError('흰 팔레트 색 기준 확인 불가')
    corrected=a/white
    value=corrected.max(axis=2)
    saturation=(value-corrected.min(axis=2))/np.maximum(value,.01)
    plastic=((value>.65)&(saturation<.12)).astype(np.uint8)
    count,labels,stats,_=cv2.connectedComponentsWithStats(plastic,8)
    if count<=1:raise ValueError('흰 팔레트 몸체 확인 불가')
    largest=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
    if stats[largest,cv2.CC_STAT_AREA]<image.shape[0]*image.shape[1]*.08:raise ValueError('흰 팔레트 몸체 근거 부족')
    points=cv2.findNonZero(np.uint8(labels==largest))
    hull=np.zeros(value.shape,np.uint8)
    cv2.fillConvexPoly(hull,cv2.convexHull(points),1)
    hull=cv2.erode(hull,np.ones((3,3),np.uint8))
    mask=((saturation>threshold)&(value>.25)&(hull>0)).astype(np.uint8)
    # Filter narrow lattice holes; this fixed 5px filter requires small-product
    # examples during commissioning, and is never enabled automatically.
    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((5,5),np.uint8))
    gray=cv2.cvtColor(corrected,cv2.COLOR_RGB2GRAY)
    energy=cv2.magnitude(cv2.Sobel(gray,cv2.CV_32F,1,0,ksize=3),cv2.Sobel(gray,cv2.CV_32F,0,1,ksize=3))
    usable=hull.astype(bool)
    texture=float(np.percentile(energy[usable],90))
    return dict(pixels=component(mask),ratio=float(mask.sum()/max(1,hull.sum())),
        hull_ratio=float(hull.mean()),texture=texture,white=white.tolist())


def compare(current,reference,spec):
    metadata=dict(comparison='fixed_coloured_product',quality_flags=[],features_computed=False)
    clipped=float(np.mean(current.min(axis=2)>=250));ref_clipped=float(np.mean(reference.min(axis=2)>=250))
    metadata.update(clipped_ratio=clipped,reference_clipped_ratio=ref_clipped)
    if clipped>max(.45,ref_clipped+.1):return dict(metadata,candidate='UNKNOWN',reason='과노출 픽셀 비율 증가',quality_flags=['OVEREXPOSED'])
    try:
        before=features(reference,spec['color_threshold']);after=features(current,spec['color_threshold'])
    except ValueError as exc:return dict(metadata,candidate='UNKNOWN',reason=str(exc))
    texture_ratio=after['texture']/max(.01,before['texture'])
    metadata.update(color_component_pixels=after['pixels'],reference_color_component_pixels=before['pixels'],
        changed_ratio=after['ratio'],white_balance_anchor=after['white'],texture_ratio=texture_ratio,
        hull_ratio=after['hull_ratio'],reference_hull_ratio=before['hull_ratio'])
    if before['texture']<.2 or texture_ratio<.4 or abs(after['hull_ratio']-before['hull_ratio'])>.25:
        return dict(metadata,candidate='UNKNOWN',reason='팔레트 가림·격자·시야 확인 필요',quality_flags=['PALLET_QUALITY'])
    if before['pixels']>=spec['color_min_component_pixels']:
        return dict(metadata,candidate='UNKNOWN',reason='빈 기준에 물품 색상 근거가 큼: 색상 모드 적용 불가')
    pixels=after['pixels']
    if pixels>=spec['color_min_component_pixels']:candidate,reason='PRESENT','흰 팔레트 내부의 고정 물품 색상 확인'
    elif pixels<=spec['color_empty_component_pixels']:candidate,reason='EMPTY','고정 유색 물품의 색상 근거 없음·팔레트 품질 확인'
    else:candidate,reason='UNKNOWN','유색 잔여 영역이 빈 상태와 물품 기준 사이'
    return dict(metadata,candidate=candidate,reason=reason,features_computed=True,
                component_pixels=pixels,weak_component_pixels=pixels,grid_occlusion_pixels=0)
