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
              registration_color_limit=6.,registration_wall_color_limit=12.,registration_partial_chroma_fallback=True,registration_full_chroma_fallback=True,
              registration_chroma_threshold=8.,registration_chroma_component_pixels=64,
              registration_still_px=3.,registration_allow_partial=False,
              registration_visible_confirmed=False,registration_min_visible_ratio=.65,registration_max_extrapolation=.35,registration_partial_component_pixels=64,registration_partial_color_limit=20.)

class ViewError(ConfigError):
    def __init__(self,reason,details=None):
        super().__init__(reason)
        self.details=details or {}



def validate(spec):
    if type(spec['registration_enabled']) is not bool:raise ConfigError('registration_enabled must be boolean')
    for name in ('registration_allow_partial','registration_visible_confirmed','registration_partial_chroma_fallback','registration_full_chroma_fallback'):
        if type(spec[name]) is not bool:raise ConfigError(name+' must be boolean')
    if spec['registration_allow_partial'] and not spec['registration_enabled']:raise ConfigError('Partial view requires registration')
    for key,lo,hi in (('registration_search_pad',0,150),('registration_inner_margin',2,8),
                      ('registration_component_pixels',8,128),('registration_weak_pixels',8,128),('registration_partial_component_pixels',16,192),('registration_chroma_component_pixels',16,256)):
        if type(spec[key]) is not int or not lo<=spec[key]<=hi:raise ConfigError('Invalid '+key)
    for key,lo,hi in (('registration_threshold_factor',1.05,1.8),('registration_min_area_ratio',.03,.4),
                      ('registration_max_shift_px',5,120),('registration_max_rotation_deg',1,25),
                      ('registration_scale_tolerance',.05,.4),('registration_pixel_threshold',4,20),
                      ('registration_weak_threshold',2,12),('registration_color_limit',1,12),('registration_still_px',.5,5),('registration_min_visible_ratio',.5,.95),('registration_max_extrapolation',.05,.5),('registration_partial_color_limit',1,32)):
        number(spec[key],key,lo,hi)
    number(spec['registration_wall_color_limit'],'registration_wall_color_limit',1,16)
    number(spec['registration_chroma_threshold'],'registration_chroma_threshold',4,24)
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
    # A near-top REAL corner can be below the old 12..18px "cut" band.
    # Use actual hull contacts with the sensor boundary to choose the TWO
    # cut endpoints; a third, real corner near the top is not another cut.
    points=hull[:,0].astype(np.float32)+origin
    contacts=points[points[:,1]<=3.5]
    endpoints=None
    if len(contacts)>=2 and np.ptp(contacts[:,0])>4:
        endpoints=contacts[[np.argmin(contacts[:,0]),np.argmax(contacts[:,0])]]
    last_reason='PARTIAL_GEOMETRY_UNCERTAIN';perimeter=cv2.arcLength(hull,True);measured_fallback=None
    for epsilon in (.015,.01,.005,.003,.02,.025,.03):
        q=cv2.approxPolyDP(hull,epsilon*perimeter,True)[:,0].astype(np.float32)+origin
        top=np.flatnonzero(q[:,1]<=3.5)
        if len(q)==4 and len(top)==1 and endpoints is None:
            # Barely touching the boundary: all four observed corners suffice.
            measured=ordered(q)
            try:describe(measured)
            except ViewError:continue
            if epsilon<=.005 and cv2.contourArea(measured)>=.995*cv2.contourArea(hull):
                measured_fallback=measured
            continue
        if len(q)!=5 or endpoints is None:continue
        near=np.flatnonzero(q[:,1]<=max(12.,spec['roi'][3]*.08))
        if len(near)<2:continue
        pairs=[(float(np.linalg.norm(q[i]-endpoints[0])+np.linalg.norm(q[j]-endpoints[1])),i,j)
               for i in near for j in near if i!=j and (int(i)-int(j))%5 in (1,4)]
        if not pairs:continue
        _,left,right=min(pairs);top=np.array([left,right])
        pairs=[]
        for index in top:
            neighbor=next((j for j in ((int(index)-1)%5,(int(index)+1)%5) if j not in top),None)
            if neighbor is None:break
            pairs.append((q[index],q[neighbor]))
        if len(pairs)!=2:continue
        a,b=pairs[0];c,d=pairs[1];u=b-a;v=d-c
        matrix=np.column_stack((u,-v));det=float(np.linalg.det(matrix))
        if abs(det)<20:continue
        intersection=a+np.linalg.solve(matrix,c-a)[0]*u
        if not np.isfinite(intersection).all() or intersection[1]>3.5:continue
        if -intersection[1]>spec['registration_max_extrapolation']*max(spec['roi'][2:]):
            last_reason='PARTIAL_EXTRAPOLATION_LIMIT';continue
        result=ordered(np.vstack(([q[j] for j in range(5) if j not in top],intersection)))
        try:describe(result)
        except ViewError:continue
        return result
    if measured_fallback is not None and last_reason!='PARTIAL_EXTRAPOLATION_LIMIT':return measured_fallback
    raise ViewError(last_reason)


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
        candidates=[];clipped=False;partial_rejections=[];rejections={};nearest=None
        def reject(reason,**detail):
            nonlocal nearest
            rejections[reason]=rejections.get(reason,0)+1
            if detail and (nearest is None or detail.get('shift_px',float('inf'))<nearest.get('shift_px',float('inf'))):
                nearest=dict(reason=reason,**detail)
        target=np.array(self.reference['quad'],np.float32).mean(0) if self.reference else np.array([x+w/2,y+h/2])
        for contour in contours:
            area=cv2.contourArea(contour)
            if not spec['registration_min_area_ratio']*w*h<=area<=1.6*w*h:
                reject('CONTOUR_AREA');continue
            points=contour[:,0]+[x0,y0]
            boundary=bool((points[:,0]<=3).any() or (points[:,1]<=3).any() or (points[:,0]>=width-4).any() or (points[:,1]>=height-4).any())
            search_cut=bool((contour[:,0,0]<=1).any() or (contour[:,0,1]<=1).any() or (contour[:,0,0]>=x1-x0-2).any() or (contour[:,0,1]>=y1-y0-2).any())
            partial=False
            if boundary or search_cut:
                clipped=True
                top_only=bool((points[:,1]<=3).any() and not (points[:,0]<=3).any()
                              and not (points[:,0]>=width-4).any() and not (points[:,1]>=height-4).any())
                if not spec['registration_allow_partial'] or not top_only:
                    reject('CLIPPED_CONTOUR');continue
                partial=True
            hull=cv2.convexHull(contour);hull_area=cv2.contourArea(hull)
            if not hull_area or area/hull_area<.7:
                reject('NONCONVEX_CONTOUR');continue
            if partial:
                try:q=top_partial_quad(hull,np.array([x0,y0]),spec)
                except ViewError as exc:
                    center=points.mean(0)
                    partial_rejections.append((float(np.linalg.norm(center-target)),str(exc)))
                    continue
            else:
                approx=cv2.approxPolyDP(hull,.02*cv2.arcLength(hull,True),True)
                if len(approx)!=4 or not cv2.isContourConvex(approx):
                    reject('NO_QUADRILATERAL');continue
                q=ordered(approx[:,0]+[x0,y0])
            try:info=describe(q)
            except ViewError:continue
            distance=float(np.linalg.norm(np.array(info['center'])-target))
            limit=spec['registration_max_shift_px']+(spec['registration_search_pad'] if partial and self.reference is None else 0)
            if distance>limit:
                reject('SHIFT_LIMIT',shift_px=distance,limit_px=limit,center=info['center']);continue
            if self.reference:
                original=describe(self.reference['quad']);scale=info['area']/original['area']
                angle=abs((info['angle']-original['angle']+180)%360-180)
                if not 1-spec['registration_scale_tolerance']<=scale<=1+spec['registration_scale_tolerance']:
                    reject('SCALE_LIMIT',shift_px=distance,area_scale=scale,center=info['center']);continue
                if angle>spec['registration_max_rotation_deg']:
                    reject('ROTATION_LIMIT',shift_px=distance,rotation_deg=angle,center=info['center']);continue
            candidates.append((distance,q,info,partial))
        candidates.sort(key=lambda row:row[0])
        if not candidates:
            if not getattr(self,'_threshold_retry',False) and (spec['registration_allow_partial'] or not clipped):
                # Scene brightness changes Otsu's histogram, including while
                # registering an empty full-view tray. Retry bounded analysis
                # thresholds against the SAME ROI center / saved pose limits.
                # No camera controls or source/model frames are changed.
                for delta in (.1,.2,-.1):
                    factor=spec['registration_threshold_factor']+delta
                    if not 1.05<=factor<=1.8:continue
                    retry=Tracker(dict(spec,registration_threshold_factor=factor),self.reference)
                    retry._threshold_retry=True;retry.pose_anchor=self.pose_anchor
                    try:result=retry.extract(image)
                    except ViewError:continue
                    self.last_profile=retry.last_profile;self.last_info=retry.last_info
                    self.valid_mask=retry.valid_mask;self.pose_anchor=retry.pose_anchor
                    self.last_info['registration_threshold_used']=factor
                    return result
            details=dict(otsu_threshold=float(threshold),threshold_factor=spec['registration_threshold_factor'],
                         search_bounds=[x0,y0,x1,y1],contours=len(contours),rejections=rejections,nearest_rejected=nearest)
            if partial_rejections:raise ViewError(min(partial_rejections)[1],details)
            raise ViewError('BASKET_CLIPPED' if clipped else 'BASKET_POSE_NOT_FOUND',details)
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
        self.lighting_anchors=[]
        if not spec['registration_allow_partial']:
            yy,xx=np.indices(self.mask.shape)
            visible=np.ones(self.mask.shape,bool) if reference_mask is None else reference_mask.astype(bool)
            usable=visible&(reference.min(2)>5)&(reference.max(2)<245)
            # Fixed, narrow WALL strips; do not use the cargo-bearing floor to
            # decide whether a box/shadow is a global illumination change.
            for region in ((yy>=2)&(yy<=5)&(xx>=16)&(xx<112),
                           (yy>=122)&(yy<=125)&(xx>=16)&(xx<112),
                           (xx>=2)&(xx<=5)&(yy>=16)&(yy<112),
                           (xx>=122)&(xx<=125)&(yy>=16)&(yy<112)):
                self.lighting_anchors.append(region&usable)

    def illumination_offset(self,signed,mask,valid_mask):
        import numpy as np
        interior=np.median(signed[mask],axis=0)
        offsets=[]
        for region in self.lighting_anchors:
            selected=region if valid_mask is None else region&valid_mask
            if selected.sum()>=64:offsets.append(np.median(signed[selected],axis=0))
        offset=interior;source='interior';sides=0
        if len(offsets)>=3:
            values=np.array(offsets);center=np.median(values,axis=0)
            matching=np.abs(values-center).max(1)<=3.
            if matching.sum()>=3:
                rim=np.median(values[matching],axis=0);sides=int(matching.sum())
                # Preserve the successful existing estimator normally. Use the
                # independent wall measurement only to disambiguate a rejected
                # floor offset, within the separate bounded wall limit.
                if abs(interior).max()>self.spec['registration_color_limit'] and abs(rim).max()<=self.spec['registration_wall_color_limit']:
                    offset=rim;source='rim_consensus'
        return offset,dict(interior_color_offset=interior.tolist(),lighting_offset_source=source,lighting_anchor_sides=sides)

    def chromatic_change(self,image,mask,rgb_offset=None):
        """Brightness-normalized color evidence; never an EMPTY authorization."""
        import cv2
        import numpy as np
        if not hasattr(self,'reference_chroma'):
            ref=self.reference.astype(np.float32)
            self.reference_chroma=ref/np.maximum(ref.sum(2,keepdims=True),1)*255
            self.reference_chroma_usable=ref.mean(2)>=self.spec['inspection_min_luma']
        current=image.astype(np.float32)
        usable=mask&self.reference_chroma_usable&(current.mean(2)>=self.spec['inspection_min_luma'])
        signed=current/np.maximum(current.sum(2,keepdims=True),1)*255-self.reference_chroma
        offset=np.median(signed[usable],axis=0) if usable.any() else np.zeros(3,np.float32)
        delta=np.abs(signed-offset).max(2)
        rgb_signed=image.astype(np.int16)-self.reference
        if rgb_offset is None:rgb_offset=np.median(rgb_signed[mask],axis=0)
        supported=np.abs(rgb_signed-rgb_offset).max(2)>=self.spec['registration_pixel_threshold']
        # Chromaticity changes mathematically with additive RGB shifts on a
        # shaded empty floor. Require local RGB evidence beyond that same
        # global shift as well; normalization artifacts cannot authorize motion.
        delta=np.where(supported,delta,0)
        active=((delta>=self.spec['registration_chroma_threshold'])&usable).astype(np.uint8)
        coherent=cv2.erode(active,np.ones((3,3),np.uint8))
        n,_,stats,_=cv2.connectedComponentsWithStats(coherent,connectivity=8)
        largest=int(stats[1:,cv2.CC_STAT_AREA].max()) if n>1 else 0
        return dict(chroma_component_pixels=largest,chroma_color_offset=offset.tolist(),
                    chroma_changed_ratio=float(active[mask].mean())),delta
    def compare(self,image,valid_mask=None):
        import cv2
        import numpy as np
        s=self.spec;mask=self.reference_mask.copy()
        if valid_mask is not None:mask&=valid_mask
        self.mask=mask
        signed=image.astype(np.int16)-self.reference
        if float(mask.sum()/self.reference_mask.sum())<s['registration_min_visible_ratio']:
            return dict(verdict='UNKNOWN',evidence_reason='INSUFFICIENT_COMMON_VIEW',changed_ratio=None,quality_flags=['INSUFFICIENT_COMMON_VIEW'],features_computed=False)
        offset,lighting=self.illumination_offset(signed,mask,valid_mask)
        clipped=max(self.reference_clipped,float((image.min(2)[mask]>=250).mean()))
        dark=max(self.reference_dark,float((image.max(2)[mask]<=5).mean()))
        flags=[]
        if s['registration_allow_partial']:
            if not s['registration_visible_confirmed']:flags.append('VISIBLE_VIEW_NOT_CONFIRMED')
        elif not s['full_view_confirmed']:flags.append('FULL_VIEW_NOT_CONFIRMED')
        if clipped>s['max_clipped_ratio']:flags.append('ROI_OVEREXPOSED')
        if dark>s['inspection_max_dark_ratio'] or min(float(self.reference_mean.mean()),float(image[mask].mean()))<s['inspection_min_luma']:flags.append('ROI_UNDEREXPOSED')
        limit=(s['registration_partial_color_limit'] if s['registration_allow_partial'] else
               s['registration_wall_color_limit'] if lighting['lighting_offset_source']=='rim_consensus' else s['registration_color_limit'])
        if float(abs(offset).max())>limit:flags.append('LIGHTING_SHIFT')
        result=dict(changed_ratio=0.,quality_flags=flags,clipped_ratio=clipped,illumination_delta=float(abs(offset).max()),
                    comparison='registered_low_template',view_scope='visible_only' if s['registration_allow_partial'] else 'full',
                    full_basket_verified=False,color_offset=offset.tolist(),features_computed=not bool(flags),**lighting)
        if flags:
            # Geometry/visibility/exposure still gate this path. A clear colored
            # object may prove OCCUPIED under shadow, but it never proves EMPTY.
            if flags==['LIGHTING_SHIFT'] and s['registration_partial_chroma_fallback' if s['registration_allow_partial'] else 'registration_full_chroma_fallback']:
                color,_=self.chromatic_change(image,mask,offset)
                if color['chroma_component_pixels']>=s['registration_chroma_component_pixels']:
                    return dict(result,**color,verdict='OCCUPIED',evidence_reason='REGISTERED_COLORED_OBJECT',
                                quality_flags=[],quality_warnings=['LIGHTING_SHIFT'],features_computed=True,
                                difference_basis='normalized_color',changed_ratio=color['chroma_changed_ratio'])
                return dict(result,**color,verdict='UNKNOWN',evidence_reason='LIGHTING_SHIFT')
            return dict(result,verdict='UNKNOWN',evidence_reason=flags[0])
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
    evidence=comparator.compare(current,current_mask)
    offset=np.asarray(evidence.get('color_offset',[0,0,0]))
    delta=np.abs(current.astype(np.int16)-reference-offset).max(2)
    threshold=spec['registration_pixel_threshold'];title='REGISTERED DIFFERENCE'
    if evidence.get('difference_basis')=='normalized_color':
        _,delta=comparator.chromatic_change(current,comparator.mask,offset)
        threshold=spec['registration_chroma_threshold'];title='COLOR DIFFERENCE'
    heat=cv2.cvtColor(np.clip(delta*12,0,255).astype(np.uint8),cv2.COLOR_GRAY2BGR)
    heat[(delta>=threshold)&comparator.mask]=(0,0,255)
    panels=[cv2.cvtColor(reference,cv2.COLOR_RGB2BGR),cv2.cvtColor(current,cv2.COLOR_RGB2BGR),heat]
    canvas=np.zeros((288,768,3),np.uint8)
    for i,(panel,title) in enumerate(zip(panels,('REGISTERED REFERENCE','REGISTERED CURRENT',title))):
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
