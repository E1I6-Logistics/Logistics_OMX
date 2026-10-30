"""Inspection-only RGB features. Never change the camera or policy frame."""
import hashlib
import json

from .config import ConfigError,number

SIZE=(320,240)
DEFAULTS=dict(vision_method='legacy',roi_polygon=None,full_view_confirmed=False,
              local_pixel_threshold=6.,local_occupied_ratio=.02,min_component_pixels=12,
              edge_threshold=12.,contrast_clip_limit=2.,max_clipped_ratio=.15)


def validate(spec):
    if spec['vision_method'] not in ('legacy','rgb_roi'):raise ConfigError('Unknown unload.vision_method')
    if type(spec['full_view_confirmed']) is not bool:raise ConfigError('full_view_confirmed must be boolean')
    for key,low,high in (('local_pixel_threshold',2,30),('local_occupied_ratio',.005,.5),
                         ('edge_threshold',4,100),('contrast_clip_limit',1,4),('max_clipped_ratio',.01,.5)):
        number(spec[key],'unload.'+key,low,high)
    if type(spec['min_component_pixels']) is not int or not 4<=spec['min_component_pixels']<=1000:
        raise ConfigError('Invalid min_component_pixels')
    polygon=spec['roi_polygon']
    if polygon is not None:
        if spec['roi'] is None or not isinstance(polygon,list) or not 3<=len(polygon)<=12:
            raise ConfigError('roi_polygon requires ROI and 3..12 absolute image points')
        x,y,w,h=spec['roi']
        if any(not isinstance(p,list) or len(p)!=2 or any(type(v) is not int for v in p)
               or not x<=p[0]<x+w or not y<=p[1]<y+h for p in polygon):
            raise ConfigError('roi_polygon points must be inside ROI')
        import cv2
        import numpy as np
        contour=np.array(polygon,np.int32)
        if not cv2.isContourConvex(contour) or cv2.contourArea(contour)<64:
            raise ConfigError('roi_polygon must be a non-crossing convex basket interior')


def signature(spec):
    return hashlib.sha256(json.dumps(dict(version=2,size=SIZE,roi=spec['roi'],polygon=spec['roi_polygon']),sort_keys=True).encode()).hexdigest()


def mask_for(spec):
    import cv2
    import numpy as np
    mask=np.full((SIZE[1],SIZE[0]),255,np.uint8)
    if spec['roi_polygon'] is not None:
        x,y,w,h=spec['roi'];mask[:]=0
        points=np.array([[(px-x)*SIZE[0]/w,(py-y)*SIZE[1]/h] for px,py in spec['roi_polygon']],np.int32)
        cv2.fillConvexPoly(mask,points,255)
    if (mask!=0).sum()<256:raise ConfigError('Basket mask too small for reliable inspection')
    return mask!=0


