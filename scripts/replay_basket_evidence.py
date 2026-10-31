#!/usr/bin/env python3
"""Replay extracted operator-labeled evidence; never connect hardware or edit it."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import cv2
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from omx_manager.basket import BasketInspector,options
from omx_manager.basket_registration import Tracker,Comparator
from omx_manager.workflow import registration_binding


def evaluate(root):
    results=[];timings=[];correct=0;frames=0;wrong_publications=0;direct_unknown=0;direct_wrong=0
    for device in ('omx3','omx4'):
        folder=Path(root)/device
        cfg=json.loads((folder/'config.json').read_text())
        cfg['hardware']['calibration_file']=str(folder/'motor_calibration.json')
        cfg['unload']['reference']=str(folder/'reference.npz')
        cfg['unload']['reference_binding']=registration_binding(cfg)
        spec=options(cfg);inspector=BasketInspector(cfg)
        with np.load(folder/'reference.npz',allow_pickle=False) as data:
            reference=data['image'];profile=json.loads(str(data['registration_profile']));mask=data['registration_mask']
        comparator=Comparator(reference,spec,mask)
        manifest=json.loads((folder/'manifest.json').read_text())
        for scene in manifest['scenes']:
            truth=scene['operator_ground_truth'];inspector.reset();tracker=Tracker(spec,profile)
            records=[];last=None
            for sample in scene['low']:
                path=folder/'scenes'/scene['name']/'low'/spec['camera']/f'{sample["index"]:03d}.png'
                content=path.read_bytes();image=cv2.cvtColor(cv2.imdecode(np.frombuffer(content,np.uint8),cv2.IMREAD_COLOR),cv2.COLOR_BGR2RGB)
                started=time.perf_counter();canonical=tracker.extract(image)
                direct=comparator.compare(canonical,tracker.valid_mask);timings.append((time.perf_counter()-started)*1000)
                public=inspector.update(image,sample['observed_monotonic'],sample['frame_monotonic'])
                frames+=1;correct+=direct['verdict']==truth;wrong_publications+=public not in ('UNKNOWN',truth)
                direct_unknown+=direct['verdict']=='UNKNOWN';direct_wrong+=direct['verdict'] not in ('UNKNOWN',truth)
                records.append(dict(index=sample['index'],sha256=hashlib.sha256(content).hexdigest(),
                                    direct=direct['verdict'],public=public,reason=inspector.reason))
                last=(image,sample['observed_monotonic'],sample['frame_monotonic'])
            final=records[-1]['public'];tail=0;image,now,stamp=last
            # Explicit simulation only: keep the last scene still for up to 1s.
            # These are NOT additional measured camera frames/trials.
            public=final
            while public=='UNKNOWN' and tail<10:
                tail+=1;now+=.11;stamp+=.11;public=inspector.update(image,now,stamp)
            results.append(dict(device=device,scene=scene['name'],truth=truth,frames=records,
                                recorded_final=final,static_tail_simulated_frames=tail,static_tail_result=public))
    return dict(measured_frames=frames,direct_matches=correct,wrong_confirmed_publications=wrong_publications,
                direct_unknown=direct_unknown,direct_wrong=direct_wrong,all_direct_correct=correct==frames,recorded_sequences_confirmed=sum(r['recorded_final']==r['truth'] for r in results),
                static_tail_sequences_correct=sum(r['static_tail_result']==r['truth'] for r in results),
                local_processing_ms=dict(median=float(np.median(timings)),p95=float(np.percentile(timings,95)),maximum=max(timings)),
                results=results,notes='Local RGB replay, not Pi/robot testing. Static-tail frames are simulated duplicates.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();report=evaluate(args.root);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False,indent=2))
    raise SystemExit(0 if not report['direct_wrong'] and not report['wrong_confirmed_publications'] and report['static_tail_sequences_correct']==8 else 1)
