#!/usr/bin/env python3
"""사람이 정답 표시한 팔레트 ZIP 재생. 장비·모델·설정 파일 변경 없음."""
import argparse
import io
import json
from pathlib import Path
import sys
import time
import zipfile
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from omx_manager import pallet as P


def evaluate(paths,repeats=1):
    groups={};seen=set()
    for path in paths:
        if path.stat().st_size>10000000:raise ValueError('ZIP 크기 초과')
        with zipfile.ZipFile(path) as outer:
            if len(outer.infolist())>100 or sum(i.file_size for i in outer.infolist())>10000000:raise ValueError('ZIP 내용 크기 초과')
            for info in outer.infolist():
                if info.is_dir():continue
                if not info.filename.endswith('.npz') or info.file_size>1000000:raise ValueError('진단 NPZ만 허용합니다')
                raw=outer.read(info)
                with zipfile.ZipFile(io.BytesIO(raw)) as inner:
                    if sum(i.file_size for i in inner.infolist())>600000:raise ValueError('NPZ 해제 크기 초과')
                with np.load(io.BytesIO(raw),allow_pickle=False) as d:
                    a,b=d['current'].copy(),d['reference'].copy();spec=dict(P.DEFAULTS,**json.loads(str(d['options'])))
                    robot,item,label=str(d['robot_id']),str(d['item']),str(d['label'])
                if (robot not in P.ITEMS or item not in P.ITEMS[robot] or label not in ('EMPTY','PRESENT')
                        or a.shape!=b.shape or a.dtype!=np.uint8 or b.dtype!=np.uint8 or a.ndim!=3
                        or a.shape[2]!=3 or min(a.shape[:2])<12 or max(a.shape[:2])>160):raise ValueError('영상 또는 라벨 형식 오류')
                P.validate(dict(device_id=robot,hardware=dict(cameras={spec['camera']:dict(width=10000,height=10000)}),pallet_stock=spec))
                key=(robot,item,Path(info.filename).name)
                if key in seen:raise ValueError('중복 자료')
                seen.add(key)
                record=dict(file=key[-1],label=label)
                for mode,enabled in (('baseline',False),('coloured',True)):
                    timings=[]
                    for _ in range(repeats):
                        start=time.perf_counter();result=P.compare(a,b,dict(spec,color_presence_enabled=enabled))
                        timings.append((time.perf_counter()-start)*1000)
                    record[mode]=dict(candidate=result['candidate'],reason=result['reason'],
                        processing_median_ms=float(np.median(timings)),processing_max_ms=max(timings),timings_ms=timings,
                        color_component_pixels=result.get('color_component_pixels'))
                groups.setdefault(robot+'/'+item,[]).append(record)
    if not groups:raise ValueError('자료가 없습니다')
    summary={}
    for key,rows in groups.items():
        summary[key]={}
        for mode in ('baseline','coloured'):
            summary[key][mode]=dict(samples=len(rows),correct=sum(r['label']==r[mode]['candidate'] for r in rows),
                false_empty=sum(r['label']=='PRESENT' and r[mode]['candidate']=='EMPTY' for r in rows),
                false_present=sum(r['label']=='EMPTY' and r[mode]['candidate']=='PRESENT' for r in rows),
                unknown=sum(r[mode]['candidate']=='UNKNOWN' for r in rows),
                processing_p50_ms=float(np.median([v for r in rows for v in r[mode]['timings_ms']])),
                processing_p95_ms=float(np.percentile([v for r in rows for v in r[mode]['timings_ms']],95)),
                processing_max_ms=max(r[mode]['processing_max_ms'] for r in rows))
    return dict(note='제공된 정답 사진을 사용한 재생 결과. 별도 실기/독립 검증 및 Pi 부하 성능 시험이 아닙니다.',
                summary=summary,items=groups)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives',type=Path,nargs='+');parser.add_argument('--repeat',type=int,choices=range(1,6),default=1)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();data=evaluate(args.archives,args.repeat)
    if args.output:args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(data['summary'],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
