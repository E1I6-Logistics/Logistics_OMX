"""Apply the configured CPU parallelism before model loading."""
import logging
import os
import sys

_threads=None


def current_threads():
    module=sys.modules.get('torch')
    if module is not None and hasattr(module,'get_num_threads') and hasattr(module,'get_num_interop_threads'):
        return dict(intra_op=module.get_num_threads(),inter_op=module.get_num_interop_threads())
    return _threads


def configure_cpu(torch_module=None):
    global _threads
    if not os.environ.get('OMX_MANAGER_CONFIG'):return None
    if torch_module is None:
        import torch as torch_module
    threads=int(os.environ.get('OMP_NUM_THREADS','4'))
    if not 1<=threads<=4:raise ValueError('OMX CPU threads must be 1..4')
    torch_module.set_num_threads(threads)
    try:torch_module.set_num_interop_threads(1)
    except RuntimeError:pass # another context may already have initialized this pool
    result=dict(intra_op=torch_module.get_num_threads(),inter_op=torch_module.get_num_interop_threads())
    _threads=result
    logging.getLogger(__name__).info('[CPU] PyTorch threads: intra=%s inter=%s (100%% utilization is not guaranteed)',result['intra_op'],result['inter_op'])
    return result
