"""Read-only servo sampling. No writes, torque, gains or calibration changes."""
import time
from .config import JOINTS,ROOT,write_json


def collect(device,cfg,*,samples=20,clock=time.monotonic,sleep=time.sleep):
    if type(samples) is not int or not 3<=samples<=100:raise ValueError('Motor diagnostic samples must be 3..100')
    records=[];start=clock()
    for index in range(samples):
        q=device.read();goal=device.bus.sync_read('Goal_Position')
        records.append(dict(elapsed_s=clock()-start,position=q,goal=goal))
        if index+1<samples:sleep(.15)
    summary={}
    for joint in JOINTS:
        positions=[row['position'][joint] for row in records];goals=[row['goal'][joint] for row in records]
        controls={}
        for register in ('Torque_Enable','Operating_Mode','Hardware_Error_Status','Position_P_Gain','Position_I_Gain',
                         'Position_D_Gain','Present_Voltage','Present_Temperature','Present_Current'):
            try:controls[register]=device.bus.read(register,joint)
            except (ValueError,KeyError,NotImplementedError):controls[register]='unsupported'
        summary[joint]=dict(position_span=max(positions)-min(positions),goal_span=max(goals)-min(goals),
                            current=positions[-1],goal=goals[-1],following_error=abs(goals[-1]-positions[-1]),
                            registered_home=cfg['home']['target'].get(joint) if cfg['home']['target'] else None,registers=controls)
    return dict(device=cfg['device_id'],at=time.time(),elapsed_s=clock()-start,motor_writes=False,
                joints=summary,samples=records)


def save(device,cfg,samples=20):
    result=collect(device,cfg,samples=samples)
    destination=ROOT/'state'/'manager'/cfg['device_id']/'motor_diagnostics.json'
    write_json(destination,result)
    return dict(result,path=str(destination))
