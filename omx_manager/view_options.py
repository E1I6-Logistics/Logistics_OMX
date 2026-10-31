"""Optional LAN viewer settings; no camera or network side effects."""
import ipaddress

DEFAULTS = dict(camera='auto', bind='0.0.0.0', port=8080, allow_ip='192.168.0.19',
                fps=1., max_width=640, jpeg_quality=70, max_age_s=2.)


def options(cfg):
    return dict(DEFAULTS, **cfg.get('view', {}))


def camera_name(cfg):
    value=options(cfg)['camera']
    if value=='auto':
        value=next((name for name in ('top','front') if name in cfg['hardware']['cameras']),None)
    if value not in cfg['hardware']['cameras']:
        from .config import ConfigError
        raise ConfigError('view.camera must select an existing camera (auto: top, then front)')
    return value


def validate(cfg):
    from .config import ConfigError, number, only_keys
    if not isinstance(cfg.get('view',{}),dict):raise ConfigError('view must be a mapping')
    only_keys(cfg.get('view',{}),' '.join(DEFAULTS),'view')
    spec=options(cfg)
    for key in ('bind','allow_ip'):
        try:ipaddress.IPv4Address(spec[key])
        except (ipaddress.AddressValueError,ValueError,TypeError):raise ConfigError('view.'+key+' must be an IPv4 address')
    for key,lo,hi in (('port',1024,65535),('max_width',160,1280),('jpeg_quality',30,90)):
        if type(spec[key]) is not int or not lo<=spec[key]<=hi:raise ConfigError('Invalid view.'+key)
    number(spec['fps'],'view.fps',.2,2)
    number(spec['max_age_s'],'view.max_age_s',.5,5)
    camera_name(cfg)
