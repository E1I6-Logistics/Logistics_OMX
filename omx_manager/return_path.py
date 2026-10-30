"""Explicitly tested joint-space corridors. Never plan from an unknown pose."""
import copy
import hashlib
import json

from .config import ConfigError, JOINTS, number, only_keys
from .home import joints, move_home
from .workflow import registration_binding


def route_signature(cfg, route):
    material = {"binding":registration_binding(cfg),"points":route["points"],
                "radius":route.get("radius",1.0)}
    return hashlib.sha256(json.dumps(material,sort_keys=True).encode()).hexdigest()


def validate_routes(cfg):
    rec=cfg["recovery"]
    if type(rec.get("auto_home",True)) is not bool: raise ConfigError("auto_home must be boolean")
    number(rec.get("poll_s",5.),"recovery.poll_s",1,60)
    routes=rec.get("routes",{})
    if not isinstance(routes,dict) or len(routes)>16: raise ConfigError("At most 16 recovery routes")
    for name,route in routes.items():
        if not isinstance(name,str) or not name or not isinstance(route,dict): raise ConfigError("Invalid route")
        only_keys(route,"points radius approved", "return route")
        number(route.get("radius",1.),"route.radius",.1,2.)
        points=route.get("points",[])
        if not isinstance(points,list) or len(points)>32: raise ConfigError("At most 32 route waypoints")
        for point in points:
            joints(point)
            if set(point)!=set(JOINTS): raise ConfigError("Route needs six joints per waypoint")


def segment_error(q, start, end):
    vector={j:end[j]-start[j] for j in JOINTS}
    norm=sum(v*v for v in vector.values())
    t=0. if norm == 0 else max(0.,min(1.,sum((q[j]-start[j])*vector[j] for j in JOINTS)/norm))
    distance=max(abs(q[j]-(start[j]+t*vector[j])) for j in JOINTS)
    return distance,t


def route_entry(cfg, route, q, *, test=False):
    if not test and route.get("approved") != route_signature(cfg,route):
        raise ConfigError("Recovery route not tested for current Home/calibration/model")
    points=route["points"]
    home=cfg["home"]["target"]
    if len(points)<2 or home is None: raise ConfigError("Capture at least two recovery waypoints")
    if test and max(abs(q[j]-points[0][j]) for j in JOINTS)>route.get('radius',1.):
        raise ConfigError('A route test must start at its first waypoint and traverse the whole route')
    if max(abs(points[-1][j]-home[j]) for j in JOINTS)>cfg["home"]["arrival_tolerance"]:
        raise ConfigError("Last recovery waypoint must be the registered Home")
    matches=[]
    for index,(start,end) in enumerate(zip(points,points[1:])):
        error,t=segment_error(q,start,end)
        if error<=route.get("radius",1.): matches.append((index,error,t))
    if not matches: raise ConfigError("Unknown pose: outside every tested recovery corridor")
    if matches[-1][0]-matches[0][0]>1:
        raise ConfigError("Ambiguous overlapping recovery corridors; register a simpler route")
    index,_,_=min(matches,key=lambda row:row[1])
    return index


def return_home(device,cfg,*,clock,sleep,stopped=lambda:False,report=None,route_name=None,test=False,preserve_gripper=False):
    if stopped(): raise InterruptedError("Operator stop; no recovery movement")
    q=joints(device.read())
    if preserve_gripper:
        # Automatic error recovery must not release an unknown carried object.
        # Keep the tested ARM corridor unchanged; replace only gripper coordinates.
        original=cfg
        cfg=copy.deepcopy(cfg)
        cfg['home']['target']['gripper']=q['gripper']
        for name,route in cfg['recovery'].get('routes',{}).items():
            approved=original['recovery']['routes'][name].get('approved')==route_signature(original,original['recovery']['routes'][name])
            for point in route['points']: point['gripper']=q['gripper']
            route['approved']=route_signature(cfg,route) if approved else None
    if route_name is None and max(abs(q[j]-cfg["home"]["target"][j]) for j in JOINTS)<=cfg["home"]["max_start_error"]:
        return move_home(device,cfg,clock=clock,sleep=sleep,stopped=stopped,report=report)
    routes=cfg["recovery"].get("routes",{})
    candidates=[];reasons=[]
    for name,route in routes.items():
        if route_name is not None and name!=route_name: continue
        try: index=route_entry(cfg,route,q,test=test)
        except ConfigError as exc:
            reasons.append(name+': '+str(exc));continue
        candidates.append((name,route,index))
    if len(candidates)!=1:
        raise ConfigError("No unique tested return route for current pose; RECOVERY_WAIT; "+('; '.join(reasons) or 'no matching registered route'))
    name,route,index=candidates[0]
    began=clock()
    for start,end in zip(route["points"][index:],route["points"][index+1:]):
        if clock()-began>300: raise TimeoutError("Return route exceeded 300s total")
        local=copy.deepcopy(cfg)
        local["home"]["target"]=end
        local["home"]["max_start_error"]=max(abs(end[j]-start[j]) for j in JOINTS)+2*route.get("radius",1.)
        local["home"]["max_following_error"]=min(cfg["home"].get("max_following_error",3.),route.get("radius",1.))
        local["home"]["move_timeout_s"]=min(180.,max(cfg["home"]["move_timeout_s"],local["home"]["max_start_error"]/cfg["home"]["move_speed"]*2+5))
        local["home"]["arrival_tolerance"]=min(cfg["home"]["arrival_tolerance"],route.get("radius",1.)/2)
        def guard(values):
            if segment_error(values,start,end)[0]>route.get("radius",1.)+1e-8:
                raise ConfigError("Feedback/goal left approved recovery segment; no further commands")
        q=move_home(device,local,clock=clock,sleep=sleep,stopped=stopped,report=report,guard=guard)
    if max(abs(q[j]-cfg["home"]["target"][j]) for j in JOINTS)>cfg["home"]["arrival_tolerance"]:
        raise RuntimeError("Return route did not reach Home")
    return q
