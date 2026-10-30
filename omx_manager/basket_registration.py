"""Opt-in tray registration for inspection only. No neural model or motor imports."""
import math
from .config import ConfigError,number

SIZE=(128,128)
DEFAULTS=dict(registration_enabled=False,registration_search_pad=60,
              registration_threshold_factor=1.35,registration_min_area_ratio=.1,
              registration_max_shift_px=60.,registration_max_rotation_deg=15.,
              registration_scale_tolerance=.25,registration_inner_margin=6,
              registration_pixel_threshold=8.,registration_weak_threshold=6.,
              registration_component_pixels=32,registration_weak_pixels=32,
              registration_color_limit=6.,registration_still_px=3.,registration_allow_partial=False,
              registration_visible_confirmed=False,registration_min_visible_ratio=.65,registration_max_extrapolation=.35,registration_partial_component_pixels=64,registration_partial_color_limit=20.)

class ViewError(ConfigError):pass


def validate(spec):
    if type(spec['registration_enabled']) is not bool:raise ConfigError('registration_enabled must be boolean')
    for name in ('registration_allow_partial','registration_visible_confirmed'):
        if type(spec[name]) is not bool:raise ConfigError(name+' must be boolean')
    if spec['registration_allow_partial'] and not spec['registration_enabled']:raise ConfigError('Partial view requires registration')
    for key,lo,hi in (('registration_search_pad',0,150),('registration_inner_margin',2,8),
                      ('registration_component_pixels',8,128),('registration_weak_pixels',8,128),('registration_partial_component_pixels',16,192)):
        if type(spec[key]) is not int or not lo<=spec[key]<=hi:raise ConfigError('Invalid '+key)
    for key,lo,hi in (('registration_threshold_factor',1.05,1.8),('registration_min_area_ratio',.03,.4),
                      ('registration_max_shift_px',5,120),('registration_max_rotation_deg',1,25),
                      ('registration_scale_tolerance',.05,.4),('registration_pixel_threshold',4,20),
                      ('registration_weak_threshold',2,12),('registration_color_limit',1,12),('registration_still_px',.5,5),('registration_min_visible_ratio',.5,.95),('registration_max_extrapolation',.05,.5),('registration_partial_color_limit',1,32)):
        number(spec[key],key,lo,hi)
    if spec['registration_weak_threshold']>spec['registration_pixel_threshold']:raise ConfigError('Weak threshold must not exceed strong threshold')
    if spec['registration_enabled'] and (not spec['low_exposure_enabled'] or spec['vision_method']!='low_template'):
        raise ConfigError('Tray registration requires inspection-only low_template')
    if spec['registration_enabled'] and spec['roi_polygon'] is not None:
        raise ConfigError('Clear the fixed polygon before enabling moving-tray registration')


def ordered(points):
    import numpy as np
    p=points.reshape(-1,2).astype(np.float32)
    if len(p)!=4 or len(np.unique(p,axis=0))!=4:raise ViewError('BASKET_GEOMETRY_AMBIGUOUS')
    center=p.mean(0);angles=np.arctan2(p[:,1]-center[1],p[:,0]-center[0]);q=p[np.argsort(angles)]
    return np.roll(q,-int(np.argmin(q[:,1])),axis=0)


def describe(quad):
    import cv2
    import numpy as np
    q=np.asarray(quad,np.float32)
    lengths=np.linalg.norm(np.roll(q,-1,axis=0)-q,axis=1)
    if min(lengths)<20 or max(lengths)/min(lengths)>2.5:raise ViewError('BASKET_SHAPE_UNCERTAIN')
    for i in range(4):
        a=q[(i-1)%4]-q[i];b=q[(i+1)%4]-q[i]
        cosine=float(np.dot(a,b)/(np.linalg.norm(a)*np.linalg.norm(b)))
        if abs(cosine)>.8:raise ViewError('BASKET_SHAPE_UNCERTAIN')
    return dict(center=q.mean(0).tolist(),area=float(cv2.contourArea(q)),
                angle=math.degrees(math.atan2(float(q[1,1]-q[0,1]),float(q[1,0]-q[0,0]))))


