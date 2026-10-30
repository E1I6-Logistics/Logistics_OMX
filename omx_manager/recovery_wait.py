"""Low-memory MQTT error presence while no model process is running."""
import argparse
import json
import signal
import threading

from .config import load


def rejected_ack(robot,data):
    job=data.get('job_id') if isinstance(data,dict) else None
    if not isinstance(job,str) or not job or len(job)>200: return None
    return robot+'/ack',dict(job_id=job,accepted=False,reason='RECOVERY_WAIT')


def run(path):
    import paho.mqtt.client as mqtt
    cfg=load(path);robot=cfg['device_id'];stop=threading.Event()
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=robot+'-recovery')
    payload=json.dumps(dict(state='ERROR',detail='RECOVERY_WAIT',model_loaded=False))
    client.will_set(robot+'/status',json.dumps(dict(state='ERROR',detail='RECOVERY_DISCONNECTED')))
    def on_connect(client,userdata,flags,reason,properties):
        if reason==0:
            client.subscribe(robot+'/command',qos=1)
            client.publish(robot+'/status',payload,qos=1)
    def on_message(client,userdata,message):
        try: data=json.loads(message.payload)
        except (ValueError,UnicodeError): return
        ack=rejected_ack(robot,data)
        if ack: client.publish(ack[0],json.dumps(ack[1]),qos=1)
    client.on_connect=on_connect;client.on_message=on_message
    client.reconnect_delay_set(min_delay=1,max_delay=30)
    client.connect_async(cfg['runtime']['broker_host'],cfg['runtime']['broker_port'],30)
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
    client.loop_start()
    try:
        while not stop.wait(5):
            if client.is_connected(): client.publish(robot+'/status',payload,qos=1)
    finally:
        client.disconnect();client.loop_stop()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    run(parser.parse_args().config)
