"""Fence camera reconnects until the previous native reader has really stopped."""


def require_stopped(camera):
    thread = camera.thread
    if thread is not None and thread.is_alive():
        raise RuntimeError(f'{camera}: previous camera reader still alive; restart process before reconnecting')
    camera.thread = None
    camera.stop_event = None


def retain_until_stopped(camera):
    # Never lose the only reference to a blocked native reader. Its stop event
    # must remain set, including when native release fails or times out.
    if camera.thread is None or not camera.thread.is_alive():
        camera.thread = None
        camera.stop_event = None
