"""Model-return regions and one-shot motion-cycle counting; no transfer claims."""
import math
from statistics import median

from .config import ConfigError, JOINTS, number, only_keys
from .home import joints
from .workflow import registration_binding

ARM=JOINTS[:-1]
PRIMARY=JOINTS[:3]
DEFAULTS=dict(mode='cycle',profiles={},departure_hold_s=.3,min_actions=30,return_hold_s=.5,
              min_training_cycles=3,tolerance_padding=1.5,min_return_tolerance=4.,max_return_tolerance=8.,
              calibration_hold_s=.6)


def options(cfg): return dict(DEFAULTS,**cfg['counting'])


def validate_cycles(cfg):
    spec=options(cfg)
    if spec['mode'] not in ('cycle','ordered_stages'): raise ConfigError('Unknown counting.mode')
    for key,lo,hi in (('departure_hold_s',.1,5),('return_hold_s',.2,10),('tolerance_padding',.1,5),
                      ('min_return_tolerance',.1,5),('max_return_tolerance',1,10),('calibration_hold_s',.3,5)):
        number(spec[key],'counting.'+key,lo,hi)
    if spec['min_return_tolerance']>spec['max_return_tolerance']: raise ConfigError('Invalid return tolerance range')
    for key,lo,hi in (('min_actions',10,1000),('min_training_cycles',3,10)):
        if type(spec[key]) is not int or not lo<=spec[key]<=hi: raise ConfigError('Invalid counting.'+key)
    profiles=spec['profiles']
    if not isinstance(profiles,dict) or set(profiles)-set(cfg['models']['profiles'][cfg['models']['active_profile']]['items']):
        raise ConfigError('Cycle profiles must use external item names')
    for item,row in profiles.items():
        if not isinstance(row,dict): raise ConfigError('Invalid cycle profile '+item)
        only_keys(row,'binding center tolerance leave_tolerance confirmed_cycles min_excursion min_cycle_s source','cycle profile')
        joints(row.get('center',{}))
        if set(row['center'])!=set(JOINTS) or set(row.get('tolerance',{}))!=set(ARM):
            raise ConfigError('Cycle center needs six joints; count tolerance needs five arm joints')
        for joint,tolerance in row['tolerance'].items(): number(tolerance,'return tolerance '+joint,.1,10)
        number(row.get('leave_tolerance'),'cycle leave tolerance',1,100)
        number(row.get('min_excursion'),'recorded excursion',1,200)
        number(row.get('min_cycle_s',spec['min_cycle_s']),'learned minimum cycle duration',1,120)
        if type(row.get('confirmed_cycles')) is not int or not 3<=row['confirmed_cycles']<=100:
            raise ConfigError('At least three operator-confirmed motion cycles required')


def require_profile(cfg,item):
    row=options(cfg)['profiles'].get(item)
    if not row or row.get('binding')!=registration_binding(cfg):
        raise ConfigError(f'{item}: model return region missing/changed; run omx_cycle_calibrate {item}')
    if row['confirmed_cycles']<options(cfg)['min_training_cycles']:
        raise ConfigError(f'{item}: not enough confirmed return cycles')
    if row['leave_tolerance']<=max(row['tolerance'][j] for j in PRIMARY):
        raise ConfigError('Cycle departure must be outside learned return region')
    if cfg['home']['target'] is None or any(abs(row['center'][j]-cfg['home']['target'][j])>cfg['home']['max_start_error'] for j in ARM):
        raise ConfigError('Learned return is outside registered Home approach; recheck calibration')
    return row


