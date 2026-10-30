"""OMX follower manager. No hardware is imported by validate/plan/simulate."""
from __future__ import annotations
import argparse
import copy
import json
import sys
import time
from pathlib import Path
from .config import ROOT, JOINTS, load, save, require_home, binding, digest, check_files, validate, path_value, diagnostic_options, service_options
from .home import joints, errors, stable_pose, move_home, HomeDetector
from .hardware import FollowerBus
from .runtime import device_lock, build, Supervisor, state_dir, acknowledge_recovery

def table(cfg,q):
    target=cfg["home"]["target"]
    print("joint               current      target       error   tolerance")
    for j in JOINTS:
        t=target[j] if target else None
        print(f"{j:18s} {q[j]:9.3f} "+("   unregistered" if t is None else f"{t:11.3f} {abs(q[j]-t):11.3f} {cfg['home']['tolerance'][j]:11.3f}"))

def edit_value(cfg,key,text):
    import yaml
    allowed={"models.active_profile","runtime.cpu_threads","runtime.fps","home.hold_s","home.unload_hold_s","home.still_delta", "home.max_start_error", "home.move_speed", "counting.min_cycle_s", "counting.stall_s", "counting.leave_tolerance", "home.arrival_tolerance", "home.move_timeout_s", "home.max_step", "home.max_following_error", "home.max_sample_gap_s", "recovery.release_delay_s"}
    allowed.update("home.tolerance."+j for j in JOINTS)
    allowed.update("diagnostics."+name for name in diagnostic_options(cfg))
    allowed.update("service."+name for name in service_options(cfg))
    from .basket import DEFAULTS
    allowed.update('unload.'+name for name in DEFAULTS if name not in ('reference','reference_binding'))
    allowed.update(('recovery.auto_home','recovery.poll_s'))
    from .cycles import DEFAULTS as CYCLE_DEFAULTS
    allowed.update('counting.'+name for name in CYCLE_DEFAULTS if name not in ('profiles','mode'))
    if key not in allowed: raise ValueError("This field requires editing the YAML with hardware/model review")
    result=copy.deepcopy(cfg)
    if key.startswith('counting.'):
        from .commissioning import initialize
        result=initialize(result)
    if key.startswith('unload.'):
        from .commissioning import initialize
        result=initialize(result)
    if key.startswith("diagnostics."):result.setdefault("diagnostics",diagnostic_options(cfg))
    if key.startswith("service."):result.setdefault("service",service_options(cfg))
    nodes=key.split("."); section=result
    for part in nodes[:-1]: section=section[part]
    section[nodes[-1]]=yaml.safe_load(text)
    validate(result)
    return result

def simulate(cfg):
    from .simulation import exercise
    result=exercise(cfg)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result

def menu(path):
    while True:
        print("\nOMX follower-only 설정 도구\n1 상태/경로 검사 (장치 미접속)\n2 관절 관측 (모터 쓰기 없음)\n3 현재 자세를 홈으로 등록 (모터 쓰기 없음)\n4 등록 홈으로 제한 이동\n5 설정 조정\n6 가상 시험\n7 실행 계획 보기\n8 구동 준비 (현재 위치 목표 설정 후 토크 켜기)\n9 하차 영상·복귀 경로 등록\n10 모델을 돌리며 사이클 복귀 영역 보정 (pick/place 수동 등록 불필요)\n0 종료")
        choice=input("> ").strip()
        if choice=="0": return
        mapping={"1":"check","2":"observe","3":"capture","4":"home","6":"simulate","7":"plan","8":"arm"}
        try:
            if choice=='10':
                item=input('보정할 모델 품목 A~F: ').strip().upper()
                if input('모델이 실제로 움직입니다. 작업 공간 비운 뒤 MOVE 입력: ').strip()=='MOVE':
                    invoke(path,'cycle-calibrate',item=item,confirm_motion=True)
            elif choice=='9':
                from .workflow_menu import run
                run(path)
            elif choice=="5":
                key=input("설정 키 (예: home.tolerance.wrist_flex): ").strip(); value=input("새 값: ").strip()
                invoke(path,"set",key=key,value=value)
            elif choice in mapping:
                confirm=choice in ("4", "8") and input("주변 비움·경로 확인 후 MOVE 입력: ").strip()=="MOVE"
                if choice in ("4", "8") and not confirm: continue
                if choice=="3" and input("모델에 맞는 초기 자세 확인 후 CAPTURE 입력: ").strip()!="CAPTURE": continue
                invoke(path,mapping[choice],confirm_motion=confirm)
        except KeyboardInterrupt: print("관측/작업 중단")
        except Exception as exc: print("[ERROR]",exc)