class Comparator:
    """Cache reference CLAHE/edges; process one bounded RGB ROI per observation."""
    def __init__(self,reference,spec):
        import cv2
        import numpy as np
        self.spec=spec;self.mask=mask_for(spec)
        self.reference=reference.astype(np.int16)
        self.clahe=cv2.createCLAHE(clipLimit=spec['contrast_clip_limit'],tileGridSize=(8,8))
        self.gray,self.contrast,self.edges=self.features(reference)
        self.gray16=self.gray.astype(np.int16)
        self.contrast16=self.contrast.astype(np.int16)
        self.reference_mean=reference[self.mask].mean(axis=0)
        self.tile_areas=self.mask.reshape(12,20,16,20).sum(axis=(1,3))
        self.expanded_edges=cv2.dilate(self.edges,np.ones((3,3),np.uint8))
        self.reference_clipped=float((np.min(reference,axis=2)[self.mask]>=250).mean())

    def features(self,image):
        import cv2
        import numpy as np
        gray=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
        # Fill outside the basket before local contrast; background movement
        # must not alter histograms inside the inspection mask.
        working=gray.copy();working[~self.mask]=int(np.median(gray[self.mask]))
        contrast=self.clahe.apply(working)
        edges=cv2.Canny(working,self.spec['edge_threshold'],self.spec['edge_threshold']*3)
        return working,contrast,edges

    def compare(self,image):
        import cv2
        import numpy as np
        spec=self.spec
        raw=np.max(np.abs(image.astype(np.int16)-self.reference),axis=2)
        raw_ratio=float((raw[self.mask]>=spec['pixel_threshold']).mean())
        illumination=float(np.abs(image[self.mask].mean(axis=0)-self.reference_mean).max())
        clipped=max(self.reference_clipped,float((np.min(image,axis=2)[self.mask]>=250).mean()))
        # Reject unusable views before spending CPU on contrast/edge components.
        flags=[]
        if clipped>spec['max_clipped_ratio']:flags.append('ROI_OVEREXPOSED')
        if illumination>spec['illumination_limit']:flags.append('LIGHTING_SHIFT')
        if not spec['full_view_confirmed']:flags.append('FULL_VIEW_NOT_CONFIRMED')
        if flags:
            return dict(verdict='UNKNOWN',evidence_reason=flags[0],changed_ratio=raw_ratio,
                        local_peak_ratio=None,component_pixels=None,edge_changed_pixels=None,
                        supported_edge_pixels=None,color_component_pixels=None,features_computed=False,
                        quality_flags=flags,clipped_ratio=clipped,illumination_delta=illumination)
        gray,contrast,edges=self.features(image)
        gray_delta=np.abs(gray.astype(np.int16)-self.gray16)
        contrast_delta=np.abs(contrast.astype(np.int16)-self.contrast16)
        expanded=cv2.dilate(edges,np.ones((3,3),np.uint8))
        edge_change=((edges>0)&(self.expanded_edges==0))|((self.edges>0)&(expanded==0))
        # Canny threshold crossings in textured backgrounds are not independent
        # object evidence. Require an actual low-threshold RGB change nearby.
        color_support=(raw>=spec['local_pixel_threshold'])&self.mask
        supported_edges=edge_change&(cv2.dilate(color_support.astype(np.uint8),np.ones((3,3),np.uint8))>0)&self.mask
        weak_contrast=(contrast_delta>=12)&(gray_delta>=3)&self.mask
        sensitive=(color_support|weak_contrast|supported_edges)&self.mask
        count,_,stats,_=cv2.connectedComponentsWithStats(sensitive.astype(np.uint8),connectivity=8)
        component=int(stats[1:,cv2.CC_STAT_AREA].max()) if count>1 else 0
        tile_changes=sensitive.reshape(12,20,16,20).sum(axis=(1,3))
        ratios=np.divide(tile_changes,self.tile_areas,out=np.zeros_like(tile_changes,dtype=float),where=self.tile_areas>=40)
        peak=float(ratios.max())
        # Weak contrast/edges alone may veto EMPTY, but cannot assert OCCUPIED.
        direct_component=0
        if color_support.any():
            direct_count,_,direct_stats,_=cv2.connectedComponentsWithStats(color_support.astype(np.uint8),connectivity=8)
            if direct_count>1:direct_component=int(direct_stats[1:,cv2.CC_STAT_AREA].max())
        occupied=raw_ratio>=spec['occupied_ratio'] or (direct_component>=spec['min_component_pixels'] and peak>=spec['local_occupied_ratio'])
        reason='LOCAL_OBJECT_EVIDENCE' if occupied else 'MATCHES_EMPTY_REFERENCE'
        verdict='OCCUPIED' if occupied else 'EMPTY'
        if not occupied:
            if raw_ratio>spec['empty_ratio'] or component>=2 or sensitive.sum()>=4:
                verdict='UNKNOWN';reason='AMBIGUOUS_LOCAL_CHANGE'
            elif int((edge_change&self.mask).sum())>=4:
                verdict='UNKNOWN';reason='UNSUPPORTED_EDGE_CHANGE'
        return dict(verdict=verdict,evidence_reason=reason,changed_ratio=raw_ratio,
                    local_peak_ratio=peak,component_pixels=component,edge_changed_pixels=int((edge_change&self.mask).sum()),
                    supported_edge_pixels=int(supported_edges.sum()),color_component_pixels=direct_component,
                    features_computed=True,quality_flags=flags,clipped_ratio=clipped,illumination_delta=illumination)


def draw_region(frame,spec):
    """Return a separate preview; never draw onto a camera's shared array."""
    import cv2
    import numpy as np
    preview=cv2.cvtColor(frame,cv2.COLOR_RGB2BGR)
    if spec['roi'] is not None:
        x,y,w,h=spec['roi'];cv2.rectangle(preview,(x,y),(x+w-1,y+h-1),(0,180,255),2)
    if spec['roi_polygon'] is not None:cv2.polylines(preview,[np.array(spec['roi_polygon'],np.int32)],True,(255,80,255),2)
    return preview
