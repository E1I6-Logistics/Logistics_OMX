"""Optional inspection-only UVC autofocus transaction; no motor interaction."""
import math


def snapshot(adapter):
    if adapter.backend!='opencv-v4l2' or not hasattr(adapter,'cap'):return None
    import cv2
    with adapter.access():
        auto=float(adapter.cap.get(cv2.CAP_PROP_AUTOFOCUS))
        position=float(adapter.cap.get(cv2.CAP_PROP_FOCUS))
    # Unsupported OpenCV controls commonly report zero. Only touch an observed,
    # active autofocus loop; already manual/fixed-focus cameras need no change.
    if auto!=1. or not math.isfinite(position) or not 0<=position<=10000:return None
    return dict(auto_focus=auto,position=position)


def freeze(adapter):
    import cv2
    with adapter.access():
        if not adapter.cap.set(cv2.CAP_PROP_AUTOFOCUS,0.) or adapter.cap.get(cv2.CAP_PROP_AUTOFOCUS)!=0.:
            raise RuntimeError('Cannot freeze active camera autofocus for inspection')
        value=float(adapter.cap.get(cv2.CAP_PROP_FOCUS))
        if not math.isfinite(value) or not 0<=value<=10000:raise RuntimeError('Invalid inspection focus readback')
        return dict(auto_focus=0.,position=value)


def locked(adapter,target,*,position=True):
    import cv2
    with adapter.access():
        if adapter.cap.get(cv2.CAP_PROP_AUTOFOCUS)!=0.:
            raise RuntimeError('Inspection autofocus unexpectedly re-enabled')
        value=float(adapter.cap.get(cv2.CAP_PROP_FOCUS))
    if not math.isfinite(value):raise RuntimeError('Invalid inspection focus readback')
    if position and abs(value-target['position'])>1.:
        raise RuntimeError('Inspection focus position changed')
    return dict(auto_focus=0.,position=value)


def restore(adapter,original):
    if original is None:return
    if (adapter.backend!='opencv-v4l2' or not hasattr(adapter,'cap') or not isinstance(original,dict)
            or set(original)!={'auto_focus','position'} or original['auto_focus']!=1.
            or not isinstance(original['position'],(int,float)) or not math.isfinite(original['position'])
            or not 0<=original['position']<=10000):
        raise RuntimeError('Invalid original autofocus journal; restoration refused')
    import cv2
    with adapter.access():
        if adapter.cap.get(cv2.CAP_PROP_AUTOFOCUS)!=1.:
            if not adapter.cap.set(cv2.CAP_PROP_AUTOFOCUS,1.):raise RuntimeError('Cannot restore original autofocus')
        if adapter.cap.get(cv2.CAP_PROP_AUTOFOCUS)!=1.:raise RuntimeError('Original autofocus restoration unverified')
