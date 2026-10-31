"""Finish rollout resources even if strategy construction/setup/teardown fails."""
import logging

logger = logging.getLogger(__name__)


def finish(ctx, strategy, *,original_error=False):
    from .hardware import cleanup_resources
    if ctx is None:
        return
    failure = None
    if strategy is not None:
        try:
            strategy.teardown(ctx)
            return
        except BaseException as exc:
            failure = exc
            logger.exception('Strategy teardown failed; completing hardware cleanup')
    hw = ctx.hardware
    for item in cleanup_resources(ctx.policy.inference, hw.robot_wrapper.inner):
        logger.warning('Fallback cleanup failed: %s', item)
    if hw.teleop is not None:
        try:
            hw.teleop.disconnect()
        except Exception:
            logger.exception('Fallback teleoperator cleanup failed')
    if failure is not None and not original_error:
        raise failure
