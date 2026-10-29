"""OMX follower manager. No hardware is imported by validate/plan/simulate."""
from __future__ import annotations
import argparse
import copy
import json
import sys
import time
from pathlib import Path
from .config import ROOT, JOINTS, load, save, require_home, binding, digest, check_files, validate, path_value
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
    allowed={"models.active_profile","runtime.cpu_threads","runtime.fps","home.hold_s","home.unload_hold_s","home.still_delta", "home.max_start_error", "home.move_speed", "counting.min_cycle_s", "counting.stall_s", "counting.leave_tolerance"}
    allowed.update("home.tolerance."+j for j in JOINTS)
    if key not in allowed: raise ValueError("This field requires editing the YAML with hardware/model review")
    result=copy.deepcopy(cfg); nodes=key.split("."); section=result
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
        print("\nOMX follower-only 설정 도구\n1 상태/경로 검사 (장치 미접속)\n2 관절 관측 (모터 쓰기 없음)\n3 현재 자세를 홈으로 등록 (모터 쓰기 없음)\n4 등록 홈으로 제한 이동\n5 설정 조정\n6 가상 시험\n7 실행 계획 보기\n8 구동 준비 (현재 위치 목표 설정 후 토크 켜기)\n0 종료")
        choice=input("> ").strip()
        if choice=="0": return
        mapping={"1":"check","2":"observe","3":"capture","4":"home","6":"simulate","7":"plan","8":"arm"}
        try:
            if choice=="5":
                key=input("설정 키 (예: home.tolerance.wrist_flex): ").strip(); value=input("새 값: ").strip()
                invoke(path,"set",key=key,value=value)
            elif choice in mapping:
                confirm=choice in ("4", "8") and input("주변 비움·경로 확인 후 MOVE 입력: ").strip()=="MOVE"
                if choice in ("4", "8") and not confirm: continue
                if choice=="3" and input("모델에 맞는 초기 자세 확인 후 CAPTURE 입력: ").strip()!="CAPTURE": continue
                invoke(path,mapping[choice],confirm_motion=confirm)
        except KeyboardInterrupt: print("관측/작업 중단")
        except Exception as exc: print("[ERROR]",exc)

def invoke(path,command,confirm_motion=False,key=None,value=None):
    cfg=load(path)
    if command=="menu": return menu(path)
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
        return Supervisor(path).run()
    with device_lock(cfg):
        if command=="set":
            updated=edit_value(cfg,key,value); save(path,updated); print("Saved; takes effect on NEXT start");return
        if command=="acknowledge-recovery":
            if not confirm_motion: raise ValueError("Inspect physical cargo and use --confirm-motion to reconcile interrupted work")
            acknowledge_recovery(cfg);print("Interrupted state archived; counters/jobs were not silently replayed");return
        if command in ("home", "arm"):
            if not confirm_motion: raise ValueError("Home movement requires --confirm-motion")
            require_home(cfg)
        original=digest(path)
        original_binding=binding(cfg) if command == "capture" else None
        with FollowerBus(cfg) as device:
            if command=="observe":
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
            elif command=="arm":
                device.arm();print("Current pose goal installed; torque enabled. Verify locally before Home move")
            elif command=="home":
                q=move_home(device,cfg,clock=time.monotonic,sleep=time.sleep);table(cfg,q)
            else: raise ValueError("Unknown command")

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,required=True,help="params/omx1.yaml etc.")
    parser.add_argument("command",choices=("menu","validate","check","plan","observe","capture","home","arm","set","run","simulate","acknowledge-recovery"))
    parser.add_argument("--confirm-motion",action="store_true")
    parser.add_argument("--key");parser.add_argument("--value")
    args=parser.parse_args(argv)
    try: invoke(args.config,args.command,args.confirm_motion,args.key,args.value)
    except KeyboardInterrupt: print("Stopped by operator");return 130
    except Exception as exc: print(f"[ERROR] {type(exc).__name__}: {exc}",file=sys.stderr);return 2
    return 0

if __name__=="__main__": raise SystemExit(main())