def validate_profile(data,spec,shape):
    import numpy as np
    if not isinstance(data,dict) or not {'version','quad','frame_shape'}<=set(data) or set(data)-{'version','quad','frame_shape','partial'} or data['version']!=1:
        raise ConfigError('Register a new moving-tray empty reference: omx_basket capture')
    if data['frame_shape']!=list(shape):raise ConfigError('Registration camera image shape changed')
    try:q=np.asarray(data['quad'],np.float32)
    except (ValueError,TypeError):raise ConfigError('Invalid registered tray geometry')
    if q.shape!=(4,2) or not np.isfinite(q).all():raise ConfigError('Invalid registered tray geometry')
    h,w=shape
    partial=data.get('partial',False)
    if type(partial) is not bool or partial and not spec['registration_allow_partial']:raise ConfigError('Partial reference requires visible-only mode')
    if partial:
        limit=spec['registration_max_extrapolation']*max(spec['roi'][2:])
        if (q[:,0]<-limit).any() or (q[:,1]<-limit).any() or (q[:,0]>w+limit).any() or (q[:,1]>h+limit).any():raise ConfigError('Reference extrapolation exceeds limit')
    elif (q<3).any() or (q[:,0]>=w-3).any() or (q[:,1]>=h-3).any():raise ConfigError('Registered basket is clipped')
    describe(q)
    if not np.allclose(q,ordered(q)):raise ConfigError('Invalid tray corner order')


def top_partial_quad(hull,origin,spec):
    """Only extrapolate a single top cut from two observed side segments.

    The virtual corner is geometry, never evidence of an unseen empty region.
    Unsupported cuts/occlusions remain UNKNOWN.
    """
    import cv2
    import numpy as np
    q=cv2.approxPolyDP(hull,.015*cv2.arcLength(hull,True),True)[:,0].astype(np.float32)+origin
    if len(q)!=5:raise ViewError('PARTIAL_GEOMETRY_UNCERTAIN')
    top=np.flatnonzero(q[:,1]<=max(12.,spec['roi'][3]*.08))
    if len(top)!=2:raise ViewError('PARTIAL_GEOMETRY_UNCERTAIN')
    pairs=[]
    for index in top:
        neighbor=next((j for j in ((int(index)-1)%5,(int(index)+1)%5) if j not in top),None)
        if neighbor is None:raise ViewError('PARTIAL_GEOMETRY_UNCERTAIN')
        pairs.append((q[index],q[neighbor]))
    a,b=pairs[0];c,d=pairs[1];u=b-a;v=d-c
    matrix=np.column_stack((u,-v));det=float(np.linalg.det(matrix))
    if abs(det)<20:raise ViewError('PARTIAL_GEOMETRY_UNCERTAIN')
    intersection=a+np.linalg.solve(matrix,c-a)[0]*u
    if not np.isfinite(intersection).all() or intersection[1]>12:raise ViewError('PARTIAL_GEOMETRY_UNCERTAIN')
    if -intersection[1]>spec['registration_max_extrapolation']*max(spec['roi'][2:]):raise ViewError('PARTIAL_EXTRAPOLATION_LIMIT')
    return ordered(np.vstack(([q[j] for j in range(5) if j not in top],intersection)))


