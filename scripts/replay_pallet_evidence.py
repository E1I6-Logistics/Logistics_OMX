#!/usr/bin/env python3
"""저장된 팔레트 ROI를 기존/정밀 방식으로 재검사. 하드웨어·기준 파일 변경 없음."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
import zipfile

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from omx_manager import pallet as P


def replay(path, repeats=5):
    if not path.is_file() or path.stat().st_size>1000000:
        raise ValueError('진단 NPZ 파일이 없거나 크기가 너무 큽니다')
    with zipfile.ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist())>600000:raise ValueError('진단 압축 해제 크기 초과')
    with np.load(path,allow_pickle=False) as data:
        current,reference=data['current'].copy(),data['reference'].copy()
        spec=dict(P.DEFAULTS,**json.loads(str(data['options'])))
        robot,item=str(data['robot_id']),str(data['item'])
    if (current.shape!=reference.shape or current.dtype!=np.uint8 or current.ndim!=3
            or reference.dtype!=np.uint8 or current.shape[2]!=3 or min(current.shape[:2])<12 or max(current.shape[:2])>160):
        raise ValueError('진단 영상 형식 오류')
    P.validate(dict(device_id=robot,hardware=dict(cameras={spec['camera']:dict(width=10000,height=10000)}),pallet_stock=spec))
    result=dict(file=str(path),robot_id=robot,item=item,repeats=repeats)
    for mode,enabled in (('legacy',False),('precision',True)):
        options=dict(spec,precision_enabled=enabled);timings=[]
        for _ in range(repeats):
            start=time.perf_counter();row=P.compare(current,reference,options);timings.append((time.perf_counter()-start)*1000)
        result[mode]=dict(row,median_processing_ms=statistics.median(timings))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files',type=Path,nargs='+');parser.add_argument('--repeat',type=int,choices=range(1,11),default=5)
    args=parser.parse_args()
    for path in args.files:
        print(json.dumps(replay(path,args.repeat),ensure_ascii=False))


if __name__=='__main__':main()
