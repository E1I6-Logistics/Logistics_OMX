"""Observe basket-rim pixels instead of treating noisy virtual corners as motion."""


def observe(tracker,gray,quad,origin):
    import cv2
    import numpy as np
    previous=getattr(tracker,'_previous_gray',None)
    rim=getattr(tracker,'_previous_rim',None);edges=getattr(tracker,'_previous_edges',None)
    score=None;stable=None
    if previous is not None and previous.shape==gray.shape and edges is not None and edges.sum()>=64:
        delta=gray.astype(np.int16)-previous
        shift=float(np.median(delta[rim]))
        score=float(np.abs(delta[edges]-shift).mean())
        stable=score<=tracker.spec.get('stability_delta',3.)
    q=quad-np.asarray(origin);center=q.mean(0)
    rim=np.zeros(gray.shape,np.uint8)
    cv2.fillConvexPoly(rim,np.round((q-center)*1.02+center).astype(np.int32),1)
    cv2.fillConvexPoly(rim,np.round((q-center)*.75+center).astype(np.int32),0)
    gradient=cv2.morphologyEx(gray,cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8))
    edges=(gradient>=4)&(rim>0)
    yy,xx=np.nonzero(edges)
    if len(xx) and (np.ptp(xx)<np.ptp(q[:,0])*.4 or np.ptp(yy)<np.ptp(q[:,1])*.4):edges[:]=False
    tracker._previous_gray=gray.astype(np.int16);tracker._previous_rim=rim>0;tracker._previous_edges=edges
    return dict(observed_rim_stable=stable,rim_motion_delta=score,rim_motion_pixels=int(edges.sum()))
