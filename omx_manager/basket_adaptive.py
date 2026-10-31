"""Bounded background consensus and spatially tolerant RGB comparison."""


def prepare(comparator):
    import cv2
    import numpy as np
    s=comparator.spec;r=s['registration_spatial_tolerance'];kernel=np.ones((2*r+1,2*r+1),np.uint8)
    ref=comparator.reference.astype(np.float32);valid=comparator.reference_mask
    comparator._adaptive_lo=cv2.erode(np.where(valid[:,:,None],ref,255.),kernel)
    comparator._adaptive_hi=cv2.dilate(np.where(valid[:,:,None],ref,0.),kernel)
    gray=cv2.cvtColor(ref,cv2.COLOR_RGB2GRAY)
    comparator._adaptive_flat=(cv2.dilate(gray,np.ones((3,3),np.uint8))-cv2.erode(gray,np.ones((3,3),np.uint8)))<3.
    comparator._adaptive_edge_zone=cv2.dilate((~comparator._adaptive_flat).astype(np.uint8),kernel)>0


def evidence(comparator,image,mask,offset,lighting):
    import cv2
    import numpy as np
    s=comparator.spec
    if not hasattr(comparator,'_adaptive_lo'):prepare(comparator)
    current=image.astype(np.float32);signed=current-comparator.reference
    gray=cv2.cvtColor(current,cv2.COLOR_RGB2GRAY)
    flat=(cv2.dilate(gray,np.ones((3,3),np.uint8))-cv2.erode(gray,np.ones((3,3),np.uint8)))<3.
    usable=mask&flat&comparator._adaptive_flat
    values=[];cells=[];quadrants=[]
    for y in range(4):
        for x in range(4):
            ys=slice(y*32,(y+1)*32);xs=slice(x*32,(x+1)*32)
            selected=usable[ys,xs]
            if selected.sum()<64:continue
            values.append(np.median(signed[ys,xs][selected],axis=0));cells.append((ys,xs,selected));quadrants.append((y//2,x//2))
    consensus=False;support=0;noise=0.
    if values:
        values=np.asarray(values)
        distances=np.abs(values[:,None,:]-values[None,:,:]).max(2)
        center=values[np.argmax((distances<=s['registration_background_tolerance']).sum(1))]
        agree=np.abs(values-center).max(1)<=s['registration_background_tolerance']
        support=int(agree.sum())
        consensus=(support>=s['registration_background_min_tiles'] and support>=s['registration_background_fraction']*len(values)
                   and len({q for i,q in enumerate(quadrants) if agree[i]})>=3)
        if consensus:
            background=np.zeros(mask.shape,bool)
            for i,(ys,xs,selected) in enumerate(cells):
                if agree[i]:background[ys,xs]|=selected
            estimate=np.median(values[agree],axis=0)
            noise=float(1.4826*np.median(np.abs(signed[background]-estimate)))
            # Keep the successful original estimate normally; independent
            # spatial support may establish a bounded illumination correction.
            if abs(offset).max()>s['registration_color_limit'] and lighting['lighting_offset_source']=='interior':
                offset=estimate;lighting=dict(lighting,lighting_offset_source='background_consensus')
    adjusted=current-offset
    delta=np.maximum(comparator._adaptive_lo-adjusted,adjusted-comparator._adaptive_hi).max(2)
    delta=np.maximum(delta,0.)
    # Spatial freedom is only for an existing reference edge. Do not explain
    # away a faint object on the plain floor as an arbitrary neighboring pixel.
    delta=np.where(comparator._adaptive_edge_zone,delta,np.abs(adjusted-comparator.reference).max(2))
    threshold=max(s['registration_pixel_threshold'],min(s['registration_pixel_threshold']*1.5,3*noise+2.))
    # A narrow wall stripe may be long, but is not a solid 2cm object. Opening
    # removes its thin core and restores real object area for size thresholds.
    kernel=np.ones((s['registration_min_blob_width'],)*2,np.uint8)
    strong=cv2.morphologyEx(((delta>=threshold)&mask).astype(np.uint8),cv2.MORPH_OPEN,kernel)
    weak=cv2.morphologyEx(((delta>=s['registration_weak_threshold'])&mask).astype(np.uint8),cv2.MORPH_OPEN,kernel)
    def component(binary):
        n,_,stats,_=cv2.connectedComponentsWithStats(binary,connectivity=8)
        return int(stats[1:,cv2.CC_STAT_AREA].max()) if n>1 else 0
    comparator.last_delta=delta;comparator.last_strong_mask=strong.astype(bool)
    extra=dict(background_consensus=consensus,background_tiles=support,background_eligible_tiles=len(values),
               background_noise=noise,effective_pixel_threshold=float(threshold),
               spatial_tolerance=s['registration_spatial_tolerance'],difference_basis='spatial_rgb')
    return offset,lighting,extra,delta,component(strong),component(weak)