def learn_profile(cfg,item,captures):
    spec=options(cfg)
    if len(captures)<spec['min_training_cycles']: raise ConfigError('More confirmed cycles required')
    samples=[]
    for row in captures:
        if row.get('confirmed') is not True or row.get('actions',0)<spec['min_actions'] or row.get('duration_s',0)<1.:
            raise ConfigError('Each sample must be a confirmed, sufficiently long model motion cycle')
        tail=[joints(q) for q in row.get('return_samples',[])]
        if len(tail)<3: raise ConfigError('Fresh stable return observations required')
        if any(max(q[j] for q in tail)-min(q[j] for q in tail)>cfg['home']['capture_max_span'] for j in ARM):
            raise ConfigError('Confirmed return observations were not stable')
        for q in tail:
            if any(abs(q[j]-cfg['home']['target'][j])>cfg['home']['max_start_error'] for j in ARM):
                raise ConfigError('Refusing to learn a far/stretched pose as Home')
            if any(abs(q[j]-cfg['home']['target'][j])>cfg['home']['tolerance'][j] for j in PRIMARY):
                raise ConfigError('Confirmed return must bring shoulder/elbow near original Home')
        samples.extend(tail)
    center={j:median(q[j] for q in samples) for j in JOINTS}
    tolerance={j:max(spec['min_return_tolerance'],max(abs(q[j]-center[j]) for q in samples)+spec['tolerance_padding']) for j in ARM}
    if max(tolerance.values())>spec['max_return_tolerance']:
        raise ConfigError('Return examples vary too much; inspect model/observations rather than widening Home automatically')
    leave=max(spec['leave_tolerance'],max(tolerance[j] for j in PRIMARY)+2.)
    excursions=[]
    for row in captures:
        path=[joints(q) for q in row.get('trajectory',[])]
        peak=max((max(abs(q[j]-center[j]) for j in PRIMARY) for q in path),default=0)
        if peak<leave: raise ConfigError('No meaningful shoulder/elbow departure in a confirmed cycle')
        excursions.append(peak)
    return dict(binding=registration_binding(cfg),center=center,tolerance=tolerance,leave_tolerance=leave,
                confirmed_cycles=len(captures),min_excursion=min(excursions),
                min_cycle_s=max(1.,min(spec['min_cycle_s'],min(c['duration_s'] for c in captures)*.5)),
                source='operator_confirmed_model_rollout')


class CycleDetector:
    """Sends + primary-joint departure + real continuous return hold; no pose stages."""
    def __init__(self,cfg,item,now=0.):
        self.cfg=cfg;self.spec=options(cfg);self.profile=require_profile(cfg,item)
        self.target=self.profile['center'];self.hold=self.spec['return_hold_s']
        self.reset(now)

    def reset(self,now):
        self.started=now;self.departed=False;self.previous=None;self.last=None;self.since=None
        self.departure_since=None;self.rows={};self.still=False;self.stalled=False
        self.last_gap_s=None;self.gap_reset=False;self.actions=0;self.peak=0.;self.phase='WAIT_DEPARTURE'

    def note_action(self): self.actions+=1

    @property
    def in_home_window(self): return self.departed and bool(self.rows) and all(self.rows[j]['ok'] and self.rows[j]['approved_approach_ok'] for j in ARM)

    def invalidate(self,now):
        self.previous=None;self.last=None;self.since=None;self.departure_since=None
        self.gap_reset=True;self.still=False

    def update(self,observation,now):
        try:q=joints(observation)
        except (ValueError,TypeError): self.invalidate(now);raise
        number(now,'cycle timestamp',0)
        self.last_gap_s=None if self.last is None else now-self.last
        self.gap_reset=self.last is None or not 0<now-self.last<=self.cfg['home']['max_sample_gap_s']
        if self.gap_reset:
            self.previous=None;self.since=None;self.departure_since=None
        self.last=now
        self.rows={j:dict(target=self.target[j],current=q[j],error=abs(q[j]-self.target[j]),
                          tolerance=self.profile['tolerance'].get(j,self.cfg['home']['tolerance'][j]),
                          approved_approach_ok=j=='gripper' or abs(q[j]-self.cfg['home']['target'][j])<=self.cfg['home']['max_start_error'],
                          ignored=j=='gripper',ok=j=='gripper' or abs(q[j]-self.target[j])<=self.profile['tolerance'][j]) for j in JOINTS}
        excursion=max(abs(q[j]-self.target[j]) for j in PRIMARY)
        self.peak=max(self.peak,excursion)
        if excursion>=self.profile['leave_tolerance']:
            if self.departure_since is None:self.departure_since=now
            if now-self.departure_since>=self.spec['departure_hold_s']:self.departed=True
        else:self.departure_since=None
        self.still=self.previous is not None and max(abs(q[j]-self.previous[j]) for j in ARM)<=self.cfg['home']['still_delta']
        eligible=self.departed and self.actions>=self.spec['min_actions'] and now-self.started>=self.profile.get('min_cycle_s',self.spec['min_cycle_s'])
        if eligible and self.in_home_window and self.still:
            if self.since is None:self.since=now
            self.phase='RETURN_HOLD'
        else:
            self.since=None
            self.phase='OUTSIDE_HOME_APPROACH' if self.departed and any(not self.rows[j]['approved_approach_ok'] for j in ARM) else 'WORKING' if self.departed else 'WAIT_DEPARTURE'
        self.previous=q
        if self.since is not None and now-self.since>=self.hold:
            self.last_completion=dict(departed=True,still=True,in_home_window=True,hold_since=self.since,
                sample_gap_s=self.last_gap_s,gap_reset=self.gap_reset,actions=self.actions,peak_excursion=self.peak,phase='CYCLE_COMPLETED')
            self.reset(now)
            return True
        return False