class Tracker:
    def __init__(self,spec,reference=None):
        self.spec=spec;self.reference=reference;self.last_profile=None;self.last_info={};self.pose_anchor=None
    def extract(self,image):
        import cv2
        import numpy as np
        spec=self.spec
        if not isinstance(image,np.ndarray) or image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2]!=3:
            raise ViewError('INVALID_CAMERA_IMAGE')
        height,width=image.shape[:2]
        if self.reference:validate_profile(self.reference,spec,(height,width))
        if spec['roi'] is None:raise ViewError('BASKET_SEARCH_ROI_REQUIRED')
        x,y,w,h=spec['roi'];pad=spec['registration_search_pad']
        x0,y0=max(0,x-pad),max(0,y-pad);x1,y1=min(width,x+w+pad),min(height,y+h+pad)
        gray=cv2.GaussianBlur(cv2.cvtColor(image[y0:y1,x0:x1],cv2.COLOR_RGB2GRAY),(5,5),0)
        threshold,_=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        if threshold<4:raise ViewError('BASKET_CONTRAST_TOO_LOW')
        binary=(gray>=min(254,threshold*spec['registration_threshold_factor'])).astype(np.uint8)*255
        contours=cv2.findContours(binary,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
        candidates=[];clipped=False
        target=np.array(self.reference['quad'],np.float32).mean(0) if self.reference else np.array([x+w/2,y+h/2])
        for contour in contours:
            area=cv2.contourArea(contour)
            if not spec['registration_min_area_ratio']*w*h<=area<=1.6*w*h:continue
            points=contour[:,0]+[x0,y0]
            boundary=bool((points[:,0]<=3).any() or (points[:,1]<=3).any() or (points[:,0]>=width-4).any() or (points[:,1]>=height-4).any())
            search_cut=bool((contour[:,0,0]<=1).any() or (contour[:,0,1]<=1).any() or (contour[:,0,0]>=x1-x0-2).any() or (contour[:,0,1]>=y1-y0-2).any())
            partial=False
            if boundary or search_cut:
                clipped=True
                top_only=bool((points[:,1]<=3).any() and not (points[:,0]<=3).any()
                              and not (points[:,0]>=width-4).any() and not (points[:,1]>=height-4).any())
                if not spec['registration_allow_partial'] or not top_only:continue
                partial=True
            hull=cv2.convexHull(contour);hull_area=cv2.contourArea(hull)
            if not hull_area or area/hull_area<.7:continue
            if partial:
                try:q=top_partial_quad(hull,np.array([x0,y0]),spec)
                except ViewError:continue
            else:
                approx=cv2.approxPolyDP(hull,.02*cv2.arcLength(hull,True),True)
                if len(approx)!=4 or not cv2.isContourConvex(approx):continue
                q=ordered(approx[:,0]+[x0,y0])
            try:info=describe(q)
            except ViewError:continue
            distance=float(np.linalg.norm(np.array(info['center'])-target))
            limit=spec['registration_max_shift_px']+(spec['registration_search_pad'] if partial and self.reference is None else 0)
            if distance>limit:continue
            if self.reference:
                original=describe(self.reference['quad']);scale=info['area']/original['area']
                angle=abs((info['angle']-original['angle']+180)%360-180)
                if not 1-spec['registration_scale_tolerance']<=scale<=1+spec['registration_scale_tolerance']:continue
                if angle>spec['registration_max_rotation_deg']:continue
            candidates.append((distance,q,info,partial))
        candidates.sort(key=lambda row:row[0])
        if not candidates:raise ViewError('BASKET_CLIPPED' if clipped else 'BASKET_POSE_NOT_FOUND')
        if len(candidates)>1 and candidates[1][0]-candidates[0][0]<15:raise ViewError('MULTIPLE_BASKET_CANDIDATES')
        _,q,info,partial=candidates[0]
        destination=np.array([[0,0],[127,0],[127,127],[0,127]],np.float32)
        transform=cv2.getPerspectiveTransform(q,destination)
        # Only this copied analysis image is transformed. Shared policy RGB stays untouched.
        canonical=cv2.warpPerspective(image,transform,SIZE,flags=cv2.INTER_LINEAR)
        canonical=cv2.GaussianBlur(canonical,(5,5),0)
        valid=cv2.warpPerspective(np.full((height,width),255,np.uint8),transform,SIZE,flags=cv2.INTER_NEAREST)
        self.valid_mask=cv2.erode(valid,np.ones((5,5),np.uint8))>0
        visible=float(self.valid_mask.mean())
        if visible<spec['registration_min_visible_ratio']:raise ViewError('INSUFFICIENT_VISIBLE_BASKET')
        self.last_profile=dict(version=1,quad=q.tolist(),frame_shape=[height,width],partial=partial)
        stable=self.pose_anchor is not None and float(np.linalg.norm(q-self.pose_anchor,axis=1).max())<=spec['registration_still_px']
        if not stable:self.pose_anchor=q.copy()
        self.last_info=dict(registration='POSE_FOUND',pose_stable=stable,tray_quad=q.tolist(),tray_center=info['center'],tray_angle_deg=info['angle'],visible_ratio=visible,
                            view_scope='visible_only' if spec['registration_allow_partial'] else 'full',full_basket_verified=False)
        return canonical


class Comparator:
    def __init__(self,reference,spec,reference_mask=None):
        import numpy as np
        self.spec=spec;self.reference=reference.astype(np.int16);self.mask=np.zeros(SIZE[::-1],bool)
        margin=spec['registration_inner_margin'];self.mask[margin:-margin,margin:-margin]=True
        if reference_mask is not None:
            if reference_mask.shape!=self.mask.shape:raise ConfigError('Registration visibility mask shape mismatch')
            self.mask&=reference_mask.astype(bool)
        if self.mask.mean()<spec['registration_min_visible_ratio']*.7:raise ConfigError('Too little registered reference visible')
        self.reference_mask=self.mask.copy()
        self.reference_mean=reference[self.mask].mean(0)
        self.reference_clipped=float((reference.min(2)[self.mask]>=250).mean())
        self.reference_dark=float((reference.max(2)[self.mask]<=5).mean())
    def compare(self,image,valid_mask=None):
        import cv2
        import numpy as np
        s=self.spec;mask=self.reference_mask.copy()
        if valid_mask is not None:mask&=valid_mask
        self.mask=mask
        signed=image.astype(np.int16)-self.reference
        if float(mask.sum()/self.reference_mask.sum())<s['registration_min_visible_ratio']:
            return dict(verdict='UNKNOWN',evidence_reason='INSUFFICIENT_COMMON_VIEW',changed_ratio=None,quality_flags=['INSUFFICIENT_COMMON_VIEW'],features_computed=False)
        offset=np.median(signed[mask],axis=0)
        clipped=max(self.reference_clipped,float((image.min(2)[mask]>=250).mean()))
        dark=max(self.reference_dark,float((image.max(2)[mask]<=5).mean()))
        flags=[]
        if s['registration_allow_partial']:
            if not s['registration_visible_confirmed']:flags.append('VISIBLE_VIEW_NOT_CONFIRMED')
        elif not s['full_view_confirmed']:flags.append('FULL_VIEW_NOT_CONFIRMED')
        if clipped>s['max_clipped_ratio']:flags.append('ROI_OVEREXPOSED')
        if dark>s['inspection_max_dark_ratio'] or min(float(self.reference_mean.mean()),float(image[mask].mean()))<s['inspection_min_luma']:flags.append('ROI_UNDEREXPOSED')
        if float(abs(offset).max())>(s['registration_partial_color_limit'] if s['registration_allow_partial'] else s['registration_color_limit']):flags.append('LIGHTING_SHIFT')
        result=dict(changed_ratio=0.,quality_flags=flags,clipped_ratio=clipped,illumination_delta=float(abs(offset).max()),
                    comparison='registered_low_template',view_scope='visible_only' if s['registration_allow_partial'] else 'full',
                    full_basket_verified=False,color_offset=offset.tolist(),features_computed=not bool(flags))
        if flags:return dict(result,verdict='UNKNOWN',evidence_reason=flags[0])
        raw=np.abs(signed-offset).max(2)
        strong=((raw>=s['registration_pixel_threshold'])&mask).astype(np.uint8)
        weak=((raw>=s['registration_weak_threshold'])&mask).astype(np.uint8)
        def component(binary):
            coherent=cv2.erode(binary,np.ones((3,3),np.uint8))
            n,_,stats,_=cv2.connectedComponentsWithStats(coherent,connectivity=8)
            return int(stats[1:,cv2.CC_STAT_AREA].max()) if n>1 else 0
        largest=component(strong);faint=component(weak)
        occupied=largest>=(s['registration_partial_component_pixels'] if s['registration_allow_partial'] else s['registration_component_pixels'])
        uncertain=faint>=s['registration_weak_pixels']
        verdict='OCCUPIED' if occupied else 'UNKNOWN' if uncertain else 'EMPTY'
        return dict(result,verdict=verdict,evidence_reason='REGISTERED_OBJECT_CHANGE' if occupied else 'REGISTERED_FAINT_CHANGE' if uncertain else 'REGISTERED_EMPTY_MATCH',
                    changed_ratio=float(strong[mask].mean()),component_pixels=largest,weak_component_pixels=faint)


def comparison_view(reference,current,spec,reference_mask=None,current_mask=None):
    import cv2
    import numpy as np
    comparator=Comparator(reference,spec,reference_mask)
    evidence=comparator.compare(current,current_mask);offset=np.median((current.astype(np.int16)-reference)[comparator.mask],axis=0)
    delta=np.abs(current.astype(np.int16)-reference-offset).max(2)
    heat=cv2.cvtColor(np.clip(delta*12,0,255).astype(np.uint8),cv2.COLOR_GRAY2BGR)
    heat[(delta>=spec['registration_pixel_threshold'])&comparator.mask]=(0,0,255)
    panels=[cv2.cvtColor(reference,cv2.COLOR_RGB2BGR),cv2.cvtColor(current,cv2.COLOR_RGB2BGR),heat]
    canvas=np.zeros((288,768,3),np.uint8)
    for i,(panel,title) in enumerate(zip(panels,('REGISTERED REFERENCE','REGISTERED CURRENT','REGISTERED DIFFERENCE'))):
        canvas[32:,i*256:(i+1)*256]=cv2.resize(panel,(256,256),interpolation=cv2.INTER_NEAREST)
        cv2.putText(canvas,title,(i*256+4,21),cv2.FONT_HERSHEY_SIMPLEX,.4,(255,255,255),1)
    return canvas,evidence


def pose_view(image,spec,profile):
    import cv2
    import numpy as np
    q=np.array(profile['quad'],np.float32)
    canvas=cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
    cv2.polylines(canvas,[q.astype(np.int32)],True,(0,255,0),2)
    margin=spec['registration_inner_margin']
    corners=np.array([[0,0],[127,0],[127,127],[0,127]],np.float32)
    inner=np.array([[[margin,margin],[127-margin,margin],[127-margin,127-margin],[margin,127-margin]]],np.float32)
    projected=cv2.perspectiveTransform(inner,cv2.getPerspectiveTransform(corners,q))[0]
    cv2.polylines(canvas,[projected.astype(np.int32)],True,(0,180,255),1)
    cv2.putText(canvas,'GREEN: TRAY / ORANGE: ANALYSIS',(12,24),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,255,255),1)
    if spec['registration_allow_partial']:
        cv2.putText(canvas,'VISIBLE ONLY - UNSEEN REGION UNVERIFIED',(12,46),cv2.FONT_HERSHEY_SIMPLEX,.48,(0,180,255),1)
    return canvas