def invoke(path,command,confirm_motion=False,key=None,value=None,diagnostics=None,important_only=None,follow=False,interval=None,samples=0,json_output=False,lines=50,action=None,expected_device=None,item=None,quantity=1,name=None,confirm_empty=False,round_trip=False,until_empty=False):
    cfg=load(path)
    if expected_device is not None and cfg["device_id"]!=expected_device:
        raise ValueError("Device mismatch: requested "+expected_device+", YAML is "+cfg["device_id"])
    if command=="order":
        from .orders import send_order
        if until_empty:return send_order(cfg,item,quantity,until_empty=True)
        return send_order(cfg,item,quantity)
    if command=="menu": return menu(path)
    if command=="service":
        from .service import manage
        return manage(cfg,path,action,confirm_motion,follow)
    if command=="logs":
        from .logs import view
        return view(ROOT/"logs"/cfg["device_id"]/"important.log",follow,lines)
    if command=="monitor":
        from .diagnostics import monitor
        return monitor(cfg,interval,samples,json_output)
    if command=='basket-status':
        from .diagnostics import read_json
        data=read_json(state_dir(cfg)/'basket_status.json')
        if not data or not isinstance(data.get('at'),(int,float)) or not 0<=time.time()-data['at']<=.5:
            data=dict(verdict='UNKNOWN',reason='NO_FRESH_BASKET_SNAPSHOT')
        print(json.dumps(data,ensure_ascii=False));return data
    if command=="validate":
        print(f"YAML valid: {cfg['device_id']}; home_registered={cfg['home']['target'] is not None}"); return
    if command=="simulate": return simulate(cfg)
    if command=="plan":
        argv,mqtt,_,_=build(cfg,path)
        print(json.dumps({"device":cfg["device_id"],"models":cfg["models"]["active_profile"],"home_registered":cfg["home"]["target"] is not None,"rollout_argv":argv,"mqtt_argv":mqtt},ensure_ascii=False,indent=2));return
    if command=="check":
        check_files(cfg)
        require_home(cfg)
        print("Files, model I/O, calibration structure and Home binding OK. Motor communication not tested.");return
    if command=="run":
        if not confirm_motion: raise ValueError("Starting the robot requires --confirm-motion after verifying YAML, Home and workspace")
        return Supervisor(path,diagnostics=diagnostics,important_only=important_only).run()
    if command=='resume':
        if not confirm_motion: raise ValueError('Inspect cargo and Home, then use --confirm-motion; interrupted jobs will not be replayed')
        from .config import write_json
        import uuid
        write_json(state_dir(cfg)/'resume_request.json',dict(at=time.time(),token=uuid.uuid4().hex,config=str(Path(path).resolve())))
        print('Recovery recheck requested. Failed/interrupted jobs remain failed; inspect recovery_wait.json for the result.');return
    with device_lock(cfg):
        if command=='tune':
            from .commissioning import tune
            save(path,tune(cfg));print('CPU threads=4; cycle counting enabled; faster basket confirmation. Existing Home/models/calibration/reference preserved.');return
        if command=='cycle-calibrate':
            if not confirm_motion:raise ValueError('Model rollout calibration requires --confirm-motion')
            from .cycle_calibration import run
            return run(path,item)
        if command=='setup-workflow':
            from .commissioning import initialize
            save(path,initialize(cfg));print('Sections added; original Home/model/camera/calibration preserved. Learn cycle return regions with omx_cycle_calibrate; pick/place registration is no longer required.');return
        if command=='stage-clear':
            if item not in cfg.get('workflow',{}).get('items',{}): raise ValueError('Specify a registered --item')
            del cfg['workflow']['items'][item]
            save(path,cfg);print('Stage registration removed. Home/calibration unchanged.');return
        if command=='workflow-check':
            from .commissioning import require_commissioned
            check_files(cfg);require_commissioned(cfg)
            print('Cycle return regions and basket reference registered. Physical reliability/return route still require local tests.');return
        if command=="set":
            updated=edit_value(cfg,key,value); save(path,updated); print("Saved; takes effect on NEXT start");return
        if command=="acknowledge-recovery":
            if not confirm_motion: raise ValueError("Inspect physical cargo and use --confirm-motion to reconcile interrupted work")
            acknowledge_recovery(cfg);print("Interrupted state archived; counters/jobs were not silently replayed");return
        if command in ("home", "arm", "release", "route-test", "return-home"):
            if not confirm_motion: raise ValueError("Home/torque operation requires --confirm-motion")
            if command != "release": require_home(cfg)
        if command=='basket-capture' and not confirm_empty:
            raise ValueError('Verify empty basket, camera view and lighting; use --confirm-empty')
        original=digest(path)
        original_binding=binding(cfg) if command == "capture" else None
        with FollowerBus(cfg) as device:
            if command in ('stage-capture','route-capture'):
                from .commissioning import capture_stage,capture_route
                updated=capture_stage(cfg,device,item,name) if command=='stage-capture' else capture_route(cfg,device,name)
                if digest(path)!=original: raise RuntimeError('YAML changed while sampling; registration refused')
                require_home(updated)
                save(path,updated);print('Observed pose registered. No motor writes; return routes remain unapproved until route-test.');return
            elif command in ('basket-capture','basket-check','basket-snapshot','basket-watch'):
                from .commissioning import basket_operation,initialize
                if cfg['role']!='unload': raise ValueError('Basket inspection is for OMX3/4')
                result=basket_operation(cfg,device,capture='snapshot' if command=='basket-snapshot' else command=='basket-capture',watch=command=='basket-watch',samples_limit=samples)
                if command=='basket-watch':return result
                if command=='basket-check': print(json.dumps(result));return result
                if command=='basket-snapshot':
                    import cv2
                    destination=state_dir(cfg)/'basket_view.png';destination.parent.mkdir(parents=True,exist_ok=True)
                    view=cv2.cvtColor(result,cv2.COLOR_RGB2BGR)
                    for x in range(0,view.shape[1],80):
                        cv2.line(view,(x,0),(x,view.shape[0]-1),(0,255,0),1);cv2.putText(view,str(x),(x+2,16),cv2.FONT_HERSHEY_SIMPLEX,.4,(0,255,0),1)
                    for y in range(0,view.shape[0],80):
                        cv2.line(view,(0,y),(view.shape[1]-1,y),(0,255,0),1);cv2.putText(view,str(y),(2,y+32),cv2.FONT_HERSHEY_SIMPLEX,.4,(0,255,0),1)
                    if not cv2.imwrite(str(destination),view): raise OSError('Cannot save basket view')
                    print(destination);return
                import numpy as np
                import uuid
                from .workflow import registration_binding
                updated=initialize(cfg)
                destination=state_dir(cfg)/('empty_basket_'+uuid.uuid4().hex+'.npz')
                destination.parent.mkdir(parents=True,exist_ok=True)
                np.savez_compressed(destination,image=result,roi=updated['unload']['roi'])
                updated['unload']['reference']=str(destination)
                updated['unload']['reference_binding']=registration_binding(cfg)
                if digest(path)!=original: raise RuntimeError('YAML changed while sampling; registration refused')
                require_home(updated);save(path,updated)
                print('Operator-confirmed empty reference registered. Test empty AND occupied baskets before production.');return
            elif command in ('route-test','return-home'):
                from .return_path import return_home,route_signature
                from .hooks import home_reporter
                if command=='route-test' and name not in cfg['recovery'].get('routes',{}):
                    raise ValueError('Specify a captured route --name before any motion')
                for joint in JOINTS:
                    if device.bus.read('Operating_Mode',joint)!=(5 if joint=='gripper' else 4):
                        raise RuntimeError('Motor mode changed; return refused')
                if command=='route-test' and round_trip:
                    import copy
                    if max(errors(device.read(),cfg['home']['target']).values())>cfg['home']['arrival_tolerance']:
                        raise ValueError('Round-trip route test must start at strict registered Home')
                    outward=copy.deepcopy(cfg)
                    route=outward['recovery']['routes'][name]
                    if len(route['points'])<2: raise ValueError('Capture at least two route waypoints including Home')
                    route['points']=list(reversed(route['points']))
                    outward['home']['target']=route['points'][-1].copy()
                    return_home(device,outward,clock=time.monotonic,sleep=time.sleep,
                                report=home_reporter(cfg),route_name=name,test=True)
                return_home(device,cfg,clock=time.monotonic,sleep=time.sleep,report=home_reporter(cfg),
                            route_name=name,test=command=='route-test')
                if command=='route-test':
                    if name not in cfg['recovery'].get('routes',{}): raise ValueError('Specify a captured route --name')
                    if digest(path)!=original: raise RuntimeError('YAML changed during route test; approval refused')
                    cfg['recovery']['routes'][name]['approved']=route_signature(cfg,cfg['recovery']['routes'][name])
                    save(path,cfg);print('Return path executed to Home. Approval is bound to this path/Home/model/calibration.')
                return
            elif command=="observe":
                for _ in range(20): table(cfg,joints(device.read())); time.sleep(.1)
            elif command=="capture":
                samples=[]; end=time.monotonic()+cfg["home"]["capture_s"]
                while time.monotonic()<end:
                    samples.append(device.read());time.sleep(.05)
                target=stable_pose(samples,cfg["home"]["capture_max_span"])
                if digest(path)!=original: raise RuntimeError("YAML changed during capture; refusing overwrite")
                if binding(cfg) != original_binding: raise RuntimeError("Calibration/model changed during capture; registration rejected")
                cfg["home"]["target"]=target;cfg["home"]["binding"]=original_binding
                save(path,cfg);table(cfg,target);print("Registered stable Home; existing motor calibration unchanged")
            elif command=="release":
                delay=cfg["recovery"].get("release_delay_s",10)
                print(f"[RELEASE] {cfg['device_id']}: {delay:g}초 뒤 전체 토크 OFF. 물품을 내려놓고 팔을 지지하세요. 취소는 Ctrl+C.",flush=True)
                time.sleep(delay)
                states=device.release()
                print("전체 토크 OFF 확인 완료:",states)
            elif command=="arm":
                device.arm();print("Current pose goal installed; torque enabled. Verify locally before Home move")
            elif command=="home":
                from .hooks import home_reporter
                q=move_home(device,cfg,clock=time.monotonic,sleep=time.sleep,report=home_reporter(cfg));table(cfg,q)
            else: raise ValueError("Unknown command")

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,required=True,help="params/omx1.yaml etc.")
    parser.add_argument("command",choices=("menu","validate","check","plan","observe","capture","home","arm","set","run","simulate","acknowledge-recovery","logs","monitor","service","release","order","setup-workflow","workflow-check","stage-capture","stage-clear","route-capture","route-test","return-home","basket-capture","basket-check","basket-watch","basket-status","basket-snapshot","resume","tune","cycle-calibrate"))
    parser.add_argument("--confirm-motion",action="store_true")
    parser.add_argument("--expected-device",choices=("omx1","omx2","omx3","omx4"),help="Launcher device/YAML consistency check")
    parser.add_argument("--key");parser.add_argument("--value")
    parser.add_argument("--item");parser.add_argument("--quantity",type=int,default=1)
    parser.add_argument('--name')
    parser.add_argument('--confirm-empty',action='store_true')
    parser.add_argument('--round-trip',action='store_true',help='Route test from Home to first waypoint and back; actual motion')
    parser.add_argument('--until-empty',action='store_true',help='OMX3/4 unload without expected quantity; confirmed empty view still required')
    parser.add_argument("--action",choices=("install","start","stop","status","logs","enable","disable","uninstall"))
    parser.add_argument("--diagnostics",action="store_true",default=None,help="Opt-in camera/loop/ACT counters and bounded logs")
    parser.add_argument("--important-only",action="store_true",default=None,help="Capture bounded full logs, show important events only")
    parser.add_argument("--follow",action="store_true",help="Follow important logs without controlling the robot")
    parser.add_argument("--lines",type=int,default=50)
    parser.add_argument("--interval",type=float,default=None)
    parser.add_argument("--samples",type=int,default=0,help="0 means continuous monitor")
    parser.add_argument("--json",action="store_true",dest="json_output")
    args=parser.parse_args(argv)
    try: invoke(args.config,args.command,args.confirm_motion,args.key,args.value,args.diagnostics,args.important_only,args.follow,args.interval,args.samples,args.json_output,args.lines,args.action,args.expected_device,args.item,args.quantity,args.name,args.confirm_empty,args.round_trip,args.until_empty)
    except KeyboardInterrupt: print("Stopped by operator");return 130
    except Exception as exc: print(f"[ERROR] {type(exc).__name__}: {exc}",file=sys.stderr);return 2
    return 0

if __name__=="__main__": raise SystemExit(main())
