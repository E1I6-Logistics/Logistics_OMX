"""Broker connection handling never opens robot, cameras or models."""
import ast
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from omx_manager import mqtt_transport as T


def test_initial_broker_loss_uses_async_retry_instead_of_process_exit():
    client = Mock()
    T.start(client, '192.168.0.16', 1883)
    client.connect.assert_not_called()
    client.connect_async.assert_called_once_with('192.168.0.16', 1883, 60)
    client.reconnect_delay_set.assert_called_once_with(min_delay=1, max_delay=5)
    client.max_queued_messages_set.assert_called_once_with(128)
    client.loop_start.assert_called_once()


def test_network_thread_is_joined_even_if_disconnect_raises():
    client = Mock()
    client.disconnect.side_effect = OSError('socket unavailable')
    with pytest.raises(OSError):
        T.stop(client)
    client.loop_stop.assert_called_once()


@pytest.mark.parametrize('topic,qos', [('omx1/status',0),('omx2/ack',1),('omx3/progress',1),('omx4/result',1)])
def test_terminal_and_progress_messages_queue_across_short_disconnect(topic,qos):
    path=Path(__file__).resolve().parents[1]/'lerobot_unified/omx_mqtt_client.py'
    tree=ast.parse(path.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='publish')
    client=Mock();env={'client':client,'json':json}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),env)
    env['publish'](topic,{'job_id':'one','success':True})
    assert client.publish.call_args.kwargs==dict(qos=qos,retain=False)
