#!/usr/bin/env python3
"""Replay labeled local PNGs. Never connect cameras, motors, MQTT or a model."""
import argparse,hashlib,json,sys,time
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import cv2
from omx_manager import basket,config as C
from omx_manager.basket_registration import Tracker,Comparator,ViewError


def evaluate(manifest):
    cases=manifest['samples'];results=[];inputs=[];reference_counts={}
    for device in ('omx3','omx4'):
        cfg=C.load(ROOT/f'params/{device}.yaml')
        cfg['unload'].update(roi=manifest['roi'][device],registration_enabled=True,low_exposure_enabled=True,
                             inspection_exposure=83.,vision_method='low_template',full_view_confirmed=True,
                             registration_allow_partial=manifest.get('partial',{}).get(device,False),registration_visible_confirmed=True)
        spec=basket.options(cfg);images=[];references=[]
        for row in (x for x in cases if x['device']==device):
            path=Path(row['path']);image=cv2.imread(str(path))
            if image is None:raise ValueError('Cannot decode '+str(path))
            image=cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
            inputs.append(dict(id=row['id'],device=device,expected=row['expected'],sha256=hashlib.sha256(path.read_bytes()).hexdigest(),path=str(path)))
            images.append((row,image))
            if row['expected']=='EMPTY':
                tracker=Tracker(spec)
                try:canonical=tracker.extract(image);references.append((row['id'],canonical,tracker.last_profile,tracker.valid_mask.copy()))
                except ViewError:pass
        reference_counts[device]=len(references)
        if not references:
            for row,image in images:
                try:Tracker(spec).extract(image);reason='REFERENCE_UNAVAILABLE'
                except ViewError as exc:reason=str(exc)
                results.append(dict(reference=None,id=row['id'],device=device,expected=row['expected'],verdict='UNKNOWN',reason=reason,
                                    accepted=row.get('allow_unknown',False),classification_attempted=False,view_scope='visible_only' if spec['registration_allow_partial'] else 'full'))
        for reference_id,reference,profile,reference_mask in references:
            for row,image in images:
                started=time.perf_counter();tracker=Tracker(spec,profile)
                try:
                    current=tracker.extract(image);evidence=Comparator(reference,spec,reference_mask).compare(current,tracker.valid_mask)
                    verdict=evidence['verdict'];reason=evidence['evidence_reason']
                except ViewError as exc:verdict='UNKNOWN';reason=str(exc)
                results.append(dict(reference=reference_id,id=row['id'],device=device,expected=row['expected'],verdict=verdict,reason=reason,
                                    accepted=verdict==row['expected'] or verdict=='UNKNOWN' and row.get('allow_unknown',False),
                                    classification_attempted=True,view_scope='visible_only' if spec['registration_allow_partial'] else 'full',host_processing_ms=round((time.perf_counter()-started)*1000,3)))
    return dict(input_count=len(inputs),inputs=inputs,reference_counts=reference_counts,results=results,
                all_accepted=bool(results) and all(row['accepted'] for row in results),
                combinations=dict(Counter(row['device']+'/'+row['expected']+'/'+row['verdict'] for row in results)),
                real_hardware_executed=False,pi_performance_measured=False,model_weights_changed=False,
                note='Offline captured images; combinations reuse images and are not independent physical trials. UNKNOWN is never counted as empty.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();report=evaluate(json.loads(a.manifest.read_text()));a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('input_count','reference_counts','all_accepted','combinations')},ensure_ascii=False,indent=2))
    return 0 if report['all_accepted'] else 1
if __name__=='__main__':raise SystemExit(main())
