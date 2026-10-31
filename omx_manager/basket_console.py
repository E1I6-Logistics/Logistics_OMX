"""Compact watch output; full evidence stays available as JSON."""
import json
import time


def reason_text(row):
    reason=row.get('reason','UNKNOWN')
    if reason in ('STABILIZING','TEMPORAL_WARMUP','WAIT_INTERVAL'):
        return '확인 중'
    if reason=='LIGHTING_SHIFT':
        value=row.get('illumination_delta')
        return '조명 차이'+(f' {value:.0f}' if isinstance(value,(int,float)) else '')
    if reason in ('BASKET_POSE_NOT_FOUND','BASKET_CLIPPED','MULTIPLE_BASKET_CANDIDATES') or reason.startswith('PARTIAL_'):
        return '바구니 위치·잘림 확인'
    if reason in ('STALE_FRAME','NO_FRESH_BASKET_SNAPSHOT','CAMERA_RECONNECTING'):
        return '새 영상 대기'
    if reason in ('ROI_OVEREXPOSED','ROI_UNDEREXPOSED'):
        return '너무 밝음' if reason=='ROI_OVEREXPOSED' else '너무 어두움'
    if reason in ('REGISTERED_FAINT_CHANGE','REGISTERED_WEAK_CHANGE'):
        return '물건 여부 불명확'
    return reason


class WatchOutput:
    def __init__(self,device,*,json_output=False,clock=time.perf_counter,emit=None,save=None):
        self.device=device;self.json_output=json_output;self.clock=clock
        self.emit=emit or (lambda line:print(line,flush=True));self.save=save
        self.last=None;self.next_print=0.;self.next_save=0.;self.save_warned=False

    def update(self,row):
        now=self.clock()
        if self.save is not None and now>=self.next_save:
            self.next_save=now+1.
            try:self.save(row)
            except OSError as exc:
                if not self.save_warned:self.emit('[BASKET] 진단 저장 실패: '+str(exc));self.save_warned=True
        verdict=row.get('verdict','UNKNOWN');reason=reason_text(row)
        # Confirmation internals may alternate every frame; only meaningful
        # status changes interrupt the five-second heartbeat in compact mode.
        tag=(verdict,row.get('candidate'),row.get('reason')) if self.json_output else (verdict,reason.split(' ')[0] if verdict=='UNKNOWN' else '')
        if tag==self.last and now<self.next_print:return False
        self.last=tag;self.next_print=now+(1. if self.json_output else 5.)
        if self.json_output:self.emit(json.dumps(row,ensure_ascii=False));return True
        label={'EMPTY':'비었음','OCCUPIED':'물건 있음'}.get(verdict,'보류')
        detail=(' | '+reason) if verdict=='UNKNOWN' else ''
        if verdict=='OCCUPIED' and row.get('evidence_reason')=='REGISTERED_COLORED_OBJECT':detail=' | 색상 근거'
        def number(name):
            value=row.get(name)
            return f'{value:.1f}ms' if isinstance(value,(int,float)) else '-'
        stamp=time.strftime('%H:%M:%S',time.localtime(row.get('at',time.time())))
        self.emit(f'[{self.device} {stamp}] {label}{detail} | 처리 {number("processing_ms")} | 영상 나이 {number("frame_age_ms")}')
        return True
